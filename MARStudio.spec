# Windows x64 onedir build. Shared Qt DLLs stay separate.
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
data = [(str(root/"marstudio"/"fonts"),"marstudio/fonts"),
        (str(root/"marstudio"/"integrity.py"),"marstudio"),
        (str(root/"docs"/"LICENSES.md"),"notices")]
for dist in ("PySide6-Essentials","shiboken6","reportlab","pillow","charset-normalizer"):
    data += copy_metadata(dist)
a = Analysis([str(root/"run_studio.py")], pathex=[str(root)], binaries=[], datas=data,
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["tkinter","PySide6.QtTest"], noarchive=False, optimize=0)
# Qt's unversioned ICU imports target the Windows 11 system forwarders. A PATH-resolved
# third-party ICU can share the DLL filename while offering only version-suffixed symbols.
# Leave those DLLs and the Universal C Runtime to Windows itself.
system_dlls = {"icu.dll", "icuuc.dll", "icuin.dll", "icudt.dll", "ucrtbase.dll"}
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in system_dlls]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MARStudio",
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MARStudio")

