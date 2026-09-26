# PyInstaller one-dir build of the engine sidecar.
#   uv run pyinstaller engine/verismo-engine.spec --noconfirm --distpath build/engine
# The output folder is copied to apps/desktop/src-tauri/resources/engine/ and bundled by Tauri.
# Tesseract (binary + eng.traineddata) is copied into <dist>/verismo-engine/tesseract/ by CI.
# ruff: noqa
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("verismo_engine")
    + collect_submodules("uvicorn")
    + ["sqlcipher3", "argon2", "rapidfuzz", "jellyfish", "metaphone", "pdfplumber", "pypdfium2", "reportlab"]
)
datas = (
    collect_data_files("verismo_engine", includes=["db/migrations/*.sql", "ai/prompts/*.md"])
    + collect_data_files("pypdfium2_raw")
    + collect_data_files("reportlab")
)

a = Analysis(
    ["verismo_engine/__main__.py"],
    pathex=["."],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "hypothesis", "synthgen", "fastembed", "keyring"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="verismo-engine", console=False, disable_windowed_traceback=True,
          upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="verismo-engine")
