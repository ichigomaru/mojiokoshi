# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['main.py'],
    pathex=['src'],  # gui.py は mojiokoshi を src から import する
    binaries=[],
    datas=collect_data_files('customtkinter'),  # CustomTkinter のテーマ(json)・フォント
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icons/my_icon.icns'],
)
app = BUNDLE(
    exe,
    name='main.app',
    icon='icons/my_icon.icns',
    bundle_identifier=None,
)
