import json
import logging
import os
import signal
import sys
import traceback

import lark_oapi as lark
from lark_oapi.adapter.websocket import client as ws_client

from config import load_config
from memory import Memory
from lark_client import LarkClient

import uuid
from datetime import datetime, timezone

from task_queue import TaskQueue, ShellTask
from tools.shell import ShellTool
from security.blacklist import CommandBlacklist


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def parse_lark_text(content: str) -> str:
    """
    Lark 文本消息 content 通常是 JSON 字符串：
    {"text": "hello"}
    """
    try:
        data = json.loads(content)
        return data.get("text", "")
    except Exception:
        return content or ""


class AgentApp:
    def __init__(self):
        self.config = load_config()
        setup_logging(self.config.log_level)

        os.makedirs(self.config.data_dir, exist_ok=True)

        self.memory = Memory(self.config.sqlite_path)
        self.lark = LarkClient(
            self.config.lark_app_id,
            self.config.lark_app_secret,
            self.config.lark_text_chunk_size,
        )
        
        self.blacklist = CommandBlacklist()

        self.shell_tool = ShellTool(
            docker_image=self.config.docker_image,
            sandbox_network=self.config.sandbox_network,
            default_timeout=self.config.default_shell_timeout,
            max_timeout=self.config.max_shell_timeout,
        )

        self.task_queue = TaskQueue(
            memory=self.memory,
            shell_tool=self.shell_tool,
            send_text_func=self.reply_from_worker,
        )


    def is_bound_user(self, open_id: str) -> bool:
        bind = self.config.bind_open_id.strip()
        if not bind:
            return False
        return open_id == bind

    def handle_text_command(self, *, open_id: str, chat_id: str, message_id: str, text: str):
        text = text.strip()

        self.memory.audit(
            event_type="command_received",
            event_level="info",
            open_id=open_id,
            content={
                "message_id": message_id,
                "chat_id": chat_id,
                "text": text,
            },
        )

        # 未绑定时，只允许 /id，方便首次部署者拿到自己的 open_id
        if not self.config.bind_open_id.strip():
            if text == "/id":
                reply = (
                    "当前未绑定运维用户。\n\n"
                    f"你的 open_id 是：\n{open_id}\n\n"
                    "请将它写入 .env：\n"
                    f"BIND_OPEN_ID={open_id}\n\n"
                    "然后重启服务。"
                )
                self.reply(chat_id, reply, open_id=open_id, message_id=message_id)
                return

            self.memory.audit(
                event_type="auth_denied_unbound",
                event_level="warning",
                open_id=open_id,
                content={"reason": "BIND_OPEN_ID is empty", "text": text},
            )
            self.reply(
                chat_id,
                "当前尚未绑定运维用户。请发送 /id 获取 open_id 后配置 BIND_OPEN_ID。",
                open_id=open_id,
                message_id=message_id,
            )
            return

        # 已绑定时，只接受绑定用户
        if not self.is_bound_user(open_id):
            self.memory.audit(
                event_type="auth_denied",
                event_level="warning",
                open_id=open_id,
                content={
                    "reason": "open_id not matched",
                    "text": text,
                },
            )
            # 为了安全，非绑定用户可以选择静默。
            # 这里 Phase 1 先回复无权限，方便调试。
            self.reply(chat_id, "无权限。", open_id=open_id, message_id=message_id)
            return

        self.memory.audit(
            event_type="auth_passed",
            event_level="info",
            open_id=open_id,
            content={"text": text},
        )

        # 二次确认 / 取消
        if self.handle_confirm_or_cancel(open_id=open_id, chat_id=chat_id, text=text):
            return

        # Shell 命令
        mode, command = self.parse_shell_command(text)
        if mode:
            self.create_shell_task(
                open_id=open_id,
                chat_id=chat_id,
                raw_text=text,
                command=command,
                mode=mode,
            )
            return

        if text == "/ping":
            self.reply(chat_id, "pong", open_id=open_id, message_id=message_id)
            return

        if text == "/id":
            self.reply(chat_id, f"你的 open_id：\n{open_id}", open_id=open_id, message_id=message_id)
            return

        if text == "/help":
            self.reply(
                chat_id,
                (
                    "PS 运维智能体 Phase 2\n\n"
                    "可用命令：\n"
                    "/ping - 连通性测试\n"
                    "/id - 查看当前 Lark open_id\n"
                    "/help - 查看帮助\n\n"
                    "Shell：\n"
                    "/shell echo hello - sandbox 模式执行\n"
                    "/shell --mode=sandbox echo hello\n"
                    "/host df -h - host 模式执行，需要确认\n"
                    "/shell --mode=host df -h\n\n"
                    "二次确认：\n"
                    "确认 task_xxx\n"
                    "取消 task_xxx"
                ),
                open_id=open_id,
                message_id=message_id,
            )
            return


        self.reply(
            chat_id,
            (
                "已收到消息，但 Phase 1 暂只支持：\n"
                "/ping\n"
                "/id\n"
                "/help"
            ),
            open_id=open_id,
            message_id=message_id,
        )

    def reply(self, chat_id: str, text: str, *, open_id: str, message_id: str):
        self.lark.send_text(chat_id, text)

        self.memory.add_message(
            message_id=None,
            open_id=open_id,
            chat_id=chat_id,
            direction="out",
            content=text,
        )

        self.memory.audit(
            event_type="message_sent",
            event_level="info",
            open_id=open_id,
            content={
                "reply_to_message_id": message_id,
                "chat_id": chat_id,
                "text": text,
            },
        )

    def on_message(self, data: lark.EventDispatcherHandler):
        """
        实际事件对象由 lark-oapi 传入。
        """

    def start(self):
        logging.info("starting ps-agent phase 2")
        self.task_queue.start_background()
        logging.info("task queue started")

        event_handler = (
            lark.EventDispatcherHandler.builder(
                self.config.lark_verification_token,
                self.config.lark_encrypt_key,
            )
            .register_p2_im_message_receive_v1(self.handle_lark_message)
            .build()
        )

        cli = ws_client.Client(
            self.config.lark_app_id,
            self.config.lark_app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        logging.info("starting lark websocket client")
        cli.start()
        
    def reply_from_worker(self, chat_id: str, text: str, open_id: str, task_id: str):
        self.lark.send_text(chat_id, text)

        self.memory.add_message(
            message_id=None,
            open_id=open_id,
            chat_id=chat_id,
            direction="out",
            content=text,
        )

        self.memory.audit(
            event_type="message_sent",
            event_level="info",
            open_id=open_id,
            task_id=task_id,
            content={
                "chat_id": chat_id,
                "text": text,
            },
        )
        
    def new_task_id(self) -> str:
        return "task_" + uuid.uuid4hex[:12]

    def parse_shell_command(self, text: str):
        """
        支持：
        /shell echo hello
        /shell --mode=host df -h
        /host df -h
        """
        text = text.strip()

        if text("/host "):
            return "host", text[len("/host "):].strip()

        if text == "/host":
            return "host", ""

        if text.startswith("/shell "):
            rest = text[len("/shell "):].strip()

            if rest.startswith("--mode=host "):
                return "host", rest[len("--mode=host "):].strip()

            if rest.startswith("--mode=sandbox "):
                return "sandbox", rest[len("--mode=sandbox "):].strip()

            return "sandbox", rest

        if text == "/shell":
            return "sandbox", ""

        return None, None
        
    def create_shell_task(
        self,
        *,
        open_id: str,
        chat_id: str,
        raw_text: str,
        command: str,
        mode: str,
    ):
        command = command.strip()

        if not command:
            self.reply(
                chat_id,
                "命令为空。\n\n示例：\n/shell echo hello\n/host df -h",
                open_id=open_id,
                message_id="",
            )
            return

        if mode == "host" and not self.config.enable_host_mode:
            self.reply(
                chat_id,
                "host mode 当前未启用。",
                open_id=open_id,
                message_id="",
            )
            return

        timeout = self.config.default_shell_timeout

        task_id = self.new_task_id()

        # 黑名单检查
        if self.config.enable_command_blacklist:
            result = self.blacklist.check(command)
            if result.blocked:
                self.memory.create_shell_task(
                    task_id=task_id,
                    open_id=open_id,
                    chat_id=chat_id,
                    raw_text=raw_text,
                    command=command,
                    mode=mode,
                    timeout=timeout,
                    status="blocked",
                    requires_confirm=False,
                )

                self.memory.audit(
                    event_type="command_blocked",
                    event_level="critical",
                    open_id=open_id,
 task_id=task_id,
                    content={
                        "command": command,
                        "mode": mode,
                        "rule": result.rule,
                        "reason": result.reason,
                    },
                )

                self.reply(
                    chat_id,
                    (
                        f"命令已被安全策略拦截。\n\n"
 f"任务：{task_id}\n"
                        f"模式：{mode}\n"
                        f"命令：\n{command}\n\n"
                        f"命中规则：\n{result.rule}"
                    ),
                    open_id=open_id,
                    message_id="",
                )
                return

        requires_confirm = False

        if mode == "host" and self.config.host_mode_require_confirm:
            requires_confirm = True

        status = "waiting_confirm" if requires_confirm else "pending"

        self.memory.create_shell_task(
            task_id=task_id,
            open_id=open_id,
            chat_id=chat_id,
            raw_text=raw_text,
            command=command,
            mode=mode,
            timeout=timeout,
            status=status,
            requires_confirm=requires_confirm,
        )

        self.memory.audit(
            event_type="task_created",
            event_level="info",
            open_id=open_id,
            task_id=task_id,
            content={
                "command": command,
                "mode": mode,
                "timeout": timeout,
                "requires_confirm": requires_confirm,
                "status": status,
            },
        )

        if requires_confirm:
            self.reply(
                chat_id,
                (
                    f"任务已创建，等待二次确认。\n\n"
                    f"任务：{task_id}\n"
                    f"模式：{mode}\n"
                    f"命令：\n{command}\n\n"
                    f"确认执行请发送：\n确认 {task_id}\n\n"
                    f"取消任务请发送：\n取消 {task_id}"
                ),
                open_id=open_id,
                message_id="",
            )
            return

        self.task_queue.submit(
            ShellTask(
                task_id=task_id,
                open_id=open_id,
                chat_id=chat_id,
                command=command,
                mode=mode,
                timeout=timeout,
            )
        )

        self.reply(
            chat_id,
            f"任务已加入队列：{task_id}",
            open_id=open_id,
            message_id="",
        )

    def handle_confirm_or_cancel(self, *, open_id: str, chat_id: str, text: str) -> bool:
        text = text.strip()

        if text.startswith("确认 "):
            task_id = text[len("确认 "):].strip()
            self.confirm_task(open_id=open_id, chat_id=chat_id, task_id=task_id)
            return True

        if text.startswith("取消 "):
            task_id = text[len("取消 "):].strip()
            self.cancel_task(open_id=open_id, chat_id=chat_id, task_id=task_id)
            return True

        return False

    def confirm_task(self, *, open_id: str, chat_id: str, task_id: str):
        task = self.memory.get_task(task_id)

        if not task:
            self.reply(chat_id, f"任务不存在：{task_id}", open_id=open_id, message_id="")
            return

        if task["open_id"] != open_id:
            self.memory.audit(
                event_type="confirm_denied",
                event_level="warning",
                open_id=open_id,
                task_id=task_id,
                content={"reason": "task owner mismatch"},
            )
            self.reply(chat_id, "无权确认该任务。", open_id=open_id, message_id="")
            return

        if task["status"] != "waiting_confirm":
            self.reply(
                chat_id,
                f"任务当前状态为 {task['status']}，不能确认执行。",
                open_id=open_id,
                message_id="",
            )
            return

        self.memory.update_task_status(
            task_id=task_id,
            status="pending",
        )

        self.memory.audit(
            event_type="task_confirmed",
            event_level="info",
            open_id=open_id,
            task_id=task_id,
            content={
                "command": task["command"],
                "mode": task["mode"],
            },
        )

        self.task_queue.submit(
            ShellTask(
                task_id=task_id,
                open_id=task["open_id"],
                chat_id=task["chat_id"],
                command=task["command"],
                mode=task["mode"],
                timeout=task["timeout"],
            )
        )

        self.reply(
            chat_id,
            f"已确认，任务加入队列：{task_id}",
            open_id=open_id,
            message_id="",
        )

    def cancel_task(self, *, open_id: str, chat_id: str, task_id: str):
        task = self.memory.get_task(task_id)

        if not task:
            self.reply(chat_id, f"任务不存在：{task_id}", open_id=open_id, message_id="")
            return

        if task["open_id"] != open_id:
            self.memory.audit(
                event_type="cancel_denied",
                event_level="warning",
                open_id=open_id,
                task_id=task_id,
                content={"reason": "task owner mismatch"},
            )
            self.reply(chat_id, "无权取消该任务。", open_id=open_id, message_id="")
            return

        if task["status"] not in ("waiting_confirm", "pending"):
            self.reply(
                chat_id,
                f"任务当前状态为 {task['status']}，不能取消。",
                open_id=open_id,
                message_id="",
            )
            return

        self.memory.update_task_status(
            task_id=task_id,
            status="cancelled",
            summary="cancelled by user",
            mark_finished=True,
        )

        self.memory.audit(
            event_type="task_cancelled",
            event_level="info",
            open_id=open_id,
            task_id=task_id,
            content={"reason": "cancelled by user"},
        )

        self.reply(
            chat_id,
            f"已取消任务：{task_id}",
            open_id=open_id,
            message_id="",
        )

    
    def handle_lark_message(self, data):
        try:
            event = data.event

            sender = event.sender
            message = event.message

            open_id = sender.sender_id.open_id
            chat_id message.chat_id
            message_id = message.message_id
            message_type = message.message_type
            content = message.content

            logging.info(
                "received message: open_id=%s chat_id=%s message_id=%s type=%s",
                open_id,
                chat_id,
                message_id,
                message_type,
            )

            if message_type != "text":
                self.memory.audit(
                    event_type="message_ignored",
                    event_level="info",
                    open_id=open_id,
                    content={
                        "reason": "non-text message",
                        "message_type": message_type,
                        "message_id": message_id,
                    },
                )
                return

            text = parse_lark_text(content)

            self.memory.add_message(
                message_id=message_id,
                open_id=open_id,
                chat_id=chat_id,
                direction="in",
                content=text,
            )

            self.memory.audit(
                event_type="message_received",
                event_level="info",
                open_id=open_id,
                content={
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "message_type": message_type,
                    "text": text,
                },
            )

            self.handle_text_command(
                open_id=open_id,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
            )

        except Exception as e:
            logging.error("handle lark message failed: %s", e)
            logging.error(traceback.format_exc())

            try:
                self.memory.audit(
                    event_type="handler_error",
                    event_level="error",
                    content={
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
            except Exception:
                pass


def main():
    app = AgentApp()
    app.start()


if __name__ == "__main__":
    main()
