// Cloudflare Worker: proxies OC Transpo GTFS-RT VehiclePositions, keeps the key secret,
// adds CORS, and returns only O-Train vehicles as JSON. Secret: OCT_KEY.
const FEED = "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-vp/beta/v1/VehiclePositions";
const RAIL = /^[124](-|$)/; // routes 1, 2, 4 (e.g. "1-373"); no bus routes share these ids

export default {
  async fetch(req, env) {
    const headers = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json", "Cache-Control": "max-age=10" };
    // ponytail: edge cache for 15s, so every visitor shares one upstream call per colo
    const r = await fetch(FEED, { headers: { "Ocp-Apim-Subscription-Key": env.OCT_KEY }, cf: { cacheTtl: 15, cacheEverything: true } });
    if (!r.ok) return new Response(JSON.stringify({ error: `upstream ${r.status}` }), { status: 502, headers });
    return new Response(JSON.stringify(decodeFeed(new Uint8Array(await r.arrayBuffer()))), { headers });
  },
};

// Minimal protobuf reader for the GTFS-RT fields we use.
function* fields(buf, start = 0, end = buf.length) {
  let i = start;
  const varint = () => { let v = 0, m = 1, b; do { b = buf[i++]; v += (b & 127) * m; m *= 128; } while (b & 128); return v; };
  while (i < end) {
    const key = varint(), no = Math.floor(key / 8), wt = key & 7;
    if (wt === 0) yield [no, varint()];
    else if (wt === 1) { yield [no, null]; i += 8; }
    else if (wt === 2) { const len = varint(); yield [no, [i, i + len]]; i += len; }
    else if (wt === 5) { yield [no, new DataView(buf.buffer, buf.byteOffset + i, 4).getFloat32(0, true)]; i += 4; }
    else throw new Error("bad wire type " + wt);
  }
}
const str = (buf, [a, b]) => new TextDecoder().decode(buf.subarray(a, b));
const msg = (buf, [a, b], f) => { for (const [no, v] of fields(buf, a, b)) f(no, v); };

export function decodeFeed(buf) {
  let timestamp = 0;
  const vehicles = [];
  for (const [no, v] of fields(buf)) {
    if (no === 1) msg(buf, v, (n, x) => { if (n === 3) timestamp = x; }); // header.timestamp
    if (no !== 2) continue; // entity
    msg(buf, v, (n, x) => {
      if (n !== 4) return; // entity.vehicle
      const o = {};
      msg(buf, x, (vn, vx) => {
        if (vn === 1) msg(buf, vx, (t, y) => { if (t === 1) o.trip_id = str(buf, y); if (t === 5) o.route_id = str(buf, y); if (t === 6) o.direction_id = y; });
        if (vn === 2) msg(buf, vx, (p, y) => { if (p === 1) o.lat = y; if (p === 2) o.lon = y; if (p === 3) o.bearing = y; if (p === 5) o.speed = y; });
        if (vn === 4) o.status = ["INCOMING_AT", "STOPPED_AT", "IN_TRANSIT_TO"][vx];
        if (vn === 5) o.timestamp = vx;
        if (vn === 7) o.stop_id = str(buf, vx);
        if (vn === 8) msg(buf, vx, (d, y) => { if (d === 1) o.id = str(buf, y); if (d === 2) o.label = str(buf, y); });
      });
      if (RAIL.test(o.route_id || "") && o.lat) vehicles.push(o);
    });
  }
  return { timestamp, vehicles };
}
