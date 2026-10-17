# -*- mode: python ; coding: utf-8 -*-
"""Onedir only: source/config/login state is never collected as data."""
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

project = Path(SPECPATH).parent
sys.path.insert(0, str(project / "src"))
from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
validate_managed_path(project)
build_root = validate_managed_path(Path(os.environ["MMPS_BUILD_ROOT"]))
license_root = validate_managed_path(Path(os.environ["MMPS_LICENSE_ROOT"]))
hook = build_root / "runtime_f_drive.py"
hook.write_text(
    "import sys, ctypes\n"
    "from pathlib import Path\n"
    "from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path\n"
    "try:\n"
    "    validate_managed_path(Path(sys.executable))\n"
    "    validate_managed_path(Path(sys._MEIPASS))\n"
    "    root = sys.argv[sys.argv.index('--root') + 1] if '--root' in sys.argv else None\n"
    "    paths = AppPaths(root) if root is not None else AppPaths()\n"
    "    paths.configure_environment()\n"
    "except (ValueError, OSError, RuntimeError, IndexError):\n"
    "    if '--startup-check' not in sys.argv:\n"
    "        ctypes.windll.user32.MessageBoxW(None, '\u65e0\u6cd5\u5b89\u5168\u8bbf\u95ee F: \u5b58\u50a8\u76ee\u5f55\uff0c\u8bf7\u8fde\u63a5\u78c1\u76d8\u5e76\u9009\u62e9\u6279\u51c6\u7684 F: \u5de5\u4f5c\u76ee\u5f55\u3002', 'MathModel Paper Studio', 16)\n"
    "    raise SystemExit(1)\n",
    encoding="utf-8",
)
a = Analysis(
    [str(project / "scripts" / "verify_f_drive_outputs.py")],
    pathex=[str(project / "src")],
    binaries=[],
    datas=[(str(license_root), "licenses")],
    hiddenimports=collect_submodules("mathmodel_paper_studio") + ["qfluentwidgets._rc.resource", "qframelesswindow._rc.resource"],
    hookspath=[], hooksconfig={}, runtime_hooks=[str(hook)],
    excludes=["tests", "pytest", "coverage", "pip", "setuptools", "docx",
              "tkinter", "numpy", "scipy", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="MathModelPaperStudio", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    runtime_tmpdir=str(AppPaths().temp),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MathModelPaperStudio")
