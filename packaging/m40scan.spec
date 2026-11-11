# -*- mode: python ; coding: utf-8 -*-
# Build with packaging/build.sh (uses a conda-forge Python so the result runs on glibc 2.17+).
import os

LIBUSB = os.environ["M40_LIBUSB"]          # path to libusb-1.0.so.0 from the build env

a = Analysis(
    ['../src/m40_app.py'],
    pathex=['../src'],
    binaries=[(LIBUSB, '.')],
    datas=[('../src/m40_deskew.py', '.'), ('../src/m40_edit.py', '.')],
    hiddenimports=['PIL._tkinter_finder'],
    noarchive=False,
)
# Leave out libraries picked up from the build machine's /usr (X11, udev, ...):
# every desktop Linux has its own, and bundling them would raise the glibc requirement.
a.binaries = [b for b in a.binaries if not (b[1].startswith('/usr/') or b[1].startswith('/lib'))]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='m40scan',
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
