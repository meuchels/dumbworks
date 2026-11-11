#!/usr/bin/env python3
"""Straighten and crop a scan from the SmartLF M scanner.

Finds the paper against the beige scanner backing, measures how crooked the
left and right edges are, rotates the image straight, then crops to the paper.

Usage: python3 m40_deskew.py in.png out.png
Needs: python3-numpy python3-pil
"""
import math
import sys

import numpy as np
from PIL import Image


def paper_mask(img):
    """True where the pixel looks like paper rather than scanner backing.
    The backing is beige (red well above blue); white paper is neutral and bright."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    warmth = a[:, :, 0] - a[:, :, 2]
    return (warmth < 12) & (a[:, :, 2] > 200)


def edges(mask, step=8):
    """For sampled rows, x of the first and last paper column (ignoring specks)."""
    h, w = mask.shape
    k = 15
    ys, lefts, rights = [], [], []
    for y in range(int(h * 0.04), int(h * 0.96), step):
        row = np.convolve(mask[y].astype(float), np.ones(k) / k, mode="same") > 0.8
        xs = np.flatnonzero(row)
        if len(xs) < w * 0.2:
            continue
        ys.append(y)
        lefts.append(xs[0])
        rights.append(xs[-1])
    return np.array(ys), np.array(lefts), np.array(rights), w


def fit_slope(ys, xs, lo, hi):
    """Robust slope dx/dy of one edge, skipping rows where the edge runs off the image."""
    ok = (xs > lo) & (xs < hi)
    if ok.sum() < 20:
        return None, 0
    y, x = ys[ok], xs[ok]
    for _ in range(3):
        m, c = np.polyfit(y, x, 1)
        r = np.abs(x - (m * y + c))
        keep = r < max(3.0, 3 * np.median(r))
        y, x = y[keep], x[keep]
    return m, len(y)


def content_angle(img, edge_guess=0.0):
    """Refine the skew using the page content: straight rows of text or table lines
    give the sharpest horizontal profile when the page is level."""
    g = img.convert("L")
    g.thumbnail((700, 700))
    best = (-1, edge_guess)
    def score(ang):
        r = np.asarray(g.rotate(-ang, resample=Image.BILINEAR, fillcolor=255)).astype(float)
        dark = (r < 110).sum(axis=1).astype(float)
        return np.sum(np.diff(dark) ** 2)
    for ang in np.arange(edge_guess - 3, edge_guess + 3.001, 0.1):
        best = max(best, (score(ang), ang))
    for ang in np.arange(best[1] - 0.1, best[1] + 0.101, 0.02):
        best = max(best, (score(ang), ang))
    return best[1], best[0]


BACKING = (200, 190, 175)


def rotate(img, angle, fill=BACKING):
    """Rotate by the measured skew (positive = page leans right going down)."""
    if abs(angle) < 0.005:
        return img
    return img.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=fill)


def analyze(img, verbose=False):
    """Measure skew and the paper's bounding box after straightening.
    Returns (angle_degrees, (left, top, right, bottom) as fractions of the rotated image).
    Works on a reduced copy so very wide scans stay quick."""
    small = img.convert("RGB")
    f = min(1.0, 1500 / max(small.size))
    if f < 1:
        small = small.resize((int(small.width * f), int(small.height * f)), Image.BILINEAR)
    mask = paper_mask(small)
    ys, L, R, w = edges(mask)
    slopes = []
    for name, xs in (("left", L), ("right", R)):
        m, n = fit_slope(ys, xs, 12, w - 12)
        if verbose:
            print(f"  {name} edge: " + (f"slope {m:+.4f} from {n} rows" if m is not None else "not usable"))
        if m is not None:
            slopes.append((n, m))
    angle = 0.0
    if slopes:
        m = sum(n * s for n, s in slopes) / sum(n for n, _ in slopes)
        angle = math.degrees(math.atan(m))
    c_ang, c_score = content_angle(small, angle)
    if verbose:
        print(f"  skew from paper edges {angle:+.2f} deg, from page content {c_ang:+.2f} deg")
    if c_score > 0:
        angle = c_ang
    rot = rotate(small, angle)
    mask = paper_mask(rot)
    rows = np.flatnonzero(mask.mean(axis=1) > 0.5)
    cols = np.flatnonzero(mask.mean(axis=0) > 0.5)
    box = (0.0, 0.0, 1.0, 1.0)
    if len(rows) > 10 and len(cols) > 10:
        W, H = rot.size
        box = ((cols[0] + 2) / W, (rows[0] + 2) / H, (cols[-1] - 1) / W, (rows[-1] - 1) / H)
    return round(float(angle), 2), box


def crop_frac(img, box):
    W, H = img.size
    l, t, r, b = box
    return img.crop((int(round(l * W)), int(round(t * H)), int(round(r * W)), int(round(b * H))))


def deskew(img, verbose=True):
    angle, box = analyze(img, verbose)
    out = crop_frac(rotate(img, angle), box)
    if verbose:
        print(f"  cropped to {out.size[0]} x {out.size[1]} px")
    return out, angle


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    im = Image.open(src)
    dpi = im.info.get("dpi", (200, 200))
    out, _ = deskew(im)
    out.save(dst, dpi=dpi)
    print("Saved", dst)
