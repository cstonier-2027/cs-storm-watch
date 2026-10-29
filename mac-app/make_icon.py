"""Draw the Stormwatch app icon (radar scope with a storm cell) as a 1024px PNG."""
import math
import sys

from PIL import Image, ImageDraw, ImageFilter

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# Rounded-square tile with a deep night gradient (macOS icon grid: 824px tile, 100px margin).
tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    gd.line([(0, y), (S, y)], fill=(int(14 + 10 * t), int(30 + 8 * t), int(52 - 12 * t), 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([100, 100, 924, 924], radius=185, fill=255)
tile.paste(grad, (0, 0), mask)
img.alpha_composite(tile)

cx, cy, r = 512, 520, 330
d = ImageDraw.Draw(img)

# Storm cell: layered reflectivity blobs (green -> yellow -> red -> magenta core), blurred.
cell = Image.new("RGBA", (S, S), (0, 0, 0, 0))
cd = ImageDraw.Draw(cell)
for (dx, dy, rx, ry, col) in [
    (60, -10, 190, 150, (2, 200, 60, 230)), (80, -20, 140, 110, (250, 230, 40, 240)),
    (95, -28, 95, 75, (250, 120, 20, 245)), (105, -34, 60, 48, (230, 25, 35, 250)),
    (110, -38, 26, 22, (240, 0, 240, 255))]:
    cd.ellipse([cx + dx - rx, cy + dy - ry, cx + dx + rx, cy + dy + ry], fill=col)
cell = cell.filter(ImageFilter.GaussianBlur(14))
clip = Image.new("L", (S, S), 0)
ImageDraw.Draw(clip).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
img.paste(cell, (0, 0), Image.composite(cell.split()[3], Image.new("L", (S, S), 0), clip))

# Radar sweep wedge.
sweep = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(sweep)
for i in range(60):
    a0 = -70 + i * 0.9
    sd.pieslice([cx - r, cy - r, cx + r, cy + r], a0, a0 + 1.2, fill=(92, 200, 255, int(4 + i * 1.6)))
img.alpha_composite(sweep)

# Range rings and crosshair.
ring = (92, 200, 255, 150)
for rr in (r, r * 2 / 3, r / 3):
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=ring, width=10 if rr == r else 5)
d.line([cx - r, cy, cx + r, cy], fill=(92, 200, 255, 70), width=4)
d.line([cx, cy - r, cx, cy + r], fill=(92, 200, 255, 70), width=4)
a = math.radians(-70 + 60 * 0.9)
d.line([cx, cy, cx + r * math.cos(a), cy + r * math.sin(a)], fill=(170, 230, 255, 255), width=9)
d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=(170, 230, 255, 255))

img.save(sys.argv[1] if len(sys.argv) > 1 else "icon_1024.png")
