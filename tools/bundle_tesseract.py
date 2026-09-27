"""Copy Tesseract into the frozen engine so scanned-PDF OCR works with nothing else installed.

    uv run python tools/bundle_tesseract.py build/engine/invoice-analytics-engine

Result: <engine>/tesseract/ with the tesseract binary, the libraries it needs, and
tessdata/eng.traineddata. The engine finds it there (ingest/ocr/tesseract.py).

- Windows: copies the UB-Mannheim install (choco install tesseract), which is already self-contained.
- macOS: copies Homebrew's tesseract and every non-system dylib it loads, rewrites their load paths
  to the bundle, and ad-hoc signs them (Apple Silicon refuses to run modified unsigned code).
- Linux: copies the distro's tesseract and every library it loads except the C runtime; the engine
  sets LD_LIBRARY_PATH to the bundle when it runs OCR.
Finally runs the bundled binary to check it starts and lists the English language data.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Always provided by the OS; bundling them breaks programs on other distros. libstdc++ is bundled on
# purpose: tesseract needs the version it was built against, which older distros may lack.
LINUX_SYSTEM_LIBS = re.compile(r"^(libc|libm|libdl|libpthread|librt|libresolv|ld-linux[^.]*|linux-vdso|libgcc_s)\.so")


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout  # noqa: S603


def find_tessdata(binary: Path) -> Path:
    cands = [
        Path(os.environ["TESSDATA_PREFIX"]) if os.environ.get("TESSDATA_PREFIX") else None,
        binary.resolve().parent.parent / "share" / "tessdata",  # Homebrew
        *sorted(Path("/usr/share/tesseract-ocr").glob("*/tessdata"), reverse=True),  # Debian/Ubuntu
        Path("/usr/share/tessdata"),  # Fedora
    ]
    for c in cands:
        if c and (c / "eng.traineddata").is_file():
            return c
    raise SystemExit("eng.traineddata not found; install the English language data")


def bundle_windows(dest: Path) -> Path:
    src = Path(os.environ.get("TESSERACT_HOME", r"C:\Program Files\Tesseract-OCR"))
    if not (src / "tesseract.exe").is_file():
        raise SystemExit(f"tesseract.exe not found in {src}")
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest / "tesseract.exe"


def _mac_deps(path: Path) -> list[str]:
    out = run("otool", "-L", str(path)).splitlines()[1:]
    deps = [line.strip().split(" (")[0] for line in out]
    return [d for d in deps if not d.startswith(("/usr/lib/", "/System/")) and not d.endswith(path.name)]


def _resolve_mac(dep: str, loader: Path) -> Path:
    if dep.startswith("@loader_path/"):
        return (loader.parent / dep.removeprefix("@loader_path/")).resolve()
    if dep.startswith("@rpath/"):
        for base in (loader.parent, Path("/opt/homebrew/lib"), Path("/usr/local/lib")):
            cand = base / dep.removeprefix("@rpath/")
            if cand.exists():
                return cand.resolve()
    return Path(dep).resolve()


def bundle_macos(dest: Path) -> Path:
    found = shutil.which("tesseract")
    if not found:
        raise SystemExit("tesseract not found (brew install tesseract)")
    src = Path(found).resolve()
    libs = dest / "libs"
    libs.mkdir(parents=True, exist_ok=True)
    binary = dest / "tesseract"
    shutil.copy2(src, binary)
    todo: list[tuple[Path, Path]] = [(binary, src)]  # (copy to patch, original it came from)
    copied: dict[str, Path] = {}
    while todo:
        target, original = todo.pop()
        os.chmod(target, 0o755)
        for dep in _mac_deps(target):
            real = _resolve_mac(dep, original)
            name = real.name
            if name not in copied:
                copied[name] = libs / name
                shutil.copy2(real, copied[name])
                os.chmod(copied[name], 0o755)
                run("install_name_tool", "-id", f"@loader_path/{name}", str(copied[name]))
                todo.append((copied[name], real))
            ref = f"@executable_path/libs/{name}" if target == binary else f"@loader_path/{name}"
            run("install_name_tool", "-change", dep, ref, str(target))
    for f in [binary, *copied.values()]:
        run("codesign", "--force", "--sign", "-", str(f))
    return binary


def bundle_linux(dest: Path) -> Path:
    found = shutil.which("tesseract")
    if not found:
        raise SystemExit("tesseract not found (apt install tesseract-ocr)")
    lib = dest / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    binary = dest / "tesseract"
    shutil.copy2(Path(found).resolve(), binary)
    for line in run("ldd", str(binary)).splitlines():
        m = re.match(r"\s*(\S+) => (\S+)", line)
        if not m or LINUX_SYSTEM_LIBS.match(m.group(1)):
            continue
        shutil.copy2(Path(m.group(2)).resolve(), lib / m.group(1))
    return binary


def _vers(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def check_macos_minimum(engine: Path, minimum: str) -> None:
    """Fail if any bundled Mach-O file needs a newer macOS than the app promises (Homebrew and some
    wheels are built for the build machine's own macOS version)."""
    too_new = []
    for f in engine.rglob("*"):
        if not f.is_file() or f.is_symlink() or f.stat().st_size < 4096:
            continue
        with f.open("rb") as fh:
            if fh.read(4) not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):  # Mach-O 64 / fat
                continue
        out = subprocess.run(["otool", "-l", str(f)], capture_output=True, text=True).stdout  # noqa: S603, S607
        m = re.search(r"LC_BUILD_VERSION.*?minos ([0-9.]+)", out, re.S)
        if m and _vers(m.group(1)) > _vers(minimum):
            too_new.append(f"{m.group(1)}  {f.relative_to(engine)}")
    if too_new:
        raise SystemExit(f"{len(too_new)} bundled files need macOS newer than {minimum}:\n" + "\n".join(too_new[:20]))
    print(f"all bundled binaries run on macOS {minimum}+")


def main() -> int:
    engine = Path(sys.argv[1])
    dest = engine / "tesseract"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    if sys.platform == "win32":
        binary = bundle_windows(dest)
    else:
        binary = bundle_macos(dest) if sys.platform == "darwin" else bundle_linux(dest)
        data = dest / "tessdata"
        data.mkdir(exist_ok=True)
        src = find_tessdata(Path(shutil.which("tesseract") or ""))
        for name in ("eng.traineddata", "osd.traineddata"):
            if (src / name).is_file():
                shutil.copy2(src / name, data / name)
    env = {"TESSDATA_PREFIX": str(dest / "tessdata"), "PATH": os.environ.get("PATH", "")}
    if sys.platform.startswith("linux"):
        env["LD_LIBRARY_PATH"] = str(dest / "lib")
    langs = subprocess.run(  # noqa: S603
        [str(binary), "--list-langs"], env=env, capture_output=True, text=True, check=True
    ).stdout
    if "eng" not in langs.split():
        raise SystemExit(f"bundled tesseract does not see eng.traineddata:\n{langs}")
    if sys.platform == "darwin" and os.environ.get("MACOS_MIN"):  # set by the release build
        check_macos_minimum(engine, os.environ["MACOS_MIN"])
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    print(f"bundled tesseract into {dest} ({size:.0f} MB): OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
