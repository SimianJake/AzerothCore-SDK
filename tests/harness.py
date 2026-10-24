"""Local test realm harness: start worldserver from the build tree, send console commands, stop cleanly.

Paths default to the layout in mod-acsdk's docs/windows-build.md; override with ACSDK_BIN (folder with worldserver.exe).
worldserver shuts down when its console stdin reaches EOF, so stdin stays an open pipe for the whole run;
CTRL_BREAK (new process group) triggers a normal shutdown.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

BIN = Path(os.environ.get("ACSDK_BIN", r"C:\acsdk-build\ac\bin\RelWithDebInfo"))
LOG = BIN / "Server.log"
MOD_CONF = BIN / "configs" / "modules" / "mod_acsdk.conf"


def read_token() -> str:
    m = re.search(r'^ACSDK.AuthToken\s*=\s*"([^"]+)"', MOD_CONF.read_text(), re.MULTILINE)
    if not m:
        raise RuntimeError(f"ACSDK.AuthToken not set in {MOD_CONF}")
    return m.group(1)


def set_module_option(key: str, value: str) -> None:
    """Set (or append) `key = value` in mod_acsdk.conf. Takes effect at the next worldserver start."""
    text = MOD_CONF.read_text()
    line = f"{key} = {value}"
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    text = pattern.sub(line, text) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
    MOD_CONF.write_text(text)


class Realm:
    def __init__(self) -> None:
        self.proc: subprocess.Popen[str] | None = None
        self.output: list[str] = []
        self._lock = threading.Lock()

    def start(self, timeout: float = 240.0) -> None:
        LOG.unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            [str(BIN / "worldserver.exe")],
            cwd=BIN,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"worldserver exited during startup ({self.proc.returncode})")
            if self.wait_output("gRPC listener started", timeout=1):
                return
        raise TimeoutError("worldserver did not start the gRPC listener")

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            with self._lock:
                self.output.append(line.rstrip("\n"))

    def mark(self) -> int:
        with self._lock:
            return len(self.output)

    def wait_output(self, needle: str, since: int = 0, timeout: float = 10.0) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                for line in self.output[since:]:
                    if needle in line:
                        return line
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def console(self, command: str, settle: float = 1.0) -> list[str]:
        """Run a worldserver console (GM) command, e.g. "tele name Examplechar Goldshire". Returns new output."""
        assert self.proc and self.proc.stdin
        start = self.mark()
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        time.sleep(settle)
        with self._lock:
            return self.output[start:]

    def stop(self, timeout: float = 180.0) -> int:
        assert self.proc
        if self.proc.poll() is None:
            os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode
