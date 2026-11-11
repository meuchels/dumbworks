"""Image adjustments for the scan preview, modelled on SmartWorks MFP's panel:
brightness, white point, black point, sharpening, deskew, crop, invert, mirror."""
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps

from m40_deskew import crop_frac, rotate

LIMITS = {
    "brightness": (-10, 10),
    "white": (0, 10),
    "black": (0, 10),
    "sharpen": (0, 5),
}


@dataclass
class Adjust:
    brightness: int = 0     # -10..10, gamma curve
    white: int = 1          # 0..10, clip the brightest (level x 6) values to white
    black: int = 5          # 0..10, clip the darkest (level x 6) values to black
    sharpen: int = 2        # 0..5, unsharp mask strength
    angle: float = 0.0      # degrees of rotation to straighten
    box: tuple = (0.0, 0.0, 1.0, 1.0)   # crop, fractions of the rotated image
    invert: bool = False
    mirror: bool = False
    auto: dict = field(default_factory=dict)  # what auto-straighten found, for Reset


def tone_lut(a):
    bp = a.black * 6
    wp = 255 - a.white * 6
    gamma = 0.92 ** a.brightness
    span = max(1, wp - bp)
    lut = []
    for v in range(256):
        x = min(1.0, max(0.0, (v - bp) / span))
        y = int(round(255 * (x ** gamma)))
        lut.append(255 - y if a.invert else y)
    return lut


def geometry(img, a):
    """Rotate, mirror and crop: the steps that change the image's shape."""
    out = rotate(img, a.angle, fill=(255, 255, 255))
    if a.mirror:
        out = ImageOps.mirror(out)
    if a.box != (0.0, 0.0, 1.0, 1.0):
        out = crop_frac(out, a.box)
    return out


def tone(img, a, scale=1.0, dpi=200):
    """Levels, brightness, sharpening and invert. scale < 1 for a reduced preview."""
    out = img.convert("RGB")
    if a.sharpen:
        out = out.filter(ImageFilter.UnsharpMask(radius=max(0.6, 1.2 * scale * dpi / 200),
                                                 percent=45 * a.sharpen, threshold=2))
    return out.point(tone_lut(a) * 3)


def apply(img, a, dpi=200):
    return tone(geometry(img, a), a, dpi=dpi)
