"""Engine entry point (the Tauri sidecar).

    VERISMO_PORT=<port> VERISMO_TOKEN=<token> verismo-engine

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
    ap = argparse.ArgumentParser(prog="verismo-engine")
    ap.add_argument("--openapi", type=Path, help="write the OpenAPI spec and exit")
    ap.add_argument("--remove-all-data", action="store_true", help="securely delete the database and keys")
    ap.add_argument("--demo", action="store_true", help="load synthetic demo data and sample reference data")
    ap.add_argument("--demo-invoices", type=int, default=1500)
    a = ap.parse_args(argv)

    from verismo_engine.config import Config

    cfg = Config.from_env()
    if a.remove_all_data:
        from verismo_engine.backup import secure_remove_all

        secure_remove_all(cfg.data_dir, cfg.keystore)
        print("all Verismo data removed")
        return 0

    from verismo_engine.context import open_engine
    from verismo_engine.logging_setup import setup_logging

    setup_logging(cfg.log_dir)
    if a.openapi:
        cfg.token = cfg.token or "openapi"
        cfg.start_workers = False
        from verismo_engine.api.app import create_app

        eng = open_engine(cfg)
        a.openapi.write_text(json.dumps(create_app(eng).openapi(), indent=1))
        eng.close()
        return 0
    eng = open_engine(cfg)
    if a.demo:
        from verismo_engine.demo import load_demo

        print(json.dumps(load_demo(eng, a.demo_invoices), indent=1))
        eng.close()
        return 0
    if not cfg.token or len(cfg.token) < 32:
        print("VERISMO_TOKEN (>=32 chars) is required", file=sys.stderr)
        return 2
    import uvicorn

    from verismo_engine.api.app import create_app

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
    logging.getLogger("verismo").info("engine listening on 127.0.0.1:%s", port)
    try:
        server.run()
    finally:
        eng.close()
    return 0


def _watch_parent() -> None:
    """Exit if the desktop shell that launched us goes away (crash, kill, or force-quit), so the
    engine never lingers holding the database open."""
    ppid = os.environ.get("VERISMO_PARENT_PID")
    if not ppid:
        return
    import threading
    import time

    import psutil

    pid = int(ppid)

    def loop() -> None:
        while True:
            time.sleep(2)
            if not psutil.pid_exists(pid):
                logging.getLogger("verismo").warning("desktop shell exited; engine stopping")
                os._exit(0)

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
