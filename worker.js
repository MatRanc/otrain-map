// Cloudflare Worker: proxies OC Transpo GTFS-RT VehiclePositions, keeps the key secret,
// adds CORS, and returns only O-Train vehicles as JSON. Secret: OCT_KEY.
const FEED = "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-vp/beta/v1/VehiclePositions";
const TRIPS = "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-tp/beta/v1/TripUpdates";
const RAIL = /^[124](-|$)/; // routes 1, 2, 4 (e.g. "1-373"); no bus routes share these ids

export default {
  async fetch(req, env) {
    const headers = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json", "Cache-Control": "max-age=10" };
    if (new URL(req.url).searchParams.has("tu")) {
      const t = await fetch(TRIPS, { headers: { "Ocp-Apim-Subscription-Key": env.OCT_KEY }, cf: { cacheTtl: 15, cacheEverything: true } });
      if (!t.ok) return new Response(JSON.stringify({ error: `upstream ${t.status}` }), { status: 502, headers });
      return new Response(JSON.stringify(decodeTrips(new Uint8Array(await t.arrayBuffer()))), { headers });
    }
    // ponytail: edge cache for 15s, so every visitor shares one upstream call per colo
    const r = await fetch(FEED, { headers: { "Ocp-Apim-Subscription-Key": env.OCT_KEY }, cf: { cacheTtl: 15, cacheEverything: true } });
    if (!r.ok) return new Response(JSON.stringify({ error: `upstream ${r.status}` }), { status: 502, headers });
    const buf = new Uint8Array(await r.arrayBuffer());
    // ?debug: vehicle counts per route_id across the whole feed (buses included), for checking route ids.
    if (new URL(req.url).searchParams.has("debug")) return new Response(JSON.stringify(decodeFeed(buf, true)), { headers });
    return new Response(JSON.stringify(decodeFeed(buf)), { headers });
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

export function decodeFeed(buf, debug = false) {
  const routes = {};
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
      routes[o.route_id] = (routes[o.route_id] || 0) + 1;
      if (debug && !o.route_id) (routes.noroute ||= []).push([o.id, o.label, o.lat, o.lon]);
      if (RAIL.test(o.route_id || "") && o.lat) vehicles.push(o);
    });
  }
  return debug ? { timestamp, total: Object.values(routes).reduce((a, b) => a + b, 0), routes } : { timestamp, vehicles };
}

// TripUpdates: rail trips with predicted stop times.
export function decodeTrips(buf) {
  const trips = [], routes = {};
  for (const [no, v] of fields(buf)) {
    if (no !== 2) continue;
    msg(buf, v, (n, x) => {
      if (n !== 3) return; // entity.trip_update
      const o = { stops: [] };
      msg(buf, x, (tn, tx) => {
        if (tn === 1) msg(buf, tx, (t, y) => { if (t === 1) o.trip_id = str(buf, y); if (t === 5) o.route_id = str(buf, y); if (t === 6) o.direction_id = y; });
        if (tn === 2) { const s = {}; msg(buf, tx, (sn, sx) => {
          if (sn === 1) s.seq = sx; if (sn === 4) s.stop_id = str(buf, sx);
          if (sn === 2 || sn === 3) msg(buf, sx, (e, ex) => { if (e === 2) s[sn === 2 ? "arr" : "dep"] = ex; });
        }); o.stops.push(s); }
        if (tn === 3) msg(buf, tx, (d, y) => { if (d === 1) o.vehicle = str(buf, y); });
        if (tn === 4) o.timestamp = tx;
      });
      routes[o.route_id] = (routes[o.route_id] || 0) + 1;
      if (RAIL.test(o.route_id || "")) trips.push(o);
    });
  }
  return { routes, trips };
}
