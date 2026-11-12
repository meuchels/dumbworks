# dumbworks

An open-source replacement for SmartWorks MFP on Linux: scanner software for the
**Colortrac SmartLF M-series** wide-format scanner sold as part of the
**Canon imagePROGRAF iPF MFP M40** systems (USB ID `148c:0060`).

Not affiliated with Colortrac or Canon. The protocol was worked out by observing
how the original Windows software talks to the scanner; see [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Features

- Auto-detects page width and position, like SmartWorks
- Scan → preview → adjust → save workflow:
  brightness, white point, black point, sharpening, deskew, crop, invert, mirror, zoom
- Automatic straightening and cropping of crooked pages
- 100–1200 dpi, colour
- Saves PDF, PNG, JPEG or TIFF; batch mode for feeding a stack of pages
- Large, touch-friendly controls; drag to pan when zoomed in
- Command-line mode for scripting

## Running

Download `m40scan-x86_64` from Releases (or build it, below), then:

```sh
chmod +x m40scan-x86_64
./m40scan-x86_64                          # the app
./m40scan-x86_64 scan page.pdf --dpi 300  # one page from the terminal
```

It is a single self-contained executable for 64-bit x86 Linux (glibc 2.17 or newer,
X11/XWayland desktop with the usual libX11, libXft and fontconfig). Nothing to install.

### USB permission

The first time on each computer, use **Setup → Fix USB permission** in the app
(or `./m40scan-x86_64 setup-usb`), then unplug and replug the scanner.
This installs [packaging/60-m40scan.rules](packaging/60-m40scan.rules) into
`/etc/udev/rules.d/`.

### Running from source

```sh
sudo apt install python3-usb python3-pil python3-pil.imagetk python3-numpy python3-tk
python3 src/m40_app.py
```

## Building the standalone executable

```sh
packaging/build.sh      # -> dist/m40scan-x86_64
```

The script downloads micromamba and a conda-forge Python, then bundles everything with
PyInstaller. Libraries from the build machine's `/usr` are left out so the result runs
on old and new distributions alike.

## Layout

| Path | |
|------|--|
| `src/m40lib.py` | scanner driver (USB protocol) |
| `src/m40_deskew.py` | skew detection, straightening, paper cropping |
| `src/m40_edit.py` | preview adjustments |
| `src/m40_app.py` | Tk GUI and command line |
| `tools/` | reverse-engineering helpers: read-only probe, USBPcap capture parser |
| `docs/PROTOCOL.md` | protocol notes |

## Status

Tested: colour 200 dpi scanning of letter-size pages, automatic page sizing, deskew.
Not yet confirmed on hardware: other resolutions, grayscale/black & white modes
(not implemented), scans wider than letter.
