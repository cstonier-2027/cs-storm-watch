"""Global layers for the Global tab, from NCEP's GFS on a 1-degree grid.

Each field is stored per forecast time as gzip + base64 text of little-endian int16
(value x scale), rows north to south, columns from 180W eastward. The page colors them itself.
"""
import base64
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os

import eccodes
import numpy as np

HOURS = range(0, 49, 6)   # now through 48 hours
PARAMS = ("var_TMP=on&var_UGRD=on&var_VGRD=on&var_PRATE=on&var_TCDC=on&var_PRMSL=on&var_ICEC=on&var_LAND=on"
          "&lev_2_m_above_ground=on&lev_10_m_above_ground=on&lev_250_mb=on&lev_surface=on"
          "&lev_entire_atmosphere=on&lev_mean_sea_level=on")
# name: (GRIB key, scale, converter). Keys are shortName:typeOfLevel:level.
FIELDS = {
    "t2": ("2t:heightAboveGround:2", 10, lambda k: (k - 273.15) * 9 / 5 + 32),      # deg F
    "prate": ("prate:surface:0", 100, lambda r: r * 3600),                          # mm per hour
    "tcc": ("tcc:atmosphere:0", 1, lambda c: c),                                    # percent
    "prmsl": ("prmsl:meanSea:0", 10, lambda p: p / 100),                            # mb
}
WINDS = {"w10": ("10u:heightAboveGround:10", "10v:heightAboveGround:10"),
         "w250": ("u:isobaricInhPa:250", "v:isobaricInhPa:250")}
WIND_SCALE = 10   # m/s x 10


def decode(b):
    out, pos = {}, 0
    while pos < len(b):
        gid = eccodes.codes_new_from_message(b[pos:])
        if gid is None:
            break
        pos += eccodes.codes_get(gid, "totalLength")
        key = f"{eccodes.codes_get(gid, 'shortName')}:{eccodes.codes_get(gid, 'typeOfLevel')}:{eccodes.codes_get(gid, 'level')}"
        if eccodes.codes_get(gid, "stepType") == "instant" and key not in out:
            nx, ny = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
            v = eccodes.codes_get_values(gid).reshape(ny, nx)
            if eccodes.codes_get(gid, "jScansPositively"):
                v = v[::-1]
            out[key] = np.roll(v, nx // 2, axis=1)   # 0..359E -> 180W..179E
        eccodes.codes_release(gid)
    return out


def pack(a, scale):
    q = np.where(np.isfinite(a), np.rint(a * scale), -32768)
    return base64.b64encode(gzip.compress(np.clip(q, -32768, 32767).astype("<i2").tobytes(), 9)).decode()


def build(now, cycle, get, save, out_dir):
    """cycle: a GFS cycle known to exist. get(url) -> bytes; save(relpath, text) writes under data/."""
    def url(fh):
        return ("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"
                f"?dir=%2Fgfs.{cycle:%Y%m%d}%2F{cycle:%H}%2Fatmos&file=gfs.t{cycle:%H}z.pgrb2.1p00.f{fh:03d}&{PARAMS}")

    age = int((now - cycle).total_seconds() // 3600)
    start = round(age / 6) * 6   # the forecast time nearest to now
    os.makedirs(os.path.join(out_dir, "global"), exist_ok=True)

    def one(fh):
        try:
            b = get(url(fh), binary=True)
            if not b.startswith(b"GRIB"):
                raise RuntimeError("not available")
            f = decode(b)
        except Exception as e:
            print("FAILED global f%03d" % fh, repr(e))
            return None
        files = {}
        for name, (key, scale, conv) in FIELDS.items():
            if key in f:
                save(f"global/{name}_{fh:03d}.txt", pack(conv(f[key]), scale))
                files[name] = f"data/global/{name}_{fh:03d}.txt"
        for name, (ku, kv) in WINDS.items():
            if ku in f and kv in f:
                save(f"global/{name}_{fh:03d}.txt", pack(np.stack([f[ku], f[kv]]), WIND_SCALE))
                files[name] = f"data/global/{name}_{fh:03d}.txt"
        static = None
        if fh == start:   # ocean temperature and sea ice barely change in two days: keep the first time only
            land = f.get("lsm:surface:0")
            sfc = f.get("t:surface:0")
            ice = f.get("ci:surface:0")
            if land is not None and sfc is not None:
                water = land < 0.5
                ice_v = np.nan_to_num(ice) if ice is not None else np.zeros_like(sfc)
                sst = np.where(water & (ice_v < 0.5), (sfc - 273.15) * 9 / 5 + 32, np.nan)
                save("global/sst.txt", pack(sst, 10))
                save("global/ice.txt", pack(np.where(water, ice_v * 100, np.nan), 1))
                static = {"sst": "data/global/sst.txt", "ice": "data/global/ice.txt"}
        return {"fh": fh, "valid": (cycle + dt.timedelta(hours=fh)).strftime("%Y-%m-%dT%H:%MZ"), "files": files, "static": static}

    with cf.ThreadPoolExecutor(2) as ex:
        frames = [r for r in ex.map(one, [start + h for h in HOURS]) if r]
    if not frames:
        return None
    static = next((f.pop("static") for f in frames if f.get("static")), None)
    for f in frames:
        f.pop("static", None)
    meta = {"built": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cycle": cycle.strftime("%Y-%m-%dT%H:%MZ"),
            "grid": {"nx": 360, "ny": 181, "lon0": -180, "lat0": 90, "d": 1},
            "scale": {**{k: v[1] for k, v in FIELDS.items()}, "sst": 10, "ice": 1, "wind": WIND_SCALE},
            "static": static, "frames": frames}
    save("global/meta.json", json.dumps(meta, indent=1))
    return meta
