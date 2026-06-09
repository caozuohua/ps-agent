import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from memory import Memory
from tools.shell import ShellTool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ShellTask:
    task_id: str
    open_id: str
    chat_id: str
    command: str
    mode: str
    timeout: int


class TaskQueue:
    def __init__(
        self,
        *,
        memory: Memory,
        shell_tool: ShellTool,
        send_text_func,
    ):
        self.memory = memory
        self.shell_tool = shell_tool
        self.send_text_func = send_text_func

        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[ShellTask] | None = None
        self.thread: threading.Thread | None = None

    def start_background(self):
        if self.thread:
            return

        self.thread = threading.Thread(
            target=self._thread_main,
            name="ps-agent-task-queue",
            daemon=True,
        )
        self.thread.start()

    def _thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.queue = asyncio.Queue()
        self.loop.create_task(self._worker())
        self.loop.run_forever()

    def submit(self, task: ShellTask):
        if not self.loop or not self.queue:
            raise RuntimeError("task queue not started")

        asyncio.run_coroutine_threadsafe(
            self.queue.put(task),
            self.loop,
        )

    async def _worker(self):
        logging.info("task queue worker started")

        while True:
            task = await self.queue.get()

            try:
                await self._execute_shell_task(task)
            except Exception as e:
                logging.exception("task execution failed: %s", e)

                self.memory.update_task_status(
                    task_id=task.task_id,
                    status="failed",
                    summary=str(e),
                    mark_finished=True,
                )
                self.memory.audit(
                    event_type="task_worker_error",
                    event_level="error",
                    open_id=task.open_id,
                    task_id=task.task_id,
                    content={"error": str(e)},
                )
                await asyncio.to_thread(
                    self.send_text_func,
                    task.chat_id,
                    f"任务执行异常：{task.task_id}\n\n{e}",
                    task.open_id,
                    task.task_id,
                )
            finally:
                self.queue.task_done()

    async def _execute_shell_task(self, task: ShellTask):
        self.memory.update_task_status(
            task_id=task.task_id,
            status="running",
            mark_started=True,
        )

        self.memory.audit(
            event_type="task_started",
            event_level="info",
            open_id=task.open_id,
            task_id=task.task_id,
            content={
                "command": task.command,
                "mode": task.mode,
                "timeout": task.timeout,
            },
        )

        await asyncio.to_thread(
            self.send_text_func,
            task.chat_id,
            f"开始执行任务：{task.task_id}\n模式：{task.mode}\n命令：\n{task.command}",
            task.open_id,
            task.task_id,
        )

        started_at = now_iso()

        result = await self.shell_tool.run(
            command=task.command,
            mode=task.mode,
            timeout=task.timeout,
        )

        finished_at = now_iso()

        status = "completed" if result.returncode == 0 else "failed"
        if result.timed_out:
            status = "timeout"

        self.memory.add_task_step(
            task_id=task.task_id,
            step_id="shell_1",
            step_type="shell",
            command=task.command,
            mode=task.mode,
            status=status,
            attempt=1,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            error="" if result.returncode == 0 else result.stderr,
            started_at=started_at,
            finished_at=finished_at,
        )

        summary = f"returncode={result.returncode}"
        if result.timed_out:
            summary = "command timed out"

        self.memory.update_task_status(
            task_id=task.task_id,
            status=status,
            summary=summary,
            mark_finished=True,
        )

        self.memory.audit(
            event_type="command_executed",
            event_level="info" if status == "completed" else "error",
            open_id=task.open_id,
            task_id=task.task_id,
            step_id="shell_1",
            content={
                "command": task.command,
                "mode": task.mode,
                "timeout": task.timeout,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
                "status": status,
            },
        )

        output = self._format_result(task, result, status)

        await asyncio.to_thread(
            self.send_text_func,
            task.chat_id,
            output,
            task.open_id,
            task.task_id,
        )

    def _format_result(self, task: ShellTask, result, status: str) -> str:
        parts = [
            f"任务完成：{task.task_id}",
            f"状态：{status}",
            f"模式：{task.mode}",
            f"返回码：{result.returncode}",
            "",
            "命令：",
            task.command,
            "",
        ]

        if result.stdout:
            parts.extend([
                "STDOUT:",
                result.stdout,
                "",
            ])

        if result.stderr:
            parts.extend([
                "STDERR:",
                result.stderr,
                "",
            ])

        if not result.stdout and not result.stderr:
            parts.append("无输出。")

        return "\n".join(parts)
