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
q = '[out:json][timeout:60];node["railway"="station"]["station"="light_rail"](45.2,-76.0,45.55,-75.4);out;'
req = urllib.request.Request("https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": q}),
                             headers={"User-Agent": "otrain-map build script", "Accept": "application/json"})
for attempt in range(5):  # Overpass throws the odd 502/504
    try: elements = json.load(urllib.request.urlopen(req, timeout=90))["elements"]; break
    except urllib.error.HTTPError as e:
        if attempt == 4: raise
        print("  Overpass", e.code, "- retrying"); __import__("time").sleep(10)
osm = {norm(e["tags"]["name"]): (e["lat"], e["lon"]) for e in elements if "name" in e["tags"]}

K = 111320 * math.cos(math.radians(45.4))
def snap(lat, lon, shape):
    best = (1e18, lat, lon)
    for (a, b) in zip(shape, shape[1:]):
        ax, ay, bx, by, px, py = a[1] * K, a[0] * 110540, b[1] * K, b[0] * 110540, lon * K, lat * 110540
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
        e = (ax + t * dx - px) ** 2 + (ay + t * dy - py) ** 2
        if e < best[0]: best = (e, a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return round(best[1], 6), round(best[2], 6)

def placed(st, shape):
    lat, lon = osm.get(norm(st["name"]), (st["lat"], st["lon"]))
    if norm(st["name"]) not in osm: print("  no OSM match, using GTFS coords:", st["name"])
    st["lat"], st["lon"] = snap(lat, lon, shape)
    return st

out = []
for rid in sorted(routes, key=lambda r: int(routes[r]["route_sort_order"])):
    r, t0 = routes[rid], best[(rid, "0")][1]
    dirs = [best[(rid, d)][1] for d in "01" if (rid, d) in best]
    out.append({
        "route_id": rid, "name": r["route_short_name"], "long_name": r["route_long_name"], "color": "#" + r["route_color"],
        "headsigns": {t["direction_id"]: t["trip_headsign"] for t in dirs},
        "shape": [[la, lo] for _, la, lo in sorted(shapes[t0["shape_id"]])],
        "stations": [placed(station(sid), [[la, lo] for _, la, lo in sorted(shapes[t0["shape_id"]])]) for _, sid in sorted(seq[t0["trip_id"]])],
        "stop_names": {sid: station(sid)["name"] for t in dirs for _, sid in seq[t["trip_id"]]},
    })
here = os.path.dirname(os.path.abspath(__file__))
json.dump(out, open(os.path.join(here, "lines.json"), "w"), separators=(",", ":"))

# Timetable: OC Transpo's GTFS-RT has no O-Train data, so the page animates trains from this.
# trips: [line index, direction, service index, s0, arr0, dep0, s1, arr1, dep1, ...] (s = station index on that line, times = s after midnight)
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
for t in trips:
    li = next(i for i, l in enumerate(out) if l["route_id"] == t["route_id"])
    idx = {st["name"]: i for i, st in enumerate(out[li]["stations"])}
    row = [li, int(t["direction_id"]), services.index(t["service_id"])]
    for _, sid, arr, dep in sorted(times[t["trip_id"]]):
        if station(sid)["name"] in idx: row += [idx[station(sid)["name"]], arr, dep]
    if len(row) > 6: sched["trips"].append(row)
json.dump(sched, open(os.path.join(here, "schedule.json"), "w"), separators=(",", ":"))
print(len(sched["trips"]), "rail trips,", len(services), "services")
for l in out: print(l["name"], l["headsigns"], len(l["shape"]), "pts,", len(l["stations"]), "stations,", len(l["stop_names"]), "stop ids")
