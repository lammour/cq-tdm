# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for CQ TDM."""

import sys
from pathlib import Path

block_cipher = None

# Get the project root
project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / 'src' / 'cq_tdm' / 'main.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=[
        # Include reference PDFs if needed (optional, comment out to reduce size)
        # (str(project_root / 'references'), 'references'),
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
        'pydicom',
        'reportlab',
        'reportlab.lib',
        'reportlab.lib.pagesizes',
        'reportlab.lib.units',
        'reportlab.lib.colors',
        'reportlab.platypus',
        'reportlab.pdfgen',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        # Unused Qt modules
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtQuick',
        'PySide6.QtQml',
        'PySide6.QtDesigner',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.QtMultimedia',
        'PySide6.QtNetwork',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtPositioning',
        # Unused packages
        'matplotlib',
        'PIL',
        'Pillow',
        'cairosvg',
        'cairocffi',
        'pygments',
        'fonttools',
        # Unused scipy submodules
        'scipy.signal',
        'scipy.sparse',
        'scipy.spatial',
        'scipy.interpolate',
        'scipy.optimize',
        'scipy.linalg',
        'scipy.stats',
        'scipy.io',
        'scipy.cluster',
        'scipy.fft',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CQ_TDM',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # Set to True for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon path here if you have one: 'icon.ico' for Windows
)
