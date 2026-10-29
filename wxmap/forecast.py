"""Write a plain-language forecast from the data build.py already collected.

Only the map's own data is used: SPC outlooks, NWS alerts, SPC storm reports,
SPC watches and mesoscale discussions, and the HRRR fields. Everything here is
rule-based, so the same data always produces the same text.
"""
import datetime as dt
import math
import os
import re
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw

TZ = ZoneInfo("America/Chicago")
R = 6378137.0

ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

CATS = ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
CAT_NAME = {"TSTM": "general thunderstorms", "MRGL": "Marginal Risk (level 1 of 5)",
            "SLGT": "Slight Risk (level 2 of 5)", "ENH": "Enhanced Risk (level 3 of 5)",
            "MDT": "Moderate Risk (level 4 of 5)", "HIGH": "High Risk (level 5 of 5)"}

# Alert groups, in the order their sections appear.
GROUPS = [
    ("Flooding", "watch", ["Flash Flood Emergency", "Flash Flood Warning", "Flood Warning", "Flood Watch",
                           "Flash Flood Watch", "Flood Advisory"]),
    ("Winter weather", "adv", ["Blizzard Warning", "Ice Storm Warning", "Winter Storm Warning",
                               "Lake Effect Snow Warning", "Winter Storm Watch", "Winter Weather Advisory"]),
    ("Wind", "adv", ["Extreme Wind Warning", "High Wind Warning", "High Wind Watch", "Wind Advisory"]),
    ("Fire weather", "watch", ["Red Flag Warning", "Fire Weather Watch"]),
    ("Heat and cold", "adv", ["Extreme Heat Warning", "Excessive Heat Warning", "Heat Advisory",
                              "Extreme Cold Warning", "Hard Freeze Warning", "Freeze Warning",
                              "Cold Weather Advisory", "Frost Advisory"]),
    ("Tropical", "warn", ["Hurricane Warning", "Hurricane Watch", "Tropical Storm Warning",
                          "Tropical Storm Watch", "Storm Surge Warning", "Storm Surge Watch"]),
    ("Coast", "adv", ["Coastal Flood Warning", "Coastal Flood Watch", "Coastal Flood Advisory",
                      "High Surf Warning", "High Surf Advisory", "Rip Current Statement", "Beach Hazards Statement"]),
    ("Fog and smoke", "adv", ["Dense Fog Advisory", "Dense Smoke Advisory"]),
]
OUTSIDE = ("AK", "HI", "GU", "PR", "AS", "VI", "MP")  # offices beyond the lower 48


# ---------------------------------------------------------------- geography
def _decode_topo(topo, obj):
    tr = topo.get("transform")
    arcs = []
    for a in topo["arcs"]:
        pts, x, y = [], 0, 0
        for p in a:
            if tr:
                x, y = x + p[0], y + p[1]
                pts.append((x * tr["scale"][0] + tr["translate"][0], y * tr["scale"][1] + tr["translate"][1]))
            else:
                pts.append((p[0], p[1]))
        arcs.append(pts)

    def ring(ids):
        out = []
        for i in ids:
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(pts if not out else pts[1:])
        return out

    for g in topo["objects"][obj]["geometries"]:
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]] if g["type"] == "Polygon" else []
        yield g.get("properties", {}).get("name"), [ring(p[0]) for p in polys]


def state_raster(topo, frame, w, h):
    """Rasterize states onto the model grid: pixel value = 1 + index into the returned names (0 = not US)."""
    x0, x1, y0, y1 = frame
    img = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(img)
    names = []
    for name, rings in _decode_topo(topo, "states"):
        if name not in ABBR:
            continue
        names.append(name)
        for r in rings:
            px = [((math.radians(lon) * R - x0) / (x1 - x0) * w,
                   (y1 - R * math.log(math.tan(math.pi / 4 + math.radians(max(min(lat, 85), -85)) / 2))) / (y1 - y0) * h)
                  for lon, lat in r]
            if len(px) > 2:
                dr.polygon(px, fill=len(names))
    return np.asarray(img), names


def pixel_lonlat(frame, w, h, x, y):
    x0, x1, y0, y1 = frame
    lon = math.degrees((x0 + (x + .5) * (x1 - x0) / w) / R)
    lat = math.degrees(2 * math.atan(math.exp((y1 - (y + .5) * (y1 - y0) / h) / R)) - math.pi / 2)
    return round(lon, 2), round(lat, 2)


# ---------------------------------------------------------------- per-hour model stats
def hour_stats(d, jj, ii, valid, st, names, frame, probe):
    """Compact, JSON-safe numbers for one HRRR hour, restricted to the US."""
    h, w = st.shape
    us = (st > 0) & valid
    v = {k: d[k][jj, ii] for k in ("stp", "uh", "refc", "mlcape", "srh1", "shr6", "t2", "td2")}
    out = {"mx": {k: round(float(np.nanmax(np.where(us, v[k], -1e9))), 1) for k in
                  ("stp", "mlcape", "srh1", "shr6", "uh", "refc")}}

    def hot(k, thr):
        m = us & (v[k] >= thr)
        best = {}
        for y, x in zip(*np.nonzero(m)):
            s = names[st[y, x] - 1]
            val = float(v[k][y, x])
            if s not in best or val > best[s][0]:
                lon, lat = pixel_lonlat(frame, w, h, x, y)
                best[s] = [round(val, 1), lon, lat]
        return best

    out["stp1"] = hot("stp", 1.0)
    out["uh75"] = hot("uh", 75.0)
    # Heavy rain cells on the coarse probe grid (any 40+ dBZ pixel in the block).
    heavy = (us & (v["refc"] >= 40))[:h // probe * probe, :w // probe * probe]
    heavy = heavy.reshape(h // probe, probe, w // probe, probe).any(axis=(1, 3))
    out["rain"] = [int(i) for i in np.flatnonzero(heavy)]
    for k in ("t2", "td2"):
        mx = np.full(len(names) + 1, -999.0)
        np.maximum.at(mx, st[us], v[k][us])
        out[k] = {names[i - 1]: round(float(mx[i]), 1) for i in range(1, len(names) + 1) if mx[i] > -999}
    return out


# ---------------------------------------------------------------- text helpers
def local(t):
    return t.replace(tzinfo=dt.timezone.utc).astimezone(TZ)


def clock(t, day=False):
    lt = local(t)
    s = f"{lt.hour % 12 or 12}{':%02d' % lt.minute if lt.minute else ''}{'am' if lt.hour < 12 else 'pm'}"
    if lt.hour == 0 and not lt.minute:
        s = "midnight"
    elif lt.hour == 12 and not lt.minute:
        s = "noon"
    return f"{s} {lt.tzname()}" + (f" {lt:%a}" if day else "")


def parse_t(s):
    if not s:
        return None
    s = s.strip()
    if re.fullmatch(r"\d{12}", s):
        return dt.datetime.strptime(s, "%Y%m%d%H%M")
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return t.astimezone(dt.timezone.utc).replace(tzinfo=None) if t.tzinfo else t
    except ValueError:
        return None


def join(xs, n=None):
    xs = list(dict.fromkeys(x for x in xs if x))
    more = 0
    if n and len(xs) > n:
        xs, more = xs[:n], len(xs) - n
    if more:
        xs = xs + [f"{more} more"]
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def temp_words(f):
    tens = int(f // 10) * 10
    part = "low" if f - tens < 4 else "mid" if f - tens < 7 else "upper"
    return f"{part} {tens}s" if tens >= 0 else f"{round(f)}°F"


def pluralize(word):
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + ("es" if word.endswith(("ch", "sh", "s", "x")) else "s")


def plural(n, word):
    return f"{n} {word if n == 1 else pluralize(word)}"


def office(sender):
    return (sender or "").replace("NWS ", "").strip()


# ---------------------------------------------------------------- places
class Places:
    def __init__(self, cities):
        self.c = [c for c in cities if c.get("c") == "USA" and c.get("st") in ABBR]
        self.lon = np.array([c["lon"] for c in self.c])
        self.lat = np.array([c["lat"] for c in self.c])

    def near(self, lon, lat, max_km=150):
        d = np.hypot((self.lon - lon) * math.cos(math.radians(lat)), self.lat - lat) * 111
        i = int(np.argmin(d))
        return (f"{self.c[i]['n']}, {ABBR[self.c[i]['st']]}" if d[i] <= max_km else None), self.c[i]["st"]

    def inside(self, geom, min_pop=0):
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        hits = []
        for c in self.c:
            if c["pop"] < min_pop:
                continue
            for p in polys:
                if _pip(c["lon"], c["lat"], p[0]) and not any(_pip(c["lon"], c["lat"], hole) for hole in p[1:]):
                    hits.append(c)
                    break
        return sorted(hits, key=lambda c: -c["pop"])


def _pip(x, y, ring):
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[i - 1][:2]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def city_name(c):
    return f"{c['n']}, {ABBR[c['st']]}"


def states_of(cs, n=4):
    return [s for s, _ in Counter(c["st"] for c in cs).most_common(n)]


# ---------------------------------------------------------------- SPC
def spc_area(layer, places):
    """Highest category in a categorical layer, with the states and big cities inside it."""
    best, feat = None, None
    for f in (layer or {}).get("features", []):
        lab = f["properties"].get("LABEL")
        if lab in CATS and (best is None or CATS.index(lab) > CATS.index(best)):
            best, feat = lab, f
    if not feat:
        return None
    cs = places.inside(feat["geometry"])
    big = [c for c in cs if c["pop"] >= 100000] or cs
    return {"cat": best, "states": states_of(cs), "cities": [city_name(c) for c in big[:5]],
            "valid": parse_t(feat["properties"].get("VALID")), "expire": parse_t(feat["properties"].get("EXPIRE"))}


def spc_prob(layer):
    best = 0.0
    for f in (layer or {}).get("features", []):
        try:
            best = max(best, float(f["properties"].get("LABEL")))
        except (TypeError, ValueError):
            pass
    return best


def spc_sig(layer):
    return any(f.get("geometry") for f in (layer or {}).get("features", []))


# ---------------------------------------------------------------- compose
def compose(now, spc, alerts, reports, watches, mds, hrrr, hours_stats, cities, probe_wh, frame):
    places = Places(cities)
    pw, ph = probe_wh
    sections, notes = [], []
    lower48 = [a for a in alerts if not office(a.get("sender")).endswith(OUTSIDE)]
    outside = [a for a in alerts if office(a.get("sender")).endswith(OUTSIDE)]
    by_event = defaultdict(list)
    for a in lower48:
        by_event[a["event"]].append(a)

    # ---- severe storms
    d1 = spc_area(spc.get("day1otlk_cat"), places)
    torn, wind, hail = (spc_prob(spc.get(k)) for k in ("day1otlk_torn", "day1otlk_wind", "day1otlk_hail"))
    sig_t, sig_w, sig_h = (spc_sig(spc.get(k)) for k in ("day1otlk_sigtorn", "day1otlk_sigwind", "day1otlk_sighail"))
    cat = d1["cat"] if d1 else None
    severe_cat = cat if cat in CATS[1:] else None
    sev = []
    if severe_cat:
        line = f"SPC has a {CAT_NAME[severe_cat]} for {join(d1['states'])}"
        line += f", including {join(d1['cities'])}." if d1["cities"] else "."
        sev.append(line)
        probs = []
        if torn:
            probs.append(f"tornadoes {torn:.0%}" + (" (strong EF2+ tornadoes possible)" if sig_t else ""))
        if wind:
            probs.append(f"damaging wind {wind:.0%}" + (" (75+ mph gusts possible)" if sig_w else ""))
        if hail:
            probs.append(f"large hail {hail:.0%}" + (" (2-inch+ hail possible)" if sig_h else ""))
        if probs:
            sev.append("Highest SPC chances within 25 miles of a point: " + join(probs) + ".")
    else:
        sev.append("SPC shows " + ("general thunderstorms only" if cat == "TSTM" else "no thunderstorm areas")
                   + " for today: tornado chances below 2%, wind and hail below 5% everywhere.")

    tw, sw = by_event.get("Tornado Warning", []), by_event.get("Severe Thunderstorm Warning", [])
    if tw:
        sev.insert(0, f"{plural(len(tw), 'Tornado Warning')} in effect now from NWS {join([office(a['sender']) for a in tw], 4)}.")
    if sw:
        sev.append(f"{plural(len(sw), 'Severe Thunderstorm Warning')} in effect from NWS {join([office(a['sender']) for a in sw], 4)}.")
    wf = [f.get("properties", {}) for f in (watches or {}).get("features", [])]
    if wf:
        nt = sum(1 for p in wf if str(p.get("type", "")).upper().startswith("TOR"))
        txt = []
        if nt:
            txt.append(plural(nt, "Tornado Watch"))
        if len(wf) - nt:
            txt.append(plural(len(wf) - nt, "Severe Thunderstorm Watch"))
        sev.append(join(txt) + " in effect.")
    md = [f.get("properties", {}) for f in (mds or {}).get("features", [])]
    if md:
        conc = [p.get("concerning") for p in md if p.get("concerning")]
        sev.append(f"{plural(len(md), 'SPC mesoscale discussion')} active" + (f" ({join(conc, 2).lower()})." if conc else "."))
    if reports:
        k = Counter(r["t"] for r in reports)
        word = {"torn": "tornado", "wind": "wind", "hail": "hail"}
        sev.append("Storm reports so far today: " + join([plural(k[t], word[t] + " report") for t in ("torn", "wind", "hail") if k[t]])
                   + f", mostly in {join([s for s, _ in Counter(r['st'] for r in reports).most_common(3)])}.")

    # Model ingredients over the US.
    hs = [(h, s) for h, s in zip(hrrr.get("hours", []), hours_stats) if s]
    peak_stp, peak_uh = None, None
    for h, s in hs:
        t = parse_t(h["valid"])
        for st, (v, lon, lat) in s["stp1"].items():
            if not peak_stp or v > peak_stp[0]:
                peak_stp = (v, t, lon, lat, st)
        for st, (v, lon, lat) in s["uh75"].items():
            if not peak_uh or v > peak_uh[0]:
                peak_uh = (v, t, lon, lat, st)
    stp_states = sorted({st for _, s in hs for st in s["stp1"]})
    max_stp_any = max((s["mx"]["stp"] for _, s in hs), default=0)
    if peak_stp:
        v, t, lon, lat, st = peak_stp
        word = "strong" if v >= 6 else "favorable" if v >= 3 else "modest"
        town, _ = places.near(lon, lat)
        line = (f"The HRRR's tornado ingredients (STP) are {word}, peaking near {v:.1f} around {clock(t, True)}"
                + (f" near {town}" if town else f" in {st}") + ".")
        others = [s for s in stp_states if s != st]
        if others:
            line += f" STP of 1 or more also shows up in {join(others, 5)}."
        sev.append(line)
    else:
        sev.append(f"Model tornado ingredients stay weak over the US (STP peaks near {max_stp_any:.1f}).")
    if peak_uh:
        v, t, lon, lat, st = peak_uh
        towns = []
        for _, s in hs:
            for st2, (v2, lo2, la2) in s["uh75"].items():
                towns.append(places.near(lo2, la2)[0] or st2)
        top = [x for x, _ in Counter(towns).most_common(4)]
        sev.append(f"The HRRR shows rotating storms (updraft helicity up to {v:.0f}) near {join(top)}"
                   f", strongest around {clock(t, True)}.")
    tone = "warn" if tw or severe_cat in ("ENH", "MDT", "HIGH") else "watch" if severe_cat in ("SLGT",) or sw else \
        "adv" if severe_cat else "info"
    sections.append({"title": "Severe storms", "tone": tone, "items": sev})

    # ---- heavy rain from the model (feeds the flooding section)
    hours_at = Counter()
    for _, s in hs:
        for i in s["rain"]:
            hours_at[i] += 1
    town_hours = Counter()
    for i, n in hours_at.items():
        if n < 3:
            continue
        x, y = i % pw, i // pw
        lon, lat = pixel_lonlat(frame, pw, ph, x, y)
        town, _ = places.near(lon, lat, max_km=80)
        if town:
            town_hours[town] = max(town_hours[town], n)
    rain_towns = [t for t, n in town_hours.most_common(8)]

    # ---- alert-driven sections
    flood_level = 0
    for title, tone, events in GROUPS:
        items = []
        for ev in events:
            xs = by_event.get(ev, [])
            if ev == "Flash Flood Warning":
                emerg = [a for a in xs if "EMERGENCY" in (a.get("desc") or "").upper()]
                if emerg:
                    items.append(f"FLASH FLOOD EMERGENCY from NWS {join([office(a['sender']) for a in emerg], 4)}. Move to higher ground now.")
                    flood_level = 3
            if not xs:
                continue
            ends = [parse_t(a.get("ends")) for a in xs]
            ends = [e for e in ends if e]
            until = f" until {clock(max(ends), True)}" if ends else ""
            items.append(f"{plural(len(xs), ev)} from NWS {join([office(a['sender']) for a in xs], 5)}{until}.")
            if ev in ("Flash Flood Warning", "Flood Warning"):
                flood_level = max(flood_level, 2)
            elif ev in ("Flood Watch", "Flash Flood Watch"):
                flood_level = max(flood_level, 1)
        if title == "Flooding" and rain_towns and (items or town_hours.most_common(1)[0][1] >= 5):
            items.append(f"The HRRR keeps heavy rain (40+ dBZ) over the same areas for hours, most persistently near {join(rain_towns, 6)}.")
            if items[:-1]:
                items.append("Repeated rain over the same ground is a flash-flood setup even without severe storms.")
        if items:
            sections.append({"title": title, "tone": "warn" if flood_level >= 2 and title == "Flooding" else tone, "items": items})

    # ---- temperatures (next 12 hours)
    t_end = now + dt.timedelta(hours=12)
    tmax = defaultdict(lambda: -999.0)
    for h, s in hs:
        if parse_t(h["valid"]) <= t_end:
            for st, v in s["t2"].items():
                tmax[st] = max(tmax[st], v)
    if tmax:
        warm = sorted(tmax.items(), key=lambda kv: -kv[1])[:3]
        cool = min(tmax.items(), key=lambda kv: kv[1])
        sections.append({"title": "Temperatures", "tone": "info", "items": [
            "Warmest in the next 12 hours: " + join([f"{temp_words(v)} in {st}" for st, v in warm]) + ".",
            f"Coolest: only the {temp_words(cool[1])} in {cool[0]}."]})

    # ---- beyond the lower 48
    if outside:
        evs = Counter(a["event"] for a in outside if a["event"] not in ("Small Craft Advisory", "Special Weather Statement"))
        if evs:
            offs = join(sorted({office(a["sender"]) for a in outside}), 4)
            sections.append({"title": "Alaska, Hawaii and territories", "tone": "info",
                             "items": [f"{join([pluralize(e) if n > 1 else e for e, n in evs.most_common(4)])} (NWS {offs})."]})

    # ---- days 2 and 3
    outlook = []
    for k in ("day2otlk_cat", "day3otlk_cat"):
        a = spc_area(spc.get(k), places)
        if not a:
            continue
        day = local(a["valid"]).strftime("%A") if a["valid"] else ("Day 2" if "2" in k else "Day 3")
        if a["cat"] == "TSTM":
            txt = "General thunderstorms only, mainly " + join(a["states"]) + ". No organized severe risk."
        else:
            txt = f"{CAT_NAME[a['cat']]} for {join(a['states'])}" + (f", including {join(a['cities'])}." if a["cities"] else ".")
        outlook.append({"title": day, "cat": a["cat"], "text": txt})

    # ---- headline
    flood_states = []
    for a in by_event.get("Flood Watch", []) + by_event.get("Flash Flood Warning", []) + by_event.get("Flood Warning", []):
        m = re.search(r"\b([A-Z]{2})$", office(a.get("sender")))
        if m:
            flood_states.append(m.group(1))
    if tw:
        headline = f"{plural(len(tw), 'tornado warning')} in effect now"
    elif severe_cat in ("SLGT", "ENH", "MDT", "HIGH"):
        headline = f"Severe storms expected in {join(d1['states'][:3])}"
    elif severe_cat == "MRGL":
        headline = f"Isolated severe storms possible in {join(d1['states'][:3])}"
    elif flood_level:
        headline = "Flooding rain is the main threat" + (f" ({join(sorted(set(flood_states)), 4)})" if flood_states else "")
    elif by_event.get("Blizzard Warning") or by_event.get("Winter Storm Warning"):
        headline = "Winter storm is the main threat"
    else:
        headline = "No organized severe weather today"
    if severe_cat:
        summary = sev[0]
    elif flood_level:
        summary = "SPC has no severe risk today. The bigger concern is flooding" + \
                  (f" where rain keeps falling over the same places, especially near {join(rain_towns[:3])}." if rain_towns else ".")
    else:
        summary = "SPC has no severe risk outlined for today."

    last = parse_t(hrrr["hours"][-1]["valid"]) if hrrr.get("hours") else now
    cyc = parse_t(hrrr.get("cycle"))
    notes.append(f"Written automatically from the map's data: SPC outlooks, NWS alerts, SPC reports and the "
                 f"{clock(cyc) if cyc else ''} HRRR run. Not an official forecast; follow your local NWS office.")
    return {"issued": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "issuedText": f"{local(now):%a %b} {local(now).day}, {clock(now)}",
            "through": clock(last, True), "headline": headline, "summary": summary,
            "level": severe_cat or ("flood" if flood_level else cat or "none"),
            "sections": sections, "outlook": outlook, "notes": notes}
