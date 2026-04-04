# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

try:
    project_root = Path(__file__).resolve().parent
except NameError:
    project_root = Path(os.getcwd()).resolve()
package_root = project_root / 'clipmind_ai'

datas = [(str(package_root / 'assets'), 'assets')]
binaries = []
hiddenimports = ['win32clipboard', 'win32con', 'win32api', 'win32gui', 'keyboard']
legacy_paddle_packages = ('paddle' + 'ocr', 'paddle' + 'paddle', 'paddle' + 'x')
for package_name in ('rapidocr_onnxruntime', 'rapidocr', 'sherpa_onnx', 'pyaudiowpatch', 'onnxruntime'):
    try:
        tmp_ret = collect_all(package_name)
    except Exception:
        continue
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [str(package_root / 'app' / 'main.py')],
    pathex=[str(package_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['paddle'] + list(legacy_paddle_packages),
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClipMindAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClipMindAI',
)
