# O-Train Live

Live map of OC Transpo O-Train Lines 1, 2 and 4. Static site (GitHub Pages → otrain.matranc.com) + a tiny Cloudflare Worker that proxies the GTFS-RT feed.

Why the Worker: OC Transpo's feed only allows CORS from its own developer portal, and the API key shouldn't ship to browsers. The Worker holds the key, edge-caches 15s, and returns O-Train vehicles as JSON.

## Setup
1. Get a key at https://nextrip-public-api.developer.azure-api.net/ (subscribe to GTFS-RT).
2. Cloudflare dashboard → Workers → Create → name it `otrain-feed`, paste `worker.js`, deploy.
   Settings → Variables → add secret `OCT_KEY` = your key.
3. Put the Worker URL in `FEED` in `index.html`.
4. DNS (Porkbun): `CNAME otrain → matranc.github.io`. Repo Settings → Pages → deploy from `main`.

## Dev
- `python3 -m http.server` then open `/?feed=<url>` to point at any feed (e.g. a local mock JSON).
- `node test_worker.mjs` checks the protobuf decoder.
- Line shapes/stations: download https://oct-gtfs-emasagcnfmcgeham.z01.azurefd.net/public-access/GTFSExport.zip, unzip, `python3 build_lines.py <dir>` → `lines.json` (station positions come from OpenStreetMap via Overpass, snapped onto the line). Rerun when OC Transpo changes service (e.g. Line 1 extensions open).
