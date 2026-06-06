# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file pour Planning Étudiant IA
Lance avec: pyinstaller build.spec
"""

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.building.datastruct import Tree

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('data', 'data'),
        ('database', 'database'),
        ('modules', 'modules'),
        ('pages', 'pages'),
        ('services', 'services'),
        ('utils', 'utils'),
    ],
    hiddenimports=[
        'streamlit',
        'streamlit.runtime',
        'streamlit.components',
        'sqlalchemy',
        'google.generativeai',
        'pdfplumber',
        'fitz',
        'plotly',
        'icalendar',
        'reportlab',
        'pandas',
    ],
    hookspath=['./'],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    noarchive=False,
)

import streamlit as _st
a.datas += Tree(str(_st.__path__[0]), prefix='streamlit')

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Planning-Etudiant-IA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
