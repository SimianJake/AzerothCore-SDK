"""Local test realm harness: start worldserver from the build tree, send console commands, stop cleanly.

ACSDK_BIN is the folder with the worldserver executable. It defaults to the Windows layout in mod-acsdk's
docs/windows-build.md, or on Linux/macOS to ~/azeroth-server/bin (AzerothCore's usual install prefix).
ACSDK_MODULE_CONF overrides where mod_acsdk.conf is (default: configs/modules next to the exe on Windows,
../etc/modules on Linux/macOS).
worldserver shuts down when its console stdin reaches EOF, so stdin stays an open pipe for the whole run;
CTRL_BREAK (Windows, new process group) or SIGINT (elsewhere) triggers a normal shutdown.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

WINDOWS = os.name == "nt"
_DEFAULT_BIN = r"C:\acsdk-build\ac\bin\RelWithDebInfo" if WINDOWS else str(Path.home() / "azeroth-server" / "bin")
BIN = Path(os.environ.get("ACSDK_BIN", _DEFAULT_BIN))
EXE = BIN / ("worldserver.exe" if WINDOWS else "worldserver")
LOG = BIN / "Server.log"
_DEFAULT_CONF = BIN / "configs" / "modules" if WINDOWS else BIN.parent / "etc" / "modules"
MOD_CONF = Path(os.environ.get("ACSDK_MODULE_CONF", _DEFAULT_CONF / "mod_acsdk.conf"))


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
            [str(EXE)],
            cwd=BIN,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
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
            os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode
