"""Install a downloadable build the way a user would, launch it, and check the engine comes up.

    uv run python tools/install_test.py <ABM Invoice Analytics setup.exe | .dmg | .AppImage>

Windows: silent per-user install (/S), launch, engine healthy, clean exit, silent uninstall.
macOS:   mount the DMG, copy the app out (as dragging to Applications does), verify its signature,
         launch, engine healthy, clean exit.
Linux:   run the AppImage (extract-and-run, so no FUSE is needed; CI wraps this in xvfb-run),
         engine healthy, clean exit.
"Clean exit" means the engine sidecar stops by itself once the app is gone (parent watchdog).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil

ENGINE = "invoice-analytics-engine"


def engines(root: psutil.Process | None = None) -> list[psutil.Process]:
    """Engine processes: children of the app (when given), else any process running the bundled
    engine. Matching on the executable path, not the process name: a PyInstaller build on macOS
    reports itself as Python."""
    procs = root.children(recursive=True) if root else list(psutil.process_iter())
    out = []
    for p in procs:
        try:
            exe = (p.exe() or "").replace("\\", "/")
            cmd = " ".join(p.cmdline()).replace("\\", "/")
        except (psutil.Error, OSError):
            continue
        if f"engine/{ENGINE}" in exe or f"engine/{ENGINE}" in cmd:
            out.append(p)
        elif root and ("uv" in os.path.basename(exe) or "python" in os.path.basename(exe)):
            raise SystemExit(f"the app started a development engine, not its bundled one: {cmd}")
    return out


def wait_healthy(app: subprocess.Popen[bytes], timeout: float = 180) -> int:
    root = psutil.Process(app.pid)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if app.poll() is not None:
            raise SystemExit(f"the app exited during start-up (code {app.returncode})")
        for p in engines(root):
            try:
                conns = p.net_connections(kind="inet")
            except (psutil.Error, OSError):
                continue
            for c in conns:
                if c.status != psutil.CONN_LISTEN:
                    continue
                try:
                    r = httpx.get(f"http://127.0.0.1:{c.laddr.port}/health", timeout=3, trust_env=False)
                except httpx.HTTPError:
                    continue
                if r.status_code == 200:
                    print(f"engine healthy on port {c.laddr.port}: {r.json()}")
                    return int(c.laddr.port)
        time.sleep(2)
    raise SystemExit(f"the installed app did not bring up a healthy engine within {timeout:.0f} s")


def stop_and_check(app: subprocess.Popen[bytes] | psutil.Process) -> None:
    """Kill the app (as a crash or force-quit would) and check its engine stops by itself."""
    if isinstance(app, subprocess.Popen):
        app = psutil.Process(app.pid)
    try:
        kids = app.children(recursive=True)
    except psutil.NoSuchProcess:
        kids = []
    ours = [k for k in kids if k in engines()]  # only engines this test started, never the user's own
    engine_tree = set(ours)
    for e in ours:
        with contextlib.suppress(psutil.Error):
            engine_tree.update(e.children(recursive=True))
    # Everything else in the tree is the app: on Linux the AppImage runtime starts the real app as a
    # child, so killing only the process we launched would leave the app (and rightly its engine) up.
    for p in [app, *(k for k in kids if k not in engine_tree)]:
        with contextlib.suppress(psutil.Error):
            p.kill()
    for p in [app, *(k for k in kids if k not in engine_tree)]:
        with contextlib.suppress(psutil.Error):
            p.wait(timeout=10)  # reap, as launchd/init would for a real user
    # The engine watches its parent and exits within a few seconds.
    end = time.monotonic() + 20
    while time.monotonic() < end and any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in ours):
        time.sleep(1)
    left = [p for p in ours if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
    for p in engine_tree:
        with contextlib.suppress(psutil.Error):
            p.kill()
    if left:
        raise SystemExit("the engine outlived the app (parent watchdog did not stop it)")
    print("engine stopped with the app")


def test_windows(setup: Path) -> None:
    subprocess.run([str(setup), "/S"], check=True)  # noqa: S603
    base = Path(os.environ["LOCALAPPDATA"])
    engine = next(base.glob(f"*/engine/{ENGINE}.exe"), None) or next(base.glob(f"**/{ENGINE}.exe"), None)
    if engine is None:
        raise SystemExit("engine not found after install")
    root = engine.parent.parent
    print(f"installed to: {root}")
    app = next(e for e in root.glob("*.exe") if "uninstall" not in e.name.lower())
    proc = subprocess.Popen([str(app)])  # noqa: S603
    wait_healthy(proc)
    stop_and_check(proc)
    subprocess.run([str(root / "uninstall.exe"), "/S"], check=True)  # noqa: S603
    end = time.monotonic() + 30  # the uninstaller re-launches itself from %TEMP%
    while time.monotonic() < end and (root / "engine").exists():
        time.sleep(1)
    if (root / "engine").exists():
        raise SystemExit("uninstall left the engine behind")
    print("uninstalled")


def test_macos(dmg: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="ia-install-"))
    mnt = work / "mnt"
    subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mnt), str(dmg)], check=True
    )  # noqa: S603, S607
    try:
        bundle = next(mnt.glob("*.app"))
        if not (mnt / "Applications").is_symlink():
            raise SystemExit("the DMG has no Applications shortcut to drag the app onto")
        app = work / "Applications" / bundle.name
        app.parent.mkdir()
        subprocess.run(["ditto", str(bundle), str(app)], check=True)  # noqa: S603, S607
    finally:
        subprocess.run(["hdiutil", "detach", str(mnt), "-quiet"], check=False)  # noqa: S603, S607
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=1", str(app)], check=True
    )  # noqa: S603, S607
    print(f"installed to: {app} (signature valid)")
    exe = next((app / "Contents" / "MacOS").iterdir())
    proc = subprocess.Popen([str(exe)])  # noqa: S603
    try:
        wait_healthy(proc)
    finally:
        stop_and_check(proc)
    shutil.rmtree(work, ignore_errors=True)


def test_linux(appimage: Path) -> None:
    appimage.chmod(0o755)
    env = {**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"}
    proc = subprocess.Popen([str(appimage)], env=env)  # noqa: S603
    try:
        wait_healthy(proc)
    finally:
        stop_and_check(proc)


def main() -> int:
    target = Path(sys.argv[1]).resolve()
    # Keep the test away from the tester's real data (and, on macOS, the login Keychain).
    data = Path(tempfile.mkdtemp(prefix="ia-install-data-"))
    os.environ["IA_DATA_DIR"] = str(data)
    if sys.platform != "win32":
        os.environ.setdefault("IA_KEYSTORE", "file")
    size = target.stat().st_size / 1e6
    print(f"installer: {target.name} ({size:.0f} MB)")
    {"win32": test_windows, "darwin": test_macos}.get(sys.platform, test_linux)(target)
    shutil.rmtree(data, ignore_errors=True)
    print("install test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
