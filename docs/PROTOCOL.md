# Colortrac SmartLF M-series USB protocol (Canon iPF MFP M40)

Worked out from SmartWorks MFP 4.3 (`SmartLF SC42 Com.dll`, Colortrac's WinUSB layer)
and USB captures of SmartWorks scanning. Unofficial; may be incomplete.

## Device

- USB `148c:0060`, bcdUSB 3.0, one interface, bulk endpoints `0x01` OUT, `0x82` IN, `0x83` IN.
- Windows binds Microsoft's generic `winusb.sys`; everything happens in user space.

## Transport

All commands are vendor control transfers addressed to an endpoint recipient,
`wValue = 0`, `wIndex = 0`, payload length in `wLength`:

- host → scanner: `bmRequestType = 0x42`, `bRequest = command`
- scanner → host: `bmRequestType = 0xC2`, `bRequest = command`

All multi-byte values are little-endian 32-bit unless noted.

## Commands seen

| Req  | Dir | Len | Meaning |
|------|-----|-----|---------|
| 0x7E | in  | 12  | Paper sensor. Byte 8: 0 = no paper, 2 = detected, 3 = loaded and ready. SmartWorks polls every 200 ms. |
| 0x65 | out | 1   | `01`: prepare paper before a scan / release it afterwards. |
| 0x78 | in  | 48  | Device information (starts `56 16 e0 00 34 00 …`). |
| 0xC5 | out | 8   | `b9 34 da 01 9d cd 7b ea`, sent verbatim (meaning unknown). |
| 0x7D | in  | 12  | Detected paper: `(2, width, left_edge)` in 600 dpi sensor pixels. |
| 0x64 | out | 28  | Scan setup, see below. |
| 0x6F | out | 8   | `(size, type)`; then `size` bytes on bulk EP `0x01`. Type `0x0A`, size `0x3004`: 3 × 4096-entry tone table + 4 bytes, sent before every scan. (Type `0x0B`, size `0x1004` also exists in the DLL.) |
| 0x67 | out | 4   | `1` before start. |
| 0x88 | out | 4   | `0` before start. |
| 0x68 | out | 1   | `01`: start scanning. |
| 0x71 | in  | 8   | `(bytes_buffered, state)`; state 2 = scanning, 1 = finished. |
| 0x70 | out | 8   | `(n_bytes, 1)`: request `n_bytes` of image data, then read them from bulk EP `0x83`. |

Other request codes handled by the DLL, not decoded: 0x66, 0x69, 0x6B, 0x72, 0x79, 0x7A, 0x7F,
0x85, 0x89, 0x8A, 0x91 (32-byte block), 0x96, 0x97, 0x98, 0xBF, 0xC0, 0xC1, 0xC3, 0xC4, 0xC6.
0xBF / 0xC0 upload 0x3004 / 0x1004 bytes via 0x6F.

## Scan setup (0x64), 7 dwords

| # | Value | Notes |
|---|-------|-------|
| 0 | width | pixels at scan dpi (multiple of 4) |
| 1 | 63000 | as SmartWorks sends; changing it had no visible effect at 200 dpi |
| 2 | offset | left edge of the window, pixels at scan dpi from the sensor origin |
| 3 | 0 | |
| 4 | x dpi | 100, 200, 300, 400, 600, 1200 offered by SmartWorks |
| 5 | y dpi | |
| 6 | 0 | low nibble appears to select colour mode |

SmartWorks sizes the window from 0x7D with a 0.1 in (60 sensor px) margin each side:
`width = (paper_w + 120) * dpi / 600`, `offset = (left_edge - 60) * dpi / 600`.
Verified: paper (5076, 8079) → width 1732, offset 2673 at 200 dpi.

## Reading the image

1. Send 0x68, then poll 0x71 (~every 15 ms).
2. While state = 2 and at least one chunk is buffered: 0x70 `(chunk, 1)`, then bulk-read `chunk` bytes from 0x83.
   SmartWorks reads the whole multiple of 1024 bytes first, then the short remainder, as two reads.
   At 200 dpi letter width the chunk is 96 lines (498 816 bytes) — about 0.5 MB.
3. When state becomes 1, request and read whatever is left, then send 0x65 `01`.

Data is 8-bit RGB, pixel-interleaved, top line first, already calibrated by the scanner.
The scan stops by itself at the paper's trailing edge.
