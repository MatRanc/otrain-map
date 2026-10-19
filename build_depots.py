"""Regenerate depots.json: yard track from the main line into each maintenance facility, from OpenStreetMap.
Usage: python3 build_depots.py (after build_lines.py; reads lines.json). The page drives trains along these after their last run."""
import heapq, json, math, os, urllib.parse, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
L = json.load(open(os.path.join(here, "lines.json")))
q = '[out:json][timeout:90];way["railway"="light_rail"]["service"](45.2,-76.0,45.55,-75.4);out geom;'
req = urllib.request.Request("https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": q}),
                             headers={"User-Agent": "otrain-map build script", "Accept": "application/json"})
ways = json.load(urllib.request.urlopen(req, timeout=120))["elements"]
K, KY = 111320 * math.cos(math.radians(45.4)), 110540
xy = lambda la, lo: (lo * K, la * KY)
pos, adj = {}, {}
for w in ways:
    for n, g in zip(w["nodes"], w["geometry"]): pos[n] = xy(g["lat"], g["lon"])
    for a, b in zip(w["nodes"], w["nodes"][1:]):
        d = math.dist(pos[a], pos[b]); adj.setdefault(a, []).append((b, d)); adj.setdefault(b, []).append((a, d))

def route(name, junction, target):
    """Shortest yard path from the switch nearest `junction` (a yard node within 8 m of the main line) to `target`."""
    shape = [xy(*p) for p in next(l for l in L if l["name"] == name)["shape"]]
    h, t = xy(*junction), xy(*target)
    near = [n for n in pos if math.dist(pos[n], h) < 500 and min(math.dist(pos[n], p) for p in shape) < 8]
    start, end = min(near, key=lambda n: math.dist(pos[n], h)), min(pos, key=lambda n: math.dist(pos[n], t))
    dist, prev, heap = {start: 0}, {}, [(0, start)]
    while heap:
        d, n = heapq.heappop(heap)
        if n == end: break
        if d > dist[n]: continue
        for m, w in adj.get(n, []):
            if d + w < dist.get(m, 1e18): dist[m], prev[m] = d + w, n; heapq.heappush(heap, (d + w, m))
    path = [end]
    while path[-1] != start: path.append(prev[path[-1]])
    print(f"  {name}: {round(dist[end])} m of yard track")
    return [[round(pos[n][1] / KY, 6), round(pos[n][0] / K, 6)] for n in reversed(path)]

# Line 4 has no yard of its own: its trains run up Line 2 to Walkley.
out = {
    "1": {"name": "Belfast Yard", "path": route("1", (45.419, -75.647), (45.4155, -75.6405))},
    "2": {"name": "Walkley Yard", "path": route("2", (45.3635, -75.6605), (45.3688, -75.6466))},
}
json.dump(out, open(os.path.join(here, "depots.json"), "w"), separators=(",", ":"))
