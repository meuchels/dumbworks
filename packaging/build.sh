#!/bin/sh
# Build dist/m40scan-x86_64: one self-contained executable (Python, Tk, Pillow, numpy, libusb).
# Uses micromamba + conda-forge so the bundle only needs glibc 2.17 and the system X11/udev libs.
set -e
cd "$(dirname "$0")"
WORK="${WORK:-$PWD/.build}"
mkdir -p "$WORK"
if [ ! -x "$WORK/bin/micromamba" ]; then
    curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "$WORK" bin/micromamba
fi
export MAMBA_ROOT_PREFIX="$WORK/root"
if [ ! -x "$WORK/env/bin/python" ]; then
    "$WORK/bin/micromamba" create -y -p "$WORK/env" -c conda-forge \
        python=3.12 tk pillow numpy pyinstaller pyusb libusb
fi
mkdir -p "$WORK/libs"
cp -L "$WORK/env/lib/libusb-1.0.so.0" "$WORK/libs/libusb-1.0.so.0"
export M40_LIBUSB="$WORK/libs/libusb-1.0.so.0"
"$WORK/env/bin/pyinstaller" --noconfirm --clean --distpath "$WORK/dist" --workpath "$WORK/pyi" m40scan.spec
mkdir -p ../dist
cp "$WORK/dist/m40scan" ../dist/m40scan-x86_64
chmod +x ../dist/m40scan-x86_64
echo "Built dist/m40scan-x86_64"
