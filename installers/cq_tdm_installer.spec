# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for CQ TDM (one-folder build for Inno Setup).

This produces dist/CQ_TDM_installer/ with all files unpacked,
which Inno Setup then packages into a graphical Windows installer.

For the standalone portable .exe, use cq_tdm.spec instead.

Run: pyinstaller installers/cq_tdm_installer.spec
Then: iscc installers/cq-tdm.iss
"""

import sys
from pathlib import Path

block_cipher = None

project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / 'src' / 'cq_tdm' / 'main.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=[
        (str(project_root / 'src' / 'cq_tdm' / 'assets'), 'cq_tdm/assets'),
    ],
    hiddenimports=[
        # Lazy-loaded project modules (importlib.import_module)
        'cq_tdm.core.dicom_loader',
        'cq_tdm.core.water_phantom',
        'cq_tdm.core.nps',
        'cq_tdm.reports.pdf_report',
        # Third-party
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtPrintSupport',
        'numpy',
        'scipy',
        'scipy.ndimage',
        'scipy.integrate',
        'scipy.signal',
        'pydicom',
        'reportlab',
        'reportlab.lib',
        'reportlab.lib.pagesizes',
        'reportlab.lib.units',
        'reportlab.lib.colors',
        'reportlab.platypus',
        'reportlab.pdfgen',
        'matplotlib',
        'matplotlib.backends.backend_agg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CQ_TDM',
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
    icon=str(project_root / 'src' / 'cq_tdm' / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CQ_TDM_installer',
)
