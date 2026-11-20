"""Build the app icon: the user's sun-and-cloud badge with a thicker tartan ring on a deep navy background.

Input: icon-source.png (the original badge, completed to a 1600 px square). Output: icon-full.png (square, for
phones and the web) and icon-mac-1024.png (round badge on transparent, sized to the macOS icon grid).
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
N, C = 1600, 799.5                      # canvas size and center
R_OUT = 795                             # outer edge of the badge
R_CENTER = 545                          # outer edge of the gold-ringed cream center (was ~680)
BG = (9, 19, 31)                        # deep navy behind the badge, matching the tartan

def tartan(n, period=210):
    """Navy tartan: blue squares at every crossing, white double pinstripes, fine diagonal weave."""
    y, x = np.mgrid[0:n, 0:n]
    img = np.zeros((n, n, 3), np.float32)
    img[:] = (17, 29, 42)
    sq = lambda v: (v % period) < period * 0.5
    blue = sq(x + 5) & sq(y + 5)
    img[blue] = (55, 99, 132)
    band = sq(x + 5) ^ sq(y + 5)                      # half-tone where one stripe crosses the base
    img[band] = (25, 42, 58)
    for off in (0.62, 0.66):                          # white double pinstripes
        p = int(period * off)
        for v in (x, y):
            line = (v % period >= p) & (v % period < p + 3)
            img[line] = (226, 232, 238)
            img[((v % period) == p + 3)] = (120, 135, 150)
    weave = ((x + y) % 5 == 0)
    img[weave] = np.clip(img[weave] * 0.82, 0, 255)  # twill texture
    return Image.fromarray(img.astype(np.uint8))

def dashed_circle(draw, r, color, width, dash_deg, gap_deg):
    a = 0.0
    while a < 360:
        draw.arc([C - r, C - r, C + r, C + r], a, min(a + dash_deg, 360), fill=color, width=width)
        a += dash_deg + gap_deg

GRAPHIC_SCALE = 1.6                     # sun and cloud size inside the cream center (1 = original)


def enlarge_graphic(src):
    """Lift the sun-and-cloud off the cream center, fill the gap with cream, and put it back larger and centered."""
    a = np.asarray(src.convert("RGB")).astype(np.float32)
    cream = np.array([243, 233, 215], np.float32)
    yy, xx = np.mgrid[0:N, 0:N]
    inside = (xx - C) ** 2 + (yy - C) ** 2 < 600 ** 2            # within the inner dashed stitch
    mask = (np.abs(a - cream).sum(axis=2) > 40) & inside
    m = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))
    hole = np.asarray(m) > 0
    # Fill the hole with the surrounding cream (normalized blur).
    keep = (~hole).astype(np.float32)
    def blur(arr, r=40, passes=3):                              # repeated box blur ~ Gaussian
        for _ in range(passes):
            for ax in (0, 1):
                c = np.cumsum(np.pad(arr, [(r + 1, r) if i == ax else (0, 0) for i in range(2)], mode="edge"), axis=ax)
                arr = (np.take(c, range(2 * r + 1, c.shape[ax]), axis=ax) - np.take(c, range(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
        return arr
    w = blur(keep)
    filled = a.copy()
    for c in range(3):
        filled[..., c] = np.where(hole, blur(a[..., c] * keep) / np.maximum(w, 1e-3), a[..., c])
    base = Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8)).convert("RGBA")
    # The graphic with a soft edge, scaled about its own center and re-centered on the badge.
    ys, xs = np.nonzero(hole)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    g = src.crop((x0, y0, x1, y1)).copy()
    g.putalpha(m.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(1.5)))
    g = g.resize((int(g.width * GRAPHIC_SCALE), int(g.height * GRAPHIC_SCALE)), Image.LANCZOS)
    base.alpha_composite(g, (int(C - g.width / 2), int(C - g.height / 2 + 10)))
    return base


def main():
    src = enlarge_graphic(Image.open(os.path.join(HERE, "icon-source.png")).convert("RGBA"))
    out = Image.new("RGBA", (N, N), BG + (255,))

    # Tartan ring with a soft dark rim and inner shadow.
    ring = tartan(N).convert("RGBA")
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).ellipse([C - R_OUT, C - R_OUT, C + R_OUT, C + R_OUT], fill=255)
    out.paste(ring, (0, 0), mask)
    shade = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(shade)
    d.ellipse([C - R_OUT, C - R_OUT, C + R_OUT, C + R_OUT], outline=150, width=22)
    d.ellipse([C - R_CENTER - 30, C - R_CENTER - 30, C + R_CENTER + 30, C + R_CENTER + 30], outline=170, width=40)
    shade = shade.filter(ImageFilter.GaussianBlur(14))
    dark = Image.new("RGBA", (N, N), (4, 10, 18, 0)); dark.putalpha(Image.fromarray((np.asarray(shade) * (np.asarray(mask) > 0)).astype(np.uint8)))
    out.alpha_composite(dark)
    d = ImageDraw.Draw(out)
    dashed_circle(d, R_OUT - 38, (222, 192, 112, 255), 9, 4.2, 2.6)          # outer gold stitching
    dashed_circle(d, R_CENTER + 42, (222, 192, 112, 255), 7, 3.6, 2.6)       # inner gold stitching

    # Gold-ringed cream center, taken from the original and scaled down.
    r_src = 668                         # just inside the original dark band, so none of the old tartan shows
    center = src.crop((int(C - r_src), int(C - r_src), int(C + r_src), int(C + r_src)))
    cm = Image.new("L", center.size, 0)
    ImageDraw.Draw(cm).ellipse([0, 0, center.size[0] - 1, center.size[1] - 1], fill=255)
    center.putalpha(cm)
    size = 2 * R_CENTER
    center = center.resize((size, size), Image.LANCZOS)
    out.alpha_composite(center, (int(C - R_CENTER), int(C - R_CENTER)))

    out.convert("RGB").save(os.path.join(HERE, "icon-full.png"), optimize=True)
    badge = out.copy()
    bm = Image.new("L", (N, N), 0)
    ImageDraw.Draw(bm).ellipse([C - R_OUT, C - R_OUT, C + R_OUT, C + R_OUT], fill=255)
    badge.putalpha(bm)
    mac = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    mac.alpha_composite(badge.resize((860, 860), Image.LANCZOS), (82, 82))
    mac.save(os.path.join(HERE, "icon-mac-1024.png"), optimize=True)

if __name__ == "__main__":
    main()
