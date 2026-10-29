"""Build the static basemap for Stormwatch: shaded terrain + ocean depth, lakes, rivers,
roads, cities and neighboring-country outlines. Run once; the weather refresh doesn't need it.

Sources (public domain / open):
  Elevation + bathymetry: AWS Terrain Tiles (Terrarium) - USGS NED/SRTM/GMTED on land, NOAA ETOPO1 offshore.
  Vectors: Natural Earth 1:10m.
"""
import concurrent.futures as cf
import io
import json
import math
import os
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "base")
CACHE = os.path.join(HERE, ".cache")
R = 6378137.0
# Basemap reaches past the weather-data frame (-126..-66 lon, ~24..50 lat) so the edges
# of the view always show real land and ocean instead of empty space.
_m = lambda lon, lat: (R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))
X0, Y0 = _m(-134, 16)
X1, Y1 = _m(-58, 56)
TW = 5200
TH = round(TW * (Y1 - Y0) / (X1 - X0))
Z = 7
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
LON0, LON1 = math.degrees(X0 / R), math.degrees(X1 / R)
LAT0 = math.degrees(2 * math.atan(math.exp(Y0 / R)) - math.pi / 2)
LAT1 = math.degrees(2 * math.atan(math.exp(Y1 / R)) - math.pi / 2)


def get(url, binary=True):
    req = urllib.request.Request(url, headers={"User-Agent": "Stormwatch basemap builder"})
    with urllib.request.urlopen(req, timeout=180) as r:
        b = r.read()
    return b if binary else b.decode()


def cached_json(name):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if not os.path.exists(p):
        open(p, "wb").write(get(NE + name))
    return json.load(open(p))


def merc_px(lon, lat):
    x = R * np.radians(lon)
    y = R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    return (x - X0) / (X1 - X0) * TW, (Y1 - y) / (Y1 - Y0) * TH


# ---------------------------------------------------------------- elevation
def elevation():
    world = 256 * 2 ** Z
    to_px = lambda X: (X + math.pi * R) / (2 * math.pi * R) * world
    px0, px1 = to_px(X0), to_px(X1)
    py0, py1 = world - to_px(Y1), world - to_px(Y0)
    tx0, tx1, ty0, ty1 = int(px0 // 256), int(px1 // 256), int(py0 // 256), int(py1 // 256)

    def tile(xy):
        x, y = xy
        b = get(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{Z}/{x}/{y}.png")
        a = np.asarray(Image.open(io.BytesIO(b)).convert("RGB")).astype(np.float32)
        return xy, a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768

    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    with cf.ThreadPoolExecutor(8) as ex:
        for (x, y), e in ex.map(tile, [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]):
            mosaic[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = e
    crop = mosaic[int(py0 - ty0 * 256):int(py1 - ty0 * 256), int(px0 - tx0 * 256):int(px1 - tx0 * 256)]
    img = Image.fromarray(crop, "F").resize((TW, TH), Image.BILINEAR)
    return np.asarray(img)


def land_mask(countries, lakes):
    m = Image.new("L", (TW, TH), 0)
    d = ImageDraw.Draw(m)

    def polys(g):
        return g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]

    for f in countries["features"]:
        for poly in polys(f["geometry"]):
            for k, ring in enumerate(poly):
                a = np.array(ring)
                if a[:, 0].max() < LON0 - 5 or a[:, 0].min() > LON1 + 5 or a[:, 1].max() < LAT0 - 5 or a[:, 1].min() > LAT1 + 5:
                    continue
                x, y = merc_px(a[:, 0], np.clip(a[:, 1], -85, 85))
                d.polygon(list(zip(x, y)), fill=255 if k == 0 else 0)
    for f in lakes["features"]:
        for poly in polys(f["geometry"]):
            a = np.array(poly[0])
            x, y = merc_px(a[:, 0], a[:, 1])
            d.polygon(list(zip(x, y)), fill=0)
    return np.asarray(m) > 127


def ramp(v, stops):
    """Linear color ramp: stops = [(value, (r,g,b)), ...] ascending."""
    xs = np.array([s[0] for s in stops], np.float32)
    out = np.zeros(v.shape + (3,), np.float32)
    for c in range(3):
        out[..., c] = np.interp(v, xs, [s[1][c] for s in stops])
    return out


def terrain(elev, land):
    # Hillshade (NW light, 45 deg). Pixel size ~ 1.5 km at mid-latitudes; exaggerate for legibility.
    lat = np.degrees(2 * np.arctan(np.exp((Y1 - (np.arange(TH) + .5) / TH * (Y1 - Y0)) / R)) - np.pi / 2)
    px_m = (X1 - X0) / TW * np.cos(np.radians(lat))[:, None]
    gy, gx = np.gradient(elev)
    zf = np.where(land, 2.2, 1.0)
    dzdx, dzdy = gx / px_m * zf, -gy / px_m * zf
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    az, alt = np.radians(315), np.radians(45)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - (np.pi / 2 - aspect))
    shade = np.clip(shade, 0, 1)

    # Muted night-mode hypsometric tint so weather layers stay dominant.
    land_c = ramp(np.clip(elev, -100, 4300), [
        (-100, (44, 58, 52)), (0, (46, 62, 52)), (300, (54, 70, 56)), (800, (70, 78, 60)),
        (1500, (88, 84, 68)), (2200, (102, 92, 80)), (3000, (118, 112, 106)), (4300, (150, 150, 152))])
    sea_c = ramp(np.clip(-elev, 0, 6000), [
        (0, (40, 74, 96)), (50, (32, 64, 88)), (200, (24, 52, 78)), (1000, (18, 40, 66)),
        (3000, (13, 30, 54)), (6000, (9, 20, 40))])
    base = np.where(land[..., None], land_c, sea_c)
    k = np.where(land, 0.45 + 0.95 * shade, 0.72 + 0.5 * shade)[..., None]
    rgb = np.clip(base * k, 0, 255).astype(np.uint8)
    # Soft coastline glow on the water side.
    edge = Image.fromarray((land * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    glow = (np.asarray(edge).astype(np.float32) / 255)[..., None] * (~land)[..., None]
    rgb = np.clip(rgb + glow * np.array([22, 40, 48]), 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


# ---------------------------------------------------------------- vectors
def in_bbox(coords, pad=2):
    a = np.array(coords)
    return not (a[:, 0].max() < LON0 - pad or a[:, 0].min() > LON1 + pad or a[:, 1].max() < LAT0 - pad or a[:, 1].min() > LAT1 + pad)


def rnd(coords, nd=3):
    return [[round(x, nd), round(y, nd)] for x, y in coords]


def lines(fc, keep, props):
    out = []
    for f in fc["features"]:
        g, p = f["geometry"], f["properties"]
        if not g or not keep(p):
            continue
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        parts = [rnd(c) for c in parts if len(c) > 1 and in_bbox(c)]
        if parts:
            out.append({"type": "Feature", "properties": props(p),
                        "geometry": {"type": "MultiLineString", "coordinates": parts}})
    return {"type": "FeatureCollection", "features": out}


def main():
    os.makedirs(OUT, exist_ok=True)
    countries = cached_json("ne_10m_admin_0_countries_lakes.geojson")
    lakes = cached_json("ne_10m_lakes.geojson")
    lakes_na = {"type": "FeatureCollection", "features": [
        f for f in lakes["features"] if f["geometry"] and in_bbox(
            (f["geometry"]["coordinates"][0][0] if f["geometry"]["type"] == "MultiPolygon" else f["geometry"]["coordinates"][0]))
        and (f["properties"].get("scalerank") or 9) <= 6]}

    print("elevation…")
    elev = elevation()
    land = land_mask(countries, lakes_na)
    terrain(elev, land).save(os.path.join(OUT, "terrain.jpg"), quality=84, optimize=True, progressive=True)
    print("terrain.jpg", TW, TH)

    # Lakes (rounded polygons).
    lk = []
    for f in lakes_na["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        lk.append({"type": "Feature", "properties": {"name": f["properties"].get("name")},
                   "geometry": {"type": "MultiPolygon", "coordinates": [[rnd(r) for r in p] for p in polys]}})
    json.dump({"type": "FeatureCollection", "features": lk}, open(os.path.join(OUT, "lakes.json"), "w"), separators=(",", ":"))

    rivers = cached_json("ne_10m_rivers_lake_centerlines.geojson")
    json.dump(lines(rivers, lambda p: (p.get("scalerank") or 9) <= 7 and p.get("featurecla") != "Lake Centerline",
                    lambda p: {"r": p.get("scalerank"), "n": p.get("name")}),
              open(os.path.join(OUT, "rivers.json"), "w"), separators=(",", ":"))

    roads = cached_json("ne_10m_roads.geojson")
    kinds = {"Major Highway": 1, "Beltway": 1, "Bypass": 2, "Secondary Highway": 2}
    json.dump(lines(roads, lambda p: p.get("continent") == "North America" and p.get("type") in kinds,
                    lambda p: {"k": kinds[p["type"]], "x": p.get("expressway") or 0,
                               "n": (p.get("label") or p.get("name") or "")[:16], "z": p.get("min_zoom")}),
              open(os.path.join(OUT, "roads.json"), "w"), separators=(",", ":"))

    places = cached_json("ne_10m_populated_places_simple.geojson")
    cities = []
    for f in places["features"]:
        p = f["properties"]
        lon, lat = p["longitude"], p["latitude"]
        if not (LON0 <= lon <= LON1 and LAT0 <= lat <= LAT1):
            continue
        cities.append({"n": p["name"], "pop": p.get("pop_max") or 0, "r": p.get("scalerank"),
                       "cap": 1 if p.get("adm0cap") else 2 if (p.get("featurecla") or "").startswith("Admin-1 capital") else 0,
                       "st": p.get("adm1name"), "c": p.get("adm0_a3"), "lon": round(lon, 3), "lat": round(lat, 3)})
    cities.sort(key=lambda c: -c["pop"])
    json.dump(cities, open(os.path.join(OUT, "cities.json"), "w"), separators=(",", ":"))

    # Neighboring land (Canada, Mexico, Caribbean) for the flat basemap.
    nb = []
    for f in countries["features"]:
        if f["properties"].get("ADM0_A3") == "USA":
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        polys = [[rnd(r, 2) for r in p] for p in polys if in_bbox(p[0], 1) and len(p[0]) > 6]
        if polys:
            nb.append({"type": "Feature", "properties": {"n": f["properties"].get("NAME")},
                       "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    json.dump({"type": "FeatureCollection", "features": nb}, open(os.path.join(OUT, "neighbors.json"), "w"), separators=(",", ":"))
    json.dump({"x0": X0, "x1": X1, "y0": Y0, "y1": Y1}, open(os.path.join(OUT, "terrain.json"), "w"))
    for fn in sorted(os.listdir(OUT)):
        print(fn, os.path.getsize(os.path.join(OUT, fn)) // 1024, "KB")


if __name__ == "__main__":
    main()
