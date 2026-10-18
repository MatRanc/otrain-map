"""Regenerate lines.json + schedule.json from OC Transpo static GTFS. Usage: python3 build_lines.py <unzipped GTFS dir>"""
import csv, json, math, re, sys, os, unicodedata, urllib.parse, urllib.request

d = sys.argv[1]
rd = lambda f: csv.DictReader(open(os.path.join(d, f), encoding="utf-8-sig"))

routes = {r["route_id"]: r for r in rd("routes.txt") if r["route_type"] == "0"}  # light rail
trips = [t for t in rd("trips.txt") if t["route_id"] in routes]
shape_ids = {t["shape_id"] for t in trips}
shapes = {}
for p in rd("shapes.txt"):
    if p["shape_id"] in shape_ids:
        shapes.setdefault(p["shape_id"], []).append((int(p["shape_pt_sequence"]), round(float(p["shape_pt_lat"]), 5), round(float(p["shape_pt_lon"]), 5)))

# Representative trip per (route, direction): the one on the longest shape (full run).
best = {}
for t in trips:
    k, n = (t["route_id"], t["direction_id"]), len(shapes[t["shape_id"]])
    if k not in best or n > best[k][0]:
        best[k] = (n, t)
rail_trips = {t["trip_id"] for t in trips}
seq, times = {}, {}
hms = lambda x: (lambda h, m, s: h * 3600 + m * 60 + s)(*map(int, x.split(":")))
for st in rd("stop_times.txt"):
    if st["trip_id"] in rail_trips:
        seq.setdefault(st["trip_id"], []).append((int(st["stop_sequence"]), st["stop_id"]))
        times.setdefault(st["trip_id"], []).append((int(st["stop_sequence"]), st["stop_id"], hms(st["arrival_time"]), hms(st["departure_time"])))

stops = {s["stop_id"]: s for s in rd("stops.txt")}
def station(sid):
    s = stops[sid]
    p = stops.get(s["parent_station"]) or s
    name = re.sub(r"(^|[\s-])(\w)", lambda m: m[1] + m[2].upper(), p["stop_name"].lower()).replace("Uottawa", "uOttawa")
    return {"name": name, "lat": float(p["stop_lat"]), "lon": float(p["stop_lon"])}

# GTFS parent-station coords are the centre of the whole complex (up to ~300 m off the track).
# OSM station nodes match the basemap, so use those and snap them onto the line.
norm = lambda n: unicodedata.normalize("NFKD", re.split(r"[~/]", n)[0]).encode("ascii", "ignore").decode().strip().lower()
K = 111320 * math.cos(math.radians(45.4))
# One Overpass call: station nodes + O-Train main-line tracks (yards/sidings/crossovers carry a service tag; skipped).
BBOX = "(45.2,-76.0,45.55,-75.4)"
q = (f'[out:json][timeout:90];(node["railway"="station"]["station"="light_rail"]{BBOX};'
     f'way["railway"="light_rail"][!"service"]["name"~"^(Confederation Line|Ellwood Subdivision|Airport Subdivision)"]{BBOX};);out geom;')
req = urllib.request.Request("https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": q}),
                             headers={"User-Agent": "otrain-map build script", "Accept": "application/json"})
for attempt in range(5):  # Overpass throws the odd 502/504
    try: elements = json.load(urllib.request.urlopen(req, timeout=90))["elements"]; break
    except urllib.error.HTTPError as e:
        if attempt == 4: raise
        print("  Overpass", e.code, "- retrying"); __import__("time").sleep(10)
osm = {norm(e["tags"]["name"]): (e["lat"], e["lon"]) for e in elements if e["type"] == "node" and "name" in e["tags"]}
tracks = [[(p["lon"], p["lat"]) for p in e["geometry"]] for e in elements if e["type"] == "way"]
print(" ", len(tracks), "OSM track ways")

def snap(lat, lon, shape):
    best = (1e18, lat, lon)
    for (a, b) in zip(shape, shape[1:]):
        ax, ay, bx, by, px, py = a[1] * K, a[0] * 110540, b[1] * K, b[0] * 110540, lon * K, lat * 110540
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
        e = (ax + t * dx - px) ** 2 + (ay + t * dy - py) ** 2
        if e < best[0]: best = (e, a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return round(best[1], 6), round(best[2], 6)

# GTFS shapes are rough: Line 2's is typically 9 m and up to 80 m off the real track, with kinks.
# Walk the GTFS shape every 5 m, snap each point to the nearest OSM main-line track within 250 m running the same way
# (< 35 deg, so it never jumps onto a crossing line), then average over +-25 m so double track doesn't zigzag between
# rails. Keep one point per 10 m. No track nearby -> keep the GTFS point.
CELL = 300
grid = {}
for w, t in enumerate(tracks):
    xy = [(lo * K, la * 110540) for lo, la in t]
    for a, b in zip(xy, xy[1:]):
        for cx in range(int(min(a[0], b[0]) // CELL) - 1, int(max(a[0], b[0]) // CELL) + 2):
            for cy in range(int(min(a[1], b[1]) // CELL) - 1, int(max(a[1], b[1]) // CELL) + 2):
                grid.setdefault((cx, cy), []).append((a, b, w))

def resample(xy, step):
    out, carry = [xy[0]], 0.0
    for (ax, ay), (bx, by) in zip(xy, xy[1:]):
        seg = math.hypot(bx - ax, by - ay)
        t = step - carry
        while t <= seg:
            out.append((ax + (bx - ax) * t / seg, ay + (by - ay) * t / seg)); t += step
        carry = seg - (t - step)
    return out + [xy[-1]]

def snap_to_track(px, py, hx, hy, prev):
    near = {}  # way -> (distance, point)
    for (ax, ay), (bx, by), w in grid.get((int(px // CELL), int(py // CELL)), []):
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy) or 1
        if abs(dx * hx + dy * hy) / seg < math.cos(math.radians(35)): continue  # not running the same way
        raw = ((px - ax) * dx + (py - ay) * dy) / (seg * seg)
        t = max(0, min(1, raw))
        qx, qy = ax + t * dx, ay + t * dy
        e = math.hypot(qx - px, qy - py)
        if e < 250 and e < near.get(w, (1e9,))[0]: near[w] = (e, (qx, qy), 0 <= raw <= 1)
    if not near: return (px, py), None
    w = min(near, key=lambda k: near[k][0])
    # Stick to the track we were on (passing loops, double track) unless another is clearly closer, but not once we've
    # run past the end of that piece of way (points would pile up on its end vertex).
    if prev in near and near[prev][2] and near[prev][0] <= near[w][0] + 10: w = prev
    return near[w][1], w

def smooth(shape, step=5, half=5):
    pts = resample([(lo * K, la * 110540) for la, lo in shape], step)
    snapped, prev = [], None
    for i, (px, py) in enumerate(pts):
        (ax, ay), (bx, by) = pts[max(0, i - 4)], pts[min(len(pts) - 1, i + 4)]  # local direction over +-20 m
        h = math.hypot(bx - ax, by - ay) or 1
        pt, prev = snap_to_track(px, py, (bx - ax) / h, (by - ay) / h, prev)
        snapped.append(pt)
    avg = []
    for i in range(len(snapped)):
        w = min(half, i, len(snapped) - 1 - i)  # shrink the window at the ends so the termini stay put
        win = snapped[i - w:i + w + 1]
        avg.append((sum(p[0] for p in win) / len(win), sum(p[1] for p in win) / len(win)))
    keep = []
    for p in avg[::2] + [avg[-1]]:  # one point per ~10 m, dropping near-duplicates
        if not keep or math.hypot(p[0] - keep[-1][0], p[1] - keep[-1][1]) > 2: keep.append(p)
    return [[round(y / 110540, 6), round(x / K, 6)] for x, y in keep]

def placed(st, shape):
    lat, lon = osm.get(norm(st["name"]), (st["lat"], st["lon"]))
    if norm(st["name"]) not in osm: print("  no OSM match, using GTFS coords:", st["name"])
    st["lat"], st["lon"] = snap(lat, lon, shape)
    return st

out = []
for rid in sorted(routes, key=lambda r: int(routes[r]["route_sort_order"])):
    r, t0 = routes[rid], best[(rid, "0")][1]
    dirs = [best[(rid, d)][1] for d in "01" if (rid, d) in best]
    line_shape = smooth([[la, lo] for _, la, lo in sorted(shapes[t0["shape_id"]])])
    out.append({
        "route_id": rid, "name": r["route_short_name"], "long_name": r["route_long_name"], "color": "#" + r["route_color"],
        "headsigns": {t["direction_id"]: t["trip_headsign"] for t in dirs},
        "shape": line_shape,
        "stations": [placed(station(sid), line_shape) for _, sid in sorted(seq[t0["trip_id"]])],
        "stop_names": {sid: station(sid)["name"] for t in dirs for _, sid in seq[t["trip_id"]]},
    })
here = os.path.dirname(os.path.abspath(__file__))
json.dump(out, open(os.path.join(here, "lines.json"), "w"), separators=(",", ":"))

# Timetable: OC Transpo's GTFS-RT has no O-Train data, so the page animates trains from this.
# trips: [line index, direction, service index, train, s0, arr0, dep0, s1, arr1, dep1, ...]
#   train = one physical vehicle (GTFS block_id; a blank block counts as its own train), so the page draws one marker per train
#   s = station index on that line, times = seconds after midnight
services = sorted({t["service_id"] for t in trips})
sched = {"services": {}, "trips": []}
for c in rd("calendar.txt"):
    if c["service_id"] in services:
        sched["services"][c["service_id"]] = {"days": "".join(c[d] for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")),
                                              "start": c["start_date"], "end": c["end_date"], "add": [], "remove": []}
for c in rd("calendar_dates.txt"):
    if c["service_id"] in services:
        e = sched["services"].setdefault(c["service_id"], {"days": "0000000", "start": "0", "end": "0", "add": [], "remove": []})
        e["add" if c["exception_type"] == "1" else "remove"].append(c["date"])
sched["service_ids"] = services
blocks = {}
for t in trips:
    li = next(i for i, l in enumerate(out) if l["route_id"] == t["route_id"])
    idx = {st["name"]: i for i, st in enumerate(out[li]["stations"])}
    train = blocks.setdefault((t["service_id"], t["route_id"], t["block_id"] or "trip:" + t["trip_id"]), len(blocks))
    row = [li, int(t["direction_id"]), services.index(t["service_id"]), train]
    for _, sid, arr, dep in sorted(times[t["trip_id"]]):
        if station(sid)["name"] in idx: row += [idx[station(sid)["name"]], arr, dep]
    if len(row) > 7: sched["trips"].append(row)
json.dump(sched, open(os.path.join(here, "schedule.json"), "w"), separators=(",", ":"))
print(len(sched["trips"]), "rail trips,", len(blocks), "trains,", len(services), "services")
for l in out: print(l["name"], l["headsigns"], len(l["shape"]), "pts,", len(l["stations"]), "stations,", len(l["stop_names"]), "stop ids")
