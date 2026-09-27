"""Engine entry point (the Tauri sidecar).

    IA_PORT=<port> IA_TOKEN=<token> invoice-analytics-engine

Also: --openapi <file> (write the API contract), --remove-all-data (uninstaller),
--demo (load synthetic data + sample reference data into the data dir).
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()  # PDF extraction runs in a spawned subprocess
    # A windowed (no console) frozen build on Windows has no stdout/stderr; writing to them would
    # crash the engine at startup.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))  # noqa: SIM115 - lives for the process
    ap = argparse.ArgumentParser(prog="invoice-analytics-engine")
    ap.add_argument("--openapi", type=Path, help="write the OpenAPI spec and exit")
    ap.add_argument("--remove-all-data", action="store_true", help="securely delete the database and keys")
    ap.add_argument("--demo", action="store_true", help="load synthetic demo data and sample reference data")
    ap.add_argument("--demo-invoices", type=int, default=1500)
    a = ap.parse_args(argv)

    from invoice_analytics.config import Config

    cfg = Config.from_env()
    if a.remove_all_data:
        from invoice_analytics.backup import secure_remove_all

        secure_remove_all(cfg.data_dir, cfg.keystore)
        print("all ABM Invoice Analytics data removed")
        return 0

    from invoice_analytics.context import open_engine
    from invoice_analytics.logging_setup import setup_logging

    setup_logging(cfg.log_dir)
    if a.openapi:
        cfg.token = cfg.token or "openapi"
        cfg.start_workers = False
        from invoice_analytics.api.app import create_app

        eng = open_engine(cfg)
        a.openapi.write_text(json.dumps(create_app(eng).openapi(), indent=1))
        eng.close()
        return 0
    eng = open_engine(cfg)
    if a.demo:
        from invoice_analytics.demo import load_demo

        print(json.dumps(load_demo(eng, a.demo_invoices), indent=1))
        eng.close()
        return 0
    if not cfg.token or len(cfg.token) < 32:
        print("IA_TOKEN (>=32 chars) is required", file=sys.stderr)
        return 2
    import uvicorn

    from invoice_analytics.api.app import create_app

    app = create_app(eng)
    port = cfg.port or 0
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", access_log=False, server_header=False, date_header=False
    )
    server = uvicorn.Server(config)
    if port == 0:  # pick a free port and report it on stdout for the supervisor
        import socket

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        config.port = port
    _watch_parent()
    print(json.dumps({"event": "listening", "port": port}), flush=True)
    logging.getLogger("invoice_analytics").info("engine listening on 127.0.0.1:%s", port)
    try:
        server.run()
    finally:
        eng.close()
    return 0


def _watch_parent() -> None:
    """Exit if the desktop shell that launched us goes away (crash, kill, or force-quit), so the
    engine never lingers holding the database open."""
    ppid = os.environ.get("IA_PARENT_PID")
    if not ppid:
        return
    import threading
    import time

    import psutil

    pid = int(ppid)

    first_parent = os.getppid()  # the shell itself, or `uv` in development

    def gone() -> bool:
        # POSIX re-parents us the moment our parent dies, even while it is still an unreaped zombie
        # that pid_exists() would report as alive.
        if os.name != "nt" and os.getppid() != first_parent:
            return True
        return not psutil.pid_exists(pid)

    def loop() -> None:
        while True:
            time.sleep(2)
            if gone():
                logging.getLogger("invoice_analytics").warning("desktop shell exited; engine stopping")
                os._exit(0)

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
