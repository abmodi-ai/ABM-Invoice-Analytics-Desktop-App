"""The engine stops when the desktop shell that launched it dies, however it dies."""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

REPO = Path(__file__).resolve().parents[2]

# Stand-in for the desktop shell: starts the engine with IA_PARENT_PID pointing at itself, prints
# the engine's pid, then waits to be killed.
SHELL = """
import os, subprocess, sys, time
env = {**os.environ, "IA_PARENT_PID": str(os.getpid())}
p = subprocess.Popen([sys.executable, "-m", "invoice_analytics"], env=env, cwd=sys.argv[1])
print(p.pid, flush=True)
time.sleep(600)
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX re-parenting; Windows relies on pid_exists")
def test_engine_exits_when_shell_is_killed_even_before_it_is_reaped(tmp_path: Path) -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {
        **os.environ,
        "IA_DATA_DIR": str(tmp_path),
        "IA_KEYSTORE": "file",
        "IA_PORT": str(port),
        "IA_TOKEN": secrets.token_hex(24),
        "IA_NO_WORKERS": "1",
    }
    shell = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", SHELL, str(REPO / "engine")], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        engine = psutil.Process(int(shell.stdout.readline()))  # type: ignore[union-attr]
        for _ in range(120):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise AssertionError("engine did not start")
        os.kill(shell.pid, signal.SIGKILL)  # deliberately not reaped: it stays a zombie meanwhile
        assert psutil.pid_exists(shell.pid)
        engine.wait(timeout=15)  # the watchdog notices within ~2 s
    finally:
        shell.kill()
        shell.wait()
