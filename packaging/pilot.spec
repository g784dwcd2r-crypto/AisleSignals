"""Attended pilot launcher; build on each target OS, never a signed installer."""
from pathlib import Path

repo = Path(SPECPATH).parent
# local-vision.py supplies the pinned model manifest. Weights/runtime remain
# external and must be explicitly installed and qualified on the pharmacy host.
a = Analysis(
    [str(repo / "scripts/run_pilot.py")],
    pathex=[str(repo), str(repo / "scripts")],
    binaries=[],
    datas=[(str(repo / "apps/web/dist"), "apps/web/dist"),
           (str(repo / "scripts/local-vision.py"), "scripts")],
    hiddenimports=["scripts.pilot_preflight", "scripts.desktop_owner", "scripts.desktop_startup", "scripts.desktop_update", "tarfile", "PIL.JpegImagePlugin", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AisleSignalsPilot", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AisleSignalsPilot")
