"""Run PyInstaller on each target OS. Development bundles are unsigned."""
from pathlib import Path

repo = Path(SPECPATH).parent
a = Analysis(
    [str(repo / "scripts/run_prototype.py")],
    pathex=[str(repo)],
    binaries=[],
    datas=[(str(repo / "apps/web/dist"), "apps/web/dist")],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AisleSignalsPrototype", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AisleSignalsPrototype")
