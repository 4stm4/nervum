"""SSH-исполнитель команд в QEMU-госте для DP E2E тестов."""

from __future__ import annotations

import subprocess


class SshExecutor:
    """Тонкая обёртка над CLI ssh для выполнения команд в QEMU-госте."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10022,
        user: str = "root",
        timeout: int = 60,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.timeout = timeout
        self._ssh_opts: list[str] = [
            "ssh",
            "-p", str(port),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
            f"{user}@{host}",
        ]

    def run(self, cmd: str) -> tuple[int, str, str]:
        """Выполняет команду в госте, возвращает (returncode, stdout, stderr)."""
        result = subprocess.run(
            self._ssh_opts + [cmd],
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        return result.returncode, result.stdout, result.stderr

    def __call__(self, cmd: str) -> str:
        """Выполняет команду, бросает AssertionError при ненулевом коде возврата."""
        rc, stdout, stderr = self.run(cmd)
        assert rc == 0, (
            f"SSH command failed (rc={rc}):\n"
            f"  cmd: {cmd!r}\n"
            f"  stderr: {stderr.strip()}\n"
            f"  stdout: {stdout.strip()}"
        )
        return stdout

    def check(self, cmd: str) -> bool:
        """Возвращает True, если команда завершилась с кодом 0."""
        rc, _, _ = self.run(cmd)
        return rc == 0

    def ping_from_ns(self, ns: str, dst_ip: str, *, count: int = 3, wait: int = 2) -> bool:
        """Запускает ping из сетевого пространства имён, возвращает True при успехе."""
        rc, _, _ = self.run(
            f"ip netns exec {ns} ping -c {count} -W {wait} -q {dst_ip}"
        )
        return rc == 0
