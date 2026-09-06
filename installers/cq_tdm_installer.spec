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
        'pydicom',
        'PIL',
        'PIL.Image',
        'matplotlib',
        'matplotlib.pyplot',
        'matplotlib.backends.backend_agg',
        'matplotlib.patches',
        'reportlab',
        'reportlab.lib',
        'reportlab.lib.pagesizes',
        'reportlab.lib.units',
        'reportlab.lib.colors',
        'reportlab.platypus',
        'reportlab.pdfgen',
    ],
    hookspath=[],
    hooksconfig={
        'matplotlib': {'backends': ['Agg']},
    },
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
        # Unused packages (matplotlib and PIL ARE used: NPS plots, PDF report figures, logo)
        'cairosvg',
        'cairocffi',
        'pygments',
        # Unused matplotlib backends / optional deps
        'matplotlib.backends.backend_qt5agg',
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_tkagg',
        'matplotlib.backends.backend_webagg',
        'matplotlib.backends.backend_nbagg',
        'matplotlib.backends.backend_gtk3agg',
        'matplotlib.backends.backend_gtk4agg',
        'matplotlib.backends.backend_macosx',
        'matplotlib.backends.backend_wx',
        'matplotlib.backends.backend_wxagg',
        'matplotlib.testing',
        'IPython',
        # Unused scipy submodules.
        # NOTE: scipy.linalg and scipy.special must NOT be excluded: scipy.ndimage
        # (used for NPS radial averaging) imports them at import time.
        'scipy.signal',
        'scipy.sparse',
        'scipy.spatial',
        'scipy.interpolate',
        'scipy.optimize',
        'scipy.stats',
        'scipy.io',
        'scipy.cluster',
        'scipy.fft',
        'scipy.integrate',
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
    upx=False,
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
    upx=False,
    name='CQ_TDM_installer',
)
