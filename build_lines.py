"""Regenerate lines.json from OC Transpo static GTFS. Usage: python3 build_lines.py <unzipped GTFS dir>"""
import csv, json, re, sys, os

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
want = {t["trip_id"] for _, t in best.values()}
seq = {}
for st in rd("stop_times.txt"):
    if st["trip_id"] in want:
        seq.setdefault(st["trip_id"], []).append((int(st["stop_sequence"]), st["stop_id"]))

stops = {s["stop_id"]: s for s in rd("stops.txt")}
def station(sid):
    s = stops[sid]
    p = stops.get(s["parent_station"]) or s
    name = re.sub(r"(^|[\s-])(\w)", lambda m: m[1] + m[2].upper(), p["stop_name"].lower()).replace("Uottawa", "uOttawa")
    return {"name": name, "lat": float(p["stop_lat"]), "lon": float(p["stop_lon"])}

out = []
for rid in sorted(routes, key=lambda r: int(routes[r]["route_sort_order"])):
    r, t0 = routes[rid], best[(rid, "0")][1]
    dirs = [best[(rid, d)][1] for d in "01" if (rid, d) in best]
    out.append({
        "route_id": rid, "name": r["route_short_name"], "long_name": r["route_long_name"], "color": "#" + r["route_color"],
        "headsigns": {t["direction_id"]: t["trip_headsign"] for t in dirs},
        "shape": [[la, lo] for _, la, lo in sorted(shapes[t0["shape_id"]])],
        "stations": [station(sid) for _, sid in sorted(seq[t0["trip_id"]])],
        "stop_names": {sid: station(sid)["name"] for t in dirs for _, sid in seq[t["trip_id"]]},
    })
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lines.json"), "w"), separators=(",", ":"))
for l in out: print(l["name"], l["headsigns"], len(l["shape"]), "pts,", len(l["stations"]), "stations,", len(l["stop_names"]), "stop ids")
