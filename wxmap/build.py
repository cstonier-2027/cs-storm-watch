"""Fetch NWS / SPC / NOAA data and render map layers for the CS's Storm Watch page.

Run:  <venv>/bin/python build.py
Writes everything into ./data next to this file.
"""
import concurrent.futures as cf
import base64
import csv
import gzip
import datetime as dt
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

import eccodes
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
UA = "CS Storm Watch map builder (personal, non-commercial)"

# Web Mercator frame shared by every raster and the page's projection.
R = 6378137.0
X0, X1, Y0, Y1 = -14026255.0, -7347086.0, 2753408.0, 6446276.0
W, H = 1400, 774          # model rasters
RW, RH = 2000, 1106       # radar frames
PROBE = 5                 # probe grid downsample factor
PW, PH = W // PROBE, H // PROBE

RADAR_FRAMES = 12         # most recent hour at 5 min
HRRR_HOURS = 19           # cycle length; only hours valid now or later are used


def get(url, binary=False, headers=None, timeout=90, tries=4):
    h = {"User-Agent": UA}
    h.update(headers or {})
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
            return b if binary else b.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(3 * (attempt + 1))


def save(name, data):
    p = os.path.join(OUT, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(p, mode) as f:
        f.write(data)


# ---------------------------------------------------------------- vectors
def build_alerts():
    d = json.loads(get("https://api.weather.gov/alerts/active?status=actual",
                       headers={"Accept": "application/geo+json"}))
    out = []
    for f in d["features"]:
        p = f["properties"]
        same = p.get("geocode", {}).get("SAME", []) or []
        fips = sorted({s[1:] for s in same if len(s) == 6 and s[1:3] != "00" and int(s[1:3]) <= 78})
        out.append({
            "id": p["id"], "event": p["event"], "severity": p.get("severity"),
            "certainty": p.get("certainty"), "urgency": p.get("urgency"),
            "headline": p.get("headline"), "area": p.get("areaDesc"),
            "sent": p.get("sent"), "onset": p.get("onset"), "ends": p.get("ends") or p.get("expires"),
            "sender": p.get("senderName"), "desc": p.get("description"),
            "instr": p.get("instruction"), "fips": fips, "ugc": p.get("geocode", {}).get("UGC", []),
            "geom": f.get("geometry"),
        })
    return out


def build_spc():
    base = "https://www.spc.noaa.gov/products/outlook/"
    names = ["day1otlk_cat", "day1otlk_torn", "day1otlk_sigtorn", "day1otlk_wind",
             "day1otlk_sigwind", "day1otlk_hail", "day1otlk_sighail",
             "day2otlk_cat", "day3otlk_cat"]
    res = {}
    for n in names:
        try:
            res[n] = json.loads(get(base + n + ".lyr.geojson"))
        except Exception as e:  # sig layers are 404 when absent
            res[n] = {"type": "FeatureCollection", "features": [], "error": str(e)}
    return res


def build_reports():
    txt = get("https://www.spc.noaa.gov/climo/reports/today_filtered.csv")
    kind, rows = None, []
    for line in txt.splitlines():
        if line.startswith("Time,"):
            kind = {"F_Scale": "torn", "Speed": "wind", "Size": "hail"}[line.split(",")[1]]
            continue
        if not line.strip():
            continue
        r = next(csv.reader([line]))
        try:
            rows.append({"t": kind, "time": r[0], "mag": r[1], "loc": r[2], "county": r[3],
                         "st": r[4], "lat": float(r[5]), "lon": float(r[6]), "txt": r[7]})
        except (ValueError, IndexError):
            pass
    return rows


def build_metars():
    d = json.loads(get("https://aviationweather.gov/api/data/metar?bbox=23,-127,51,-65&format=geojson"))
    obs = []
    for f in d["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        obs.append({"id": p.get("id"), "site": p.get("site"), "lat": lat, "lon": lon,
                    "t": p.get("temp"), "td": p.get("dewp"), "wd": p.get("wdir"), "ws": p.get("wspd"),
                    "wg": p.get("wgst"), "slp": p.get("slp") or p.get("altim"), "vis": p.get("visib"),
                    "cvr": p.get("cover"), "wx": p.get("wx"), "obs": p.get("obsTime"), "raw": p.get("rawOb")})
    return obs


def build_watches_mds():
    w = json.loads(get("https://mesonet.agron.iastate.edu/json/spcwatch.py"))
    m = json.loads(get("https://mesonet.agron.iastate.edu/api/1/nws/spc_mcd.geojson"))
    return w, m


# ---------------------------------------------------------------- radar
def wms_bbox():
    return f"{X0:.0f},{Y0:.0f},{X1:.0f},{Y1:.0f}"


def build_radar(now):
    end = now - dt.timedelta(minutes=now.minute % 5 + 5, seconds=now.second, microseconds=now.microsecond)
    times = [end - dt.timedelta(minutes=5 * i) for i in range(RADAR_FRAMES)][::-1]

    def one(it):
        i, t = it
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q-t.cgi?SERVICE=WMS&VERSION=1.1.1"
               f"&REQUEST=GetMap&LAYERS=nexrad-n0q-wmst&STYLES=&SRS=EPSG:3857&BBOX={wms_bbox()}"
               f"&WIDTH={RW}&HEIGHT={RH}&FORMAT=image/png&TRANSPARENT=true&TIME={t:%Y-%m-%dT%H:%M:00Z}")
        b = get(url, binary=True)
        name = f"radar/frame_{i:02d}.png"  # stable names so republishes replace files
        save(name, b)
        return {"t": t.strftime("%Y-%m-%dT%H:%MZ"), "src": "data/" + name, "bytes": len(b)}

    with cf.ThreadPoolExecutor(6) as ex:
        frames = list(ex.map(one, enumerate(times)))
    return frames


SAT_FRAMES = 5  # last hour at 15-min cadence


def goes_times(now):
    """Most recent GOES-East CONUS scan times in the IEM archive (checks today and yesterday)."""
    found = []
    for back in (0, 1):
        d = now - dt.timedelta(days=back)
        try:
            html = get(f"https://mesonet.agron.iastate.edu/archive/data/{d:%Y/%m/%d}/GIS/sat/")
        except Exception:
            continue
        for hhmm in sorted(set(re.findall(r"conus_goes_ir4km_(\d{4})\.tif", html))):
            found.append(d.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=0, microsecond=0))
    return sorted(found)[-SAT_FRAMES:]


def sat_index():
    """Map each output pixel to the archive's WGS84 grid (-130W..-60W, 52N..22N, 0.04 deg)."""
    xs = X0 + (np.arange(W) + 0.5) * (X1 - X0) / W
    ys = Y1 - (np.arange(H) + 0.5) * (Y1 - Y0) / H
    lon = np.degrees(xs / R)
    lat = np.degrees(2 * np.arctan(np.exp(ys / R)) - np.pi / 2)
    col = np.floor((lon + 130) / 0.04).astype(int)
    row = np.floor((52 - lat) / 0.04).astype(int)
    LON_I, LAT_I = np.meshgrid(col, row)
    valid = (LON_I >= 0) & (LON_I < 1750) & (LAT_I >= 0) & (LAT_I < 750)
    return np.clip(LAT_I, 0, 749), np.clip(LON_I, 0, 1749), valid


def ir_kelvin(c):
    # IEM 8-bit IR count to brightness temperature (GINI convention).
    c = c.astype(np.float32)
    return np.where(c <= 176, 330 - c / 2, 418 - c)


def ramp(v, stops):
    xs = np.array([s[0] for s in stops], np.float32)
    out = np.zeros(v.shape + (4,), np.float32)
    for k in range(4):
        out[..., k] = np.interp(v, xs, [s[1][k] for s in stops])
    return out.astype(np.uint8)


def build_satellite(now):
    os.makedirs(os.path.join(OUT, "sat"), exist_ok=True)
    times = goes_times(now)
    jj, ii, valid = sat_index()
    frames = []
    for i, t in enumerate(times):
        base = f"https://mesonet.agron.iastate.edu/archive/data/{t:%Y/%m/%d}/GIS/sat/conus_goes_%s4km_{t:%H%M}.tif"
        ir = np.asarray(Image.open(io.BytesIO(get(base % "ir", binary=True))))[jj, ii]
        tk = ir_kelvin(ir)
        # Natural clouds: transparent over warm ground, thickening to bright white for cold, high tops.
        clouds = ramp(tk, [(205, (255, 255, 255, 245)), (235, (246, 248, 252, 225)), (255, (228, 233, 240, 175)),
                           (262, (205, 212, 222, 95)), (270, (190, 198, 210, 30)), (276, (180, 190, 200, 0))])
        # Enhanced IR: colors the coldest tops (strong storms) on top of the white cloud field.
        enh = ramp(tk, [(185, (250, 250, 250, 245)), (195, (150, 40, 160, 245)), (205, (220, 30, 40, 245)),
                        (215, (250, 140, 30, 245)), (225, (245, 225, 60, 240)), (235, (60, 200, 110, 230)),
                        (245, (60, 140, 230, 220)), (255, (200, 210, 225, 160)), (270, (170, 180, 195, 70)),
                        (282, (160, 170, 185, 0))])
        out = {"t": t.strftime("%Y-%m-%dT%H:%MZ")}
        for name, img in (("ir", clouds), ("enh", enh)):
            img[~valid] = 0
            feather(img)
            fn = f"sat/{name}_{i:02d}.png"
            Image.fromarray(img, "RGBA").save(os.path.join(OUT, fn), optimize=True)
            out[name] = "data/" + fn
        vis = np.asarray(Image.open(io.BytesIO(get(base % "vis", binary=True))))[jj, ii].astype(np.float32)
        if np.percentile(vis[valid], 75) > 45:  # daylight over enough of the map
            a = np.clip((vis - 70) / 110, 0, 1)
            img = np.zeros((H, W, 4), np.uint8)
            g = np.clip(150 + vis * 0.45, 0, 255)
            img[..., 0] = img[..., 1] = img[..., 2] = g.astype(np.uint8)
            img[..., 3] = (a ** 0.8 * 240).astype(np.uint8)
            img[~valid] = 0
            feather(img)
            fn = f"sat/vis_{i:02d}.png"
            Image.fromarray(img, "RGBA").save(os.path.join(OUT, fn), optimize=True)
            out["vis"] = "data/" + fn
        frames.append(out)
    return frames


# ---------------------------------------------------------------- HRRR
FIELDS = [  # (key in idx line, short name)
    ("REFC:entire atmosphere", "refc"),
    ("CAPE:surface", "sbcape"),
    ("CIN:surface", "sbcin"),
    ("CAPE:90-0 mb above ground", "mlcape"),
    ("CIN:90-0 mb above ground", "mlcin"),
    ("HLCY:1000-0 m above ground", "srh1"),
    ("HLCY:3000-0 m above ground", "srh3"),
    ("VUCSH:0-6000 m above ground", "ushr6"),
    ("VVCSH:0-6000 m above ground", "vshr6"),
    ("HGT:level of adiabatic condensation from sfc", "lcl"),
    ("MXUPHL:5000-2000 m above ground", "uh"),
    ("TMP:2 m above ground", "t2"),
    ("DPT:2 m above ground", "td2"),
]


def latest_hrrr_cycle(now):
    for back in range(1, 6):
        c = now - dt.timedelta(hours=back)
        d = c.strftime("%Y%m%d")
        url = (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/hrrr.{d}/conus/"
               f"hrrr.t{c:%H}z.wrfsfcf{HRRR_HOURS - 1:02d}.grib2.idx")
        try:
            get(url)
            return c.replace(minute=0, second=0, microsecond=0)
        except Exception:
            continue
    raise RuntimeError("no HRRR cycle found")


def fetch_hrrr_fields(cycle, fh):
    base = (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/hrrr.{cycle:%Y%m%d}/conus/"
            f"hrrr.t{cycle:%H}z.wrfsfcf{fh:02d}.grib2")
    idx = get(base + ".idx").splitlines()
    starts = [(int(l.split(":")[1]), l) for l in idx]
    out = {}
    for key, name in FIELDS:
        for i, (off, line) in enumerate(starts):
            parts = line.split(":")
            if f"{parts[3]}:{parts[4]}" == key:
                end = starts[i + 1][0] - 1 if i + 1 < len(starts) else ""
                b = get(base, binary=True, headers={"Range": f"bytes={off}-{end}"}, timeout=180)
                gid = eccodes.codes_new_from_message(b)
                try:
                    nx = eccodes.codes_get(gid, "Nx"); ny = eccodes.codes_get(gid, "Ny")
                    vals = eccodes.codes_get_values(gid).reshape(ny, nx)
                    if "grid" not in out:
                        out["grid"] = {k: eccodes.codes_get(gid, k) for k in [
                            "Nx", "Ny", "DxInMetres", "Latin1InDegrees", "Latin2InDegrees", "LoVInDegrees",
                            "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                            "jScansPositively"]}
                finally:
                    eccodes.codes_release(gid)
                out[name] = vals
                break
    return out


def lcc_index(grid):
    """Map every output pixel (Web Mercator) to the nearest HRRR grid cell."""
    Re = 6371229.0
    lat1 = math.radians(grid["Latin1InDegrees"])
    lov = math.radians(grid["LoVInDegrees"] - 360 if grid["LoVInDegrees"] > 180 else grid["LoVInDegrees"])
    n = math.sin(lat1)
    F = math.cos(lat1) * math.tan(math.pi / 4 + lat1 / 2) ** n / n
    rho0 = Re * F / math.tan(math.pi / 4 + lat1 / 2) ** n

    def fwd(lat, lon):
        rho = Re * F / np.tan(np.pi / 4 + lat / 2) ** n
        th = n * (lon - lov)
        return rho * np.sin(th), rho0 - rho * np.cos(th)

    la0 = math.radians(grid["latitudeOfFirstGridPointInDegrees"])
    lo0 = grid["longitudeOfFirstGridPointInDegrees"]
    lo0 = math.radians(lo0 - 360 if lo0 > 180 else lo0)
    x0, y0 = fwd(np.array(la0), np.array(lo0))
    xs = X0 + (np.arange(W) + 0.5) * (X1 - X0) / W
    ys = Y1 - (np.arange(H) + 0.5) * (Y1 - Y0) / H
    lon = xs / R
    lat = 2 * np.arctan(np.exp(ys / R)) - np.pi / 2
    LON, LAT = np.meshgrid(lon, lat)
    x, y = fwd(LAT, LON)
    dx = grid["DxInMetres"]
    i = np.rint((x - x0) / dx).astype(int)
    j = np.rint((y - y0) / dx).astype(int)
    valid = (i >= 0) & (i < grid["Nx"]) & (j >= 0) & (j < grid["Ny"])
    return np.clip(j, 0, grid["Ny"] - 1), np.clip(i, 0, grid["Nx"] - 1), valid


def derive(f):
    shr6 = np.hypot(f["ushr6"], f["vshr6"]) * 1.943844  # kt
    shr6_ms = shr6 / 1.943844
    ml = np.maximum(f["mlcape"], 0)
    lcl_term = np.clip((2000 - f["lcl"]) / 1000, 0, 1)
    shr_term = np.where(shr6_ms < 12.5, 0, np.minimum(shr6_ms, 30) / 20)
    cin_term = np.clip((200 + f["mlcin"]) / 150, 0, 1)
    stp = (ml / 1500) * lcl_term * (np.maximum(f["srh1"], 0) / 150) * shr_term * cin_term
    return {
        "refc": f["refc"], "stp": np.maximum(stp, 0), "mlcape": ml, "sbcape": np.maximum(f["sbcape"], 0),
        "srh1": f["srh1"], "srh3": f["srh3"], "shr6": shr6, "uh": f["uh"], "mlcin": f["mlcin"],
        "t2": (f["t2"] - 273.15) * 9 / 5 + 32, "td2": (f["td2"] - 273.15) * 9 / 5 + 32,
    }


def feather(img, px=48):
    """Fade alpha to zero toward the frame edges so rasters blend into the wider basemap."""
    h, w = img.shape[:2]
    d = np.minimum.outer(np.minimum(np.arange(h), np.arange(h)[::-1]), np.minimum(np.arange(w), np.arange(w)[::-1]))
    m = np.clip(d / px, 0, 1) ** 1.5
    img[..., 3] = (img[..., 3] * m).astype(np.uint8)
    return img


def hx(s):
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))


# Each scale: list of (lower bound, hex). Values below the first bound are transparent.
SCALES = {
    "refc": [(5, "#04e9e7"), (10, "#019ff4"), (15, "#0300f4"), (20, "#02fd02"), (25, "#01c501"),
             (30, "#008e00"), (35, "#fdf802"), (40, "#e5bc00"), (45, "#fd9500"), (50, "#fd0000"),
             (55, "#d40000"), (60, "#bc0000"), (65, "#f800fd"), (70, "#9854c6"), (75, "#fdfdfd")],
    "stp": [(0.25, "#5e6b7a"), (0.5, "#8fa1b3"), (1, "#f2e15b"), (2, "#f5a623"), (3, "#e8452c"),
            (4, "#c2185b"), (6, "#9c27b0"), (8, "#5e35b1"), (10, "#e1bee7")],
    "mlcape": [(100, "#3b5b7a"), (250, "#3f7fb0"), (500, "#4fb3a9"), (1000, "#8bcf5c"), (1500, "#e8d84a"),
               (2000, "#f2a23a"), (2500, "#e8692c"), (3000, "#d1362f"), (4000, "#a31a5c"), (5000, "#e7a6e0")],
    "sbcape": None,  # reuses mlcape
    "srh1": [(50, "#3e5872"), (100, "#4f8fc0"), (150, "#56b6a0"), (200, "#9ccc5a"), (250, "#ecd54a"),
             (300, "#f39a36"), (400, "#e2482d"), (500, "#b21e5a"), (600, "#e2a2e8")],
    "srh3": [(100, "#3e5872"), (150, "#4f8fc0"), (200, "#56b6a0"), (250, "#9ccc5a"), (300, "#ecd54a"),
             (400, "#f39a36"), (500, "#e2482d"), (600, "#b21e5a"), (800, "#e2a2e8")],
    "shr6": [(20, "#3e5872"), (30, "#4f8fc0"), (40, "#56b6a0"), (50, "#9ccc5a"), (60, "#ecd54a"),
             (70, "#f39a36"), (80, "#e2482d"), (90, "#b21e5a")],
    "uh": [(25, "#6f8db0"), (50, "#9ccc5a"), (75, "#ecd54a"), (100, "#f39a36"), (150, "#e2482d"),
           (200, "#b21e5a"), (300, "#f0b2f2")],
    "t2": [(-20, "#e3d9f2"), (0, "#9d7ccf"), (10, "#5a5fc7"), (20, "#3a8fd6"), (32, "#5bc0de"), (40, "#4fb38a"),
           (50, "#8ccb4f"), (60, "#e3d34a"), (70, "#f2a23a"), (80, "#e5622c"), (90, "#c42a2a"), (100, "#8e1a3f")],
    "td2": [(0, "#8a6a4a"), (20, "#a88a5c"), (30, "#c7b083"), (40, "#9ccc8a"), (50, "#4fb36a"), (55, "#2f9a55"),
            (60, "#2f8a8a"), (65, "#2a6fb0"), (70, "#4a4fc0"), (75, "#7a3fb8")],
}
SCALES["sbcape"] = SCALES["mlcape"]
OPAQUE = {"t2", "td2"}  # continuous fields shown everywhere
PROBE_SCALE = {"refc": 10, "stp": 100, "mlcape": 1, "sbcape": 1, "srh1": 1, "srh3": 1, "shr6": 10,
               "uh": 1, "mlcin": 1, "t2": 10, "td2": 10}
LAYERS = list(PROBE_SCALE.keys())
RASTER = [k for k in LAYERS if k in SCALES]


def colorize(name, v):
    sc = SCALES[name]
    rgba = np.zeros(v.shape + (4,), np.uint8)
    if name in OPAQUE:
        lo, col = sc[0]
        rgba[...] = (*hx(col), 200)
    for lo, col in sc:
        m = v >= lo
        rgba[m] = (*hx(col), 225)
    return rgba


def build_hrrr(now):
    cycle = latest_hrrr_cycle(now)
    print("HRRR cycle", cycle)
    start = max(0, int((now - cycle).total_seconds() // 3600))  # forecast hour valid at the current hour
    fhs = list(range(start, HRRR_HOURS))
    first = fetch_hrrr_fields(cycle, start)
    jj, ii, valid = lcc_index(first["grid"])
    pj, pi = jj[PROBE // 2::PROBE, PROBE // 2::PROBE][:PH, :PW], ii[PROBE // 2::PROBE, PROBE // 2::PROBE][:PH, :PW]
    pvalid = valid[PROBE // 2::PROBE, PROBE // 2::PROBE][:PH, :PW]

    def one(fh):
        done = os.path.join(OUT, "hrrr", f"summary_{cycle:%Y%m%d%H}_f{fh:02d}.json")
        if os.path.exists(done):
            return json.load(open(done))
        f = first if fh == start else fetch_hrrr_fields(cycle, fh)
        d = derive(f)
        for k in RASTER:
            v = d[k][jj, ii]
            img = colorize(k, v)
            img[~valid] = 0
            feather(img)
            Image.fromarray(img, "RGBA").save(os.path.join(OUT, "hrrr", f"{k}_h{fh - start:02d}.png"), optimize=True)
        buf = bytearray()
        for k in LAYERS:
            v = np.where(pvalid, d[k][pj, pi] * PROBE_SCALE[k], -32768)
            buf += np.clip(np.rint(v), -32768, 32767).astype("<i2").tobytes()
        save(f"hrrr/probe_h{fh - start:02d}.txt", base64.b64encode(gzip.compress(bytes(buf), 9)).decode())  # artifacts serve text, not .bin
        mx = {k: float(np.nanmax(d[k][jj, ii][valid])) for k in ("stp", "mlcape", "srh1", "shr6", "uh", "refc")}
        print("f%02d" % fh, {k: round(v, 1) for k, v in mx.items()})
        res = {"fh": fh, "valid": (cycle + dt.timedelta(hours=fh)).strftime("%Y-%m-%dT%H:%MZ"), "max": mx}
        json.dump(res, open(done, "w"))
        return res

    os.makedirs(os.path.join(OUT, "hrrr"), exist_ok=True)
    with cf.ThreadPoolExecutor(2) as ex:
        hours = sorted(ex.map(one, fhs), key=lambda h: h["fh"])
    return {"cycle": cycle.strftime("%Y-%m-%dT%H:%MZ"), "hours": hours, "layers": LAYERS,
            "probeScale": PROBE_SCALE, "probe": [PW, PH],
            "scales": {k: SCALES[k] for k in RASTER}}


def main():
    os.makedirs(OUT, exist_ok=True)
    # Start clean so nothing from an earlier run (past radar scans, old model hours) is published.
    for sub in ("radar", "hrrr", "sat"):
        d = os.path.join(OUT, sub)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                os.remove(os.path.join(d, fn))
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    meta = {"built": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "frame": {"x0": X0, "x1": X1, "y0": Y0, "y1": Y1}}
    with cf.ThreadPoolExecutor(6) as ex:
        jobs = {"alerts": ex.submit(build_alerts), "spc": ex.submit(build_spc),
                "reports": ex.submit(build_reports), "metars": ex.submit(build_metars),
                "wm": ex.submit(build_watches_mds), "radar": ex.submit(build_radar, now),
                "sat": ex.submit(build_satellite, now)}
        hrrr = build_hrrr(now)
        res = {}
        for k, j in jobs.items():
            try:
                res[k] = j.result()
            except Exception as e:
                print("FAILED", k, e)
                res[k] = None
    save("alerts.json", json.dumps(res["alerts"] or [], separators=(",", ":")))
    save("spc.json", json.dumps(res["spc"] or {}, separators=(",", ":")))
    save("reports.json", json.dumps(res["reports"] or [], separators=(",", ":")))
    save("metars.json", json.dumps(res["metars"] or [], separators=(",", ":")))
    w, m = res["wm"] or ({"features": []}, {"features": []})
    save("watches.json", json.dumps(w, separators=(",", ":")))
    save("mds.json", json.dumps(m, separators=(",", ":")))
    meta["radar"] = res["radar"] or []
    meta["sat"] = res["sat"] or []
    meta["hrrr"] = hrrr
    save("meta.json", json.dumps(meta, indent=1))
    # Paths for the Artifact publish (relative to this folder).
    files = sorted(os.path.relpath(os.path.join(dp, f), HERE) for dp, _, fs in os.walk(OUT) for f in fs
                   if not f.startswith("summary_"))
    with open(os.path.join(HERE, "publish_files.json"), "w") as fh:
        json.dump([{"path": f} for f in files], fh)
    print("done", meta["built"])


if __name__ == "__main__":
    main()
