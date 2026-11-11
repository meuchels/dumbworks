#!/usr/bin/env python3
"""Read-only probe for the Colortrac SmartLF M-series (Canon iPF MFP M40) scanner.

Does only what the Windows driver does on connect: lists the USB endpoints,
reads the 48-byte device-info block (request 0x78) and one status poll
(request 0x71). Sends nothing that moves paper or changes settings.

Setup:  sudo apt install python3-usb   (or: pip install pyusb)
Run:    sudo python3 m40_probe.py
"""
import struct
import sys

import usb.core
import usb.util

VID, PID = 0x148C, 0x0060
REQ_IN = 0xC2  # vendor request, device-to-host, recipient = endpoint (as in SC42 Com.dll)


def hexdump(b):
    for i in range(0, len(b), 16):
        chunk = b[i:i + 16]
        print(f"  {i:04x}  {chunk.hex(' '):<48}  {''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)}")


dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    sys.exit("Scanner 148c:0060 not found. Is it powered on and plugged in?")

print(f"Found {VID:04x}:{PID:04x}  bus {dev.bus} addr {dev.address}  speed {dev.speed}")
for attr in ("manufacturer", "product", "serial_number"):
    try:
        print(f"  {attr}: {getattr(dev, attr)}")
    except Exception as e:
        print(f"  {attr}: <{e}>")

for cfg in dev:
    for intf in cfg:
        print(f"Interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting} class {intf.bInterfaceClass:#04x}")
        for ep in intf:
            kind = {0: "control", 1: "iso", 2: "bulk", 3: "interrupt"}[ep.bmAttributes & 3]
            print(f"  EP {ep.bEndpointAddress:#04x} {kind:9} maxpacket {ep.wMaxPacketSize}")

try:
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
except (NotImplementedError, usb.core.USBError):
    pass
dev.set_configuration()
usb.util.claim_interface(dev, 0)

try:
    info = bytes(dev.ctrl_transfer(REQ_IN, 0x78, 0, 0, 48, timeout=3000))
    print(f"\nDevice info (0x78), {len(info)} bytes:")
    hexdump(info)
    if len(info) >= 8:
        a, b = struct.unpack_from("<II", info)
        print(f"  first two dwords: {a} / {b}")
except usb.core.USBError as e:
    print(f"\n0x78 failed: {e}")

try:
    st = bytes(dev.ctrl_transfer(REQ_IN, 0x71, 0, 0, 8, timeout=3000))
    avail, flags = struct.unpack("<II", st)
    print(f"\nStatus (0x71): bytes available {avail}, flags {flags:#x}")
except usb.core.USBError as e:
    print(f"\n0x71 failed: {e}")

usb.util.release_interface(dev, 0)
usb.util.dispose_resources(dev)
