# PyInstaller build for Cardclops.exe: python -m PyInstaller installer/Cardclops.spec
# A one-folder build (starts fast, and Inno Setup packs the folder); no console window.
from pathlib import Path

ROOT = Path(SPECPATH).parent

analysis = Analysis(
    [str(ROOT / "installer" / "launch.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "static"), "static"), (str(ROOT / "scripts"), "scripts")],
    hiddenimports=["gallery.importers", "gallery.goldfish", "gallery.builder", "gallery.manafix",
                   "gallery.archidekt", "gallery.history", "gallery.versions", "certifi"],
    excludes=["tkinter", "PIL", "numpy.f2py", "pytest"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name="Cardclops",
    icon=str(ROOT / "installer" / "icon.ico"),
    console=False,
    version=None,
)
collect = COLLECT(exe, analysis.binaries, analysis.datas, name="Cardclops")
