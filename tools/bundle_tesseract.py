"""Copy Tesseract into the frozen engine so scanned-PDF OCR works with nothing else installed.

    uv run python tools/bundle_tesseract.py build/engine/invoice-analytics-engine

Result: <engine>/tesseract/ with the tesseract binary, the libraries it needs, and
tessdata/eng.traineddata. The engine finds it there (ingest/ocr/tesseract.py).

- Windows: copies MSYS2's UCRT64 tesseract.exe (mingw-w64-ucrt-x86_64-tesseract-ocr) and only the
  DLLs it loads (found by walking PE imports with objdump), not the training tools and their GUI
  libraries. MSYS2_ROOT points at the MSYS2 install (default C:/msys64).
- macOS: copies Homebrew's tesseract and every non-system dylib it loads, rewrites their load paths
  to the bundle, and ad-hoc signs them (Apple Silicon refuses to run modified unsigned code).
- Linux: copies the distro's tesseract and every library it loads except the C runtime; the engine
  sets LD_LIBRARY_PATH to the bundle when it runs OCR.
It also copies the licence texts of Tesseract and of every library bundled with it into
<engine>/licenses/ (Apache-2.0 and the BSD-style licences require them to travel with every copy),
together with this app's own LICENSE and NOTICE, and fails if Tesseract's or Leptonica's is missing.
Finally runs the bundled binary to check it starts and lists the English language data.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE_NAME = re.compile(r"(licen[cs]e|copying|notice|copyright)", re.I)  # e.g. leptonica-license.txt

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


MSYS_PREFIX = "mingw-w64-ucrt-x86_64-"


def _msys_root() -> Path:
    return Path(os.environ.get("MSYS2_ROOT") or r"C:\msys64")


def bundle_windows(dest: Path) -> tuple[Path, list[Path]]:
    bin_dir = _msys_root() / "ucrt64" / "bin"
    exe = bin_dir / "tesseract.exe"
    if not exe.is_file():
        raise SystemExit(f"{exe} not found (pacman -S {MSYS_PREFIX}tesseract-ocr {MSYS_PREFIX}binutils)")
    needed: dict[str, Path] = {}
    todo = [exe]
    while todo:
        for name in re.findall(r"DLL Name: (\S+)", run(str(bin_dir / "objdump.exe"), "-p", str(todo.pop()))):
            dll = bin_dir / name  # Windows' own DLLs (KERNEL32, ucrtbase, ...) are not in ucrt64/bin
            if name.lower() not in needed and dll.is_file():
                needed[name.lower()] = dll
                todo.append(dll)
    shutil.copy2(exe, dest / exe.name)
    for dll in needed.values():
        shutil.copy2(dll, dest / dll.name)
    return dest / exe.name, [exe, *needed.values()]


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


def bundle_macos(dest: Path) -> tuple[Path, list[Path]]:
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
    originals: list[Path] = []
    while todo:
        target, original = todo.pop()
        os.chmod(target, 0o755)
        for dep in _mac_deps(target):
            real = _resolve_mac(dep, original)
            name = real.name
            if name not in copied:
                copied[name] = libs / name
                originals.append(real)
                shutil.copy2(real, copied[name])
                os.chmod(copied[name], 0o755)
                run("install_name_tool", "-id", f"@loader_path/{name}", str(copied[name]))
                todo.append((copied[name], real))
            ref = f"@executable_path/libs/{name}" if target == binary else f"@loader_path/{name}"
            run("install_name_tool", "-change", dep, ref, str(target))
    for f in [binary, *copied.values()]:
        run("codesign", "--force", "--sign", "-", str(f))
    return binary, [src, *originals]


def bundle_linux(dest: Path) -> tuple[Path, list[Path]]:
    found = shutil.which("tesseract")
    if not found:
        raise SystemExit("tesseract not found (apt install tesseract-ocr)")
    lib = dest / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    binary = dest / "tesseract"
    shutil.copy2(Path(found).resolve(), binary)
    sources = [Path(found)]
    for line in run("ldd", str(binary)).splitlines():
        m = re.match(r"\s*(\S+) => (\S+)", line)
        if not m or LINUX_SYSTEM_LIBS.match(m.group(1)):
            continue
        shutil.copy2(Path(m.group(2)).resolve(), lib / m.group(1))
        sources.append(Path(m.group(2)))
    return binary, sources


def _copy_licence_files(src_dir: Path, out: Path) -> int:
    n = 0
    for f in sorted(src_dir.iterdir()) if src_dir.is_dir() else []:
        if f.is_file() and LICENSE_NAME.search(f.name):
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out / f.name)
            n += 1
    return n


def licences_macos(sources: list[Path], out: Path) -> list[str]:
    """Licence files from each Homebrew keg (Cellar/<formula>/<version>/); for a keg that ships
    none (e.g. leptonica), from the formula's source archive."""
    found = []
    kegs = set()
    for p in sources:
        if "Cellar" in p.parts:
            i = p.parts.index("Cellar")
            kegs.add(Path(*p.parts[: i + 3]))  # .../Cellar/<formula>/<version>
    for keg in sorted(kegs):
        formula = keg.parent.name
        if not _copy_licence_files(keg, out / formula):
            run("brew", "fetch", "--build-from-source", formula)
            archive = Path(run("brew", "--cache", "--build-from-source", formula).strip())
            with tarfile.open(archive) as tar:
                for m in tar.getmembers():
                    parts = Path(m.name).parts
                    if m.isfile() and len(parts) <= 2 and LICENSE_NAME.search(parts[-1]):
                        (out / formula).mkdir(parents=True, exist_ok=True)
                        (out / formula / parts[-1]).write_bytes(tar.extractfile(m).read())  # type: ignore[union-attr]
        if (out / formula).is_dir():
            found.append(formula)
    return found


def licences_linux(sources: list[Path], out: Path) -> list[str]:
    """Debian/Ubuntu: every package's licence is /usr/share/doc/<package>/copyright."""
    found = []
    for path in sources:
        pkg = None
        for cand in {str(path), str(path.resolve()), str(path).replace("/usr/lib/", "/lib/", 1)}:
            r = subprocess.run(["dpkg", "-S", cand], capture_output=True, text=True)  # noqa: S603, S607
            if r.returncode == 0:
                pkg = r.stdout.split(":", 1)[0].strip()
                break
        copyright_file = Path("/usr/share/doc") / (pkg or "") / "copyright"
        if pkg and copyright_file.is_file():
            (out / pkg).mkdir(parents=True, exist_ok=True)
            shutil.copy2(copyright_file, out / pkg / "copyright")
            found.append(pkg)
        else:
            print(f"warning: no licence file found for {path.name}")
    return sorted(set(found))


def licences_windows(sources: list[Path], out: Path) -> list[str]:
    """MSYS2 installs every package's licence as /ucrt64/share/licenses/<name>/; pacman says which
    package each bundled file came from."""
    root = _msys_root()
    msys_paths = ["/" + p.relative_to(root).as_posix() for p in sources]
    owners = run(
        str(root / "usr" / "bin" / "bash.exe"), "-lc", "pacman -Qqo " + " ".join(f"'{p}'" for p in msys_paths)
    ).split()
    found = []
    for pkg in sorted(set(owners)):
        name = pkg.removeprefix(MSYS_PREFIX)
        if _copy_licence_files(root / "ucrt64" / "share" / "licenses" / name, out / name):
            found.append(name)
        else:
            print(f"warning: no licence file found for {pkg}")
    return found


def write_licences(engine: Path, dest: Path, sources: list[Path]) -> None:
    out = engine / "licenses"
    shutil.rmtree(out, ignore_errors=True)
    app = out / "ABM-Invoice-Analytics"
    app.mkdir(parents=True)
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, app / name)
    if sys.platform == "win32":
        found = licences_windows(sources, out)
    elif sys.platform == "darwin":
        found = licences_macos(sources, out)
    else:
        found = licences_linux(sources, out)
    text = " ".join(found).lower()
    required = ("tesseract", "lept")
    missing = [n for n in required if n not in text]
    if not found or missing:
        raise SystemExit(f"licence text not found for: {', '.join(missing)} (found: {found})")
    print(f"licence texts for {len(found)} bundled components -> {out.relative_to(engine)}/")


def _vers(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def check_macos_minimum(engine: Path, minimum: str) -> None:
    """Fail if any bundled Mach-O file needs a newer major macOS than the app promises (Homebrew and
    some wheels are built for the build machine's own macOS version)."""
    too_new = []
    for f in engine.rglob("*"):
        if not f.is_file() or f.is_symlink() or f.stat().st_size < 4096:
            continue
        with f.open("rb") as fh:
            if fh.read(4) not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):  # Mach-O 64 / fat
                continue
        out = subprocess.run(["otool", "-l", str(f)], capture_output=True, text=True).stdout  # noqa: S603, S607
        m = re.search(r"LC_BUILD_VERSION.*?minos ([0-9.]+)", out, re.S)
        # Compare major versions: minor updates are free for every Mac on that major release, and
        # Homebrew builds from source for the runner's exact version (e.g. 14.8).
        if m and _vers(m.group(1))[0] > _vers(minimum)[0]:
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
        binary, sources = bundle_windows(dest)
    else:
        binary, sources = bundle_macos(dest) if sys.platform == "darwin" else bundle_linux(dest)
    data = dest / "tessdata"
    data.mkdir(exist_ok=True)
    src = find_tessdata(sources[0])  # tessdata sits beside the binary's install prefix
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
    write_licences(engine, dest, sources)
    if sys.platform == "darwin" and os.environ.get("MACOS_MIN"):  # set by the release build
        check_macos_minimum(engine, os.environ["MACOS_MIN"])
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    print(f"bundled tesseract into {dest} ({size:.0f} MB): OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
