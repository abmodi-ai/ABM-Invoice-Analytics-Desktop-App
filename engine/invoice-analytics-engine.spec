# PyInstaller one-dir build of the engine sidecar.
#   uv run pyinstaller engine/invoice-analytics-engine.spec --noconfirm --distpath build/engine
# The output folder is copied to apps/desktop/src-tauri/resources/engine/ and bundled by Tauri.
# Tesseract (binary, its libraries and tessdata/eng.traineddata) is copied into
# <dist>/invoice-analytics-engine/tesseract/ by tools/bundle_tesseract.py.
# ruff: noqa
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

hidden = (
    collect_submodules("invoice_analytics")
    + collect_submodules("uvicorn")
    + ["sqlcipher3", "argon2", "rapidfuzz", "jellyfish", "metaphone", "pdfplumber", "pypdfium2", "reportlab"]
)
if sys.platform != "win32":  # the database key lives in the OS keyring (Keychain / Secret Service)
    hidden += collect_submodules("keyring")
    if sys.platform.startswith("linux"):
        hidden += collect_submodules("secretstorage") + collect_submodules("jeepney")
datas = (
    collect_data_files("invoice_analytics", includes=["db/migrations/*.sql", "ai/prompts/*.md"])
    + collect_data_files("pypdfium2_raw")
    + collect_data_files("reportlab")
    + (copy_metadata("keyring") if sys.platform != "win32" else [])  # backends are found via entry points
)

a = Analysis(
    ["invoice_analytics/__main__.py"],
    pathex=["."],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "hypothesis", "synthgen", "fastembed"] + (["keyring"] if sys.platform == "win32" else []),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="invoice-analytics-engine", console=False, disable_windowed_traceback=True,
          upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="invoice-analytics-engine")
