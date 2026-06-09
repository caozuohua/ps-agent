import asyncio
import os
import signal
import uuid
from dataclasses import dataclass


@dataclass
class ShellResult:
    mode: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class ShellTool:
    def __init__(
        self,
        *,
        docker_image: str = "alpine:latest",
        sandbox_network: str = "none",
        default_timeout: int = 10,
        max_timeout: int = 120,
    ):
        self.docker_image = docker_image
        self.sandbox_network = sandbox_network
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout

    def clamp_timeout(self, timeout: int | None) -> int:
        if timeout is None:
            timeout = self.default_timeout
        timeout = max(1, int(timeout))
        timeout = min(timeout, self.max_timeout)
        return timeout

    async def run(
        self,
        *,
        command: str,
        mode: str = "sandbox",
        timeout: int | None = None,
    ) -> ShellResult:
        timeout = self.clamp_timeout(timeout)

        if mode == "sandbox":
            return await self.run_sandbox(command=command, timeout=timeout)

        if mode == "host":
            return await self.run_host(command=command, timeout=timeout)

        raise ValueError(f"unsupported shell mode: {mode}")

    async def run_sandbox(self, *, command: str, timeout: int) -> ShellResult:
        container_name = f"ps-agent-{uuid.uuid4().hex[:12]}"

        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.sandbox_network,
            self.docker_image,
            "sh",
            "-lc",
            command,
        ]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return ShellResult(
                mode="sandbox",
                command=command,
                returncode=proc.returncode,
                stdout=stdout_b.decode(errors="replace"),
                stderr=stderr_b.decode(errors="replace"),
                timed_out=False,
            )

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

            # 尝试强制清理超时容器
            cleanup = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await cleanup.communicate()

            return ShellResult(
                mode="sandbox",
                command=command,
                returncode=124,
                stdout="",
                stderr=f"command timed out after {timeout}s",
                timed_out=True,
            )

    async def run_host(self, *, command: str, timeout: int) -> ShellResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return ShellResult(
                mode="host",
                command=command,
                returncode=proc.returncode,
                stdout=stdout_b.decode(errors="replace"),
                stderr=stderr_b.decode(errors="replace"),
                timed_out=False,
            )

        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

            return ShellResult(
                mode="host",
                command=command,
                returncode=124,
                stdout="",
                stderr=f"command timed out after {timeout}s",
                timed_out=True,
            )
