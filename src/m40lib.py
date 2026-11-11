"""Driver for the Colortrac SmartLF M-series scanner (Canon iPF MFP M40), USB 148c:0060.

Protocol worked out from SmartWorks MFP 4.3 (SmartLF SC42 Com.dll) and USB captures.
Everything is vendor control requests (type 0x42 out / 0xC2 in) plus bulk endpoints:
  0x7E in 12  paper sensor; byte 8 = 0 none, 2 detected, 3 ready
  0x65 out 1  prepare / release paper
  0x78 in 48  device information
  0xC5 out 8  sent verbatim from the capture (meaning unknown)
  0x7D in 12  detected paper: (2, width, left edge) in 600 dpi sensor pixels
  0x64 out 28 scan setup: width px, 63000, offset px, 0, xdpi, ydpi, 0
  0x6F out 8  upload header (size, 0x0A) followed by a 12292-byte tone table on bulk EP 0x01
  0x67, 0x88  4-byte values 1 and 0, sent before start
  0x68 out 1  start scan
  0x71 in 8   (bytes buffered, state 2 = scanning / 1 = finished)
  0x70 out 8  (bytes, 1) then bulk read from EP 0x83, as N*1024 bytes plus the remainder
Image data is 8-bit RGB, already corrected by the scanner.
"""
import base64
import struct
import time
import zlib

import usb.core
import usb.util

def _find_backend():
    """Prefer a libusb bundled with the standalone app; fall back to the system one."""
    import os
    import sys
    here = getattr(sys, "_MEIPASS", None)
    if here:
        for name in ("libusb-1.0.so.0", "libusb-1.0.so"):
            path = os.path.join(here, name)
            if os.path.exists(path):
                import usb.backend.libusb1
                be = usb.backend.libusb1.get_backend(find_library=lambda _x, p=path: p)
                if be is not None:
                    return be
    try:
        import libusb_package
        return libusb_package.get_libusb1_backend()
    except Exception:
        return None


_BACKEND = _find_backend()

VID, PID = 0x148C, 0x0060
OUT, IN = 0x42, 0xC2
EP_DATA_IN, EP_DATA_OUT = 0x83, 0x01
FIELD1 = 63000
DPI = 200
# Resolutions SmartWorks MFP offers; the SC42 driver passes all of these straight to the scanner.
RESOLUTIONS = [100, 200, 300, 400, 600, 1200]
C5_BYTES = bytes.fromhex("b934da019dcd7bea")
TABLE = zlib.decompress(base64.b64decode("eNrt1/N31XEYAOCdGi7rZmPZWjaWbds2F5Zbtm3bts1l21yu+/UH79vvnfM+/8XjEymyr6+fn79/QIDNZrfbHQ6H0+l0uVxutzuKV1Qvj8cTTRHdK4ZXTEUsRWxFHEVcRTxFfFUCRUJVIlViVRJNUlWgKpkmuSaFJqUmlSa1Jo0urS6dJr0ugy6jLpMusyGLLqshmyFIl92Qw5TTkMuQ25THkNeUz5TfUMBS0FTIVNhUxFLUVMwSbCluKWEpaSllKc0oYynLKMcob6nAqMioxKjMqsKoyqjGqs6qwajJqsWqzarDqsupx6rPacBqyGnEacxpwmrKa8ZpzmnBaclrxWnNa8Nry2vHa8/rwOvI6yTozOsi6MrrJugu6CHoKegl6C3oIwgR9RX0E/UXDBCFigaKBokGi4aIhkqGiYZLRojCJCMlo0SjJWMkY2XjJOMlE2QTJZNkkyVTZFNl02TTZTNkM2WzZLMBc2RzAfNk8wELAAsBiwCLAUsASwHLAMshKwArIasAqyFrIGsh6yDrIRsgGyGbQJshWyBbQdtA2yE7QDtBu0C7QXtAe0H7YPtBB2AHQYdgh2FHQEdhx2DHYSdgJ2GnEKdhZxBnYecQ52EXEBcRlxCXEVcQVxHXEOGY64gbiJuYW5jbiDuYu5h7mPuYB5iHmEeYx6gnmKeoZ5jnqBeol5hXqNeoN6i3qHeo97gPqI+oT7jPqC+4CNxX3DfUd9wP3E/cr3/4jfvD8qH/0//p//R/+j/9n/5P/6f/0//p//R/+j/9n/5P/6f/0//p//R/+j/9n/5P/6f/0//p//R/+j/9n/7/3//fFhQY/he0/hwg"))
assert len(TABLE) == 0x3004


class ScannerError(Exception):
    pass


class Cancelled(Exception):
    pass


class PermissionProblem(ScannerError):
    def __init__(self):
        super().__init__("This user is not allowed to use the scanner's USB device yet.")


UDEV_RULE = ('# Colortrac SmartLF M-series scanner (Canon iPF MFP M40)\n'
             'SUBSYSTEM=="usb", ATTR{idVendor}=="148c", ATTR{idProduct}=="0060", '
             'MODE="0666", TAG+="uaccess"\n')
UDEV_PATH = "/etc/udev/rules.d/60-m40scan.rules"


def _noop(*a, **k):
    pass


class Scanner:
    def __init__(self, log=lambda m: print(m, flush=True)):
        self.dev = None
        self.log = log

    # --- low level -------------------------------------------------------
    def _out(self, req, data):
        self.dev.ctrl_transfer(OUT, req, 0, 0, data, timeout=5000)

    def _in(self, req, n):
        return bytes(self.dev.ctrl_transfer(IN, req, 0, 0, n, timeout=5000))

    def _read_chunk(self, n):
        buf = bytearray()
        for part in (n - n % 1024, n % 1024):
            got = 0
            while got < part:
                r = self.dev.read(EP_DATA_IN, part - got, timeout=20000)
                got += len(r)
                buf += r
        return bytes(buf)

    # --- connection ------------------------------------------------------
    def open(self, tries=15, cancel=None):
        """The scanner drops off USB and reconnects while it starts up, so retry."""
        last = None
        for _ in range(tries):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            dev = usb.core.find(idVendor=VID, idProduct=PID, backend=_BACKEND)
            if dev is None:
                last = "scanner not found on USB"
                self.log("Waiting for the scanner to appear on USB...")
            else:
                try:
                    try:
                        if dev.is_kernel_driver_active(0):
                            dev.detach_kernel_driver(0)
                    except (NotImplementedError, usb.core.USBError):
                        pass
                    try:
                        cfg = dev.get_active_configuration()
                    except usb.core.USBError:
                        cfg = None
                    if cfg is None or cfg.bConfigurationValue != 1:
                        dev.set_configuration(1)
                    usb.util.claim_interface(dev, 0)
                    self.dev = dev
                    self._in(0x7E, 12)
                    return
                except usb.core.USBError as e:
                    last = str(e)
                    if getattr(e, "errno", None) == 13:
                        raise PermissionProblem()
                    self.log(f"Scanner not ready yet ({e}), retrying...")
                    usb.util.dispose_resources(dev)
                    self.dev = None
            time.sleep(2)
        raise ScannerError(f"Could not open the scanner: {last}")

    def close(self):
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, 0)
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(self.dev)
            self.dev = None

    def paper_state(self):
        return self._in(0x7E, 12)[8]

    # --- scanning --------------------------------------------------------
    def wait_for_paper(self, cancel=None):
        while self.paper_state() != 3:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            time.sleep(0.2)

    def scan(self, margin_in=0.5, dpi=DPI, progress=_noop):
        """Scan the loaded page. Returns (width, lines, raw RGB bytes).
        progress(lines_done) is called after each chunk."""
        self._out(0x65, b"\x01")
        time.sleep(1.2)
        self._in(0x78, 48)
        self._out(0xC5, C5_BYTES)
        _, paper_w, paper_left = struct.unpack("<3I", self._in(0x7D, 12))
        self.log(f"Detected paper {paper_w / 600:.2f} in wide")
        margin = int(round(margin_in * 600))
        scale = dpi / 600
        width = int(round((paper_w + 2 * margin) * scale / 4)) * 4
        offset = max(0, int(round((paper_left - margin) * scale)))
        bpl = width * 3
        # SmartWorks reads ~0.5 MB at a time (96 lines at 200 dpi, letter width):
        # the largest multiple of 8 lines that fits in 512000 bytes.
        lines_per = 512000 // bpl
        lines_per = lines_per // 8 * 8 if lines_per >= 8 else max(1, lines_per)
        chunk = lines_per * bpl
        self._out(0x65, b"\x01")
        time.sleep(1.3)
        self._in(0x78, 48)
        self._out(0xC5, C5_BYTES)
        time.sleep(0.2)
        self._out(0xC5, C5_BYTES)
        time.sleep(0.2)

        self._out(0x64, struct.pack("<7I", width, FIELD1, offset, 0, dpi, dpi, 0))
        self._out(0x6F, struct.pack("<II", len(TABLE), 0x0A))
        self.dev.write(EP_DATA_OUT, TABLE, timeout=5000)
        self._out(0x67, struct.pack("<I", 1))
        self._out(0x88, struct.pack("<I", 0))
        self._out(0x68, b"\x01")
        self.log(f"Scanning {width / dpi:.2f} in wide at {dpi} dpi...")

        data = bytearray()
        try:
            while True:
                avail, state = struct.unpack("<II", self._in(0x71, 8))
                if state == 2 and avail >= chunk:
                    n = chunk
                elif state != 2 and avail > 0:
                    n = avail
                elif state != 2:
                    break
                else:
                    time.sleep(0.015)
                    continue
                self._out(0x70, struct.pack("<II", n, 1))
                data += self._read_chunk(n)
                progress(len(data) // bpl)
        finally:
            try:
                self._out(0x65, b"\x01")
            except usb.core.USBError:
                pass
        lines = len(data) // bpl
        return width, lines, bytes(data[: lines * bpl])
