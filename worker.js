// Cloudflare Worker: keeps the OC Transpo key secret and adds CORS. Secret: OCT_KEY.
// GET / -> { timestamp, vehicles, alerts }
//   vehicles: O-Train vehicles (none today: OC Transpo's GTFS-RT is buses only) + R1/R2/R4 replacement buses
//   alerts:   O-Train items from OC Transpo's service-updates RSS
// GET /?debug -> vehicle counts per route_id across the whole feed
const FEED = "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-vp/beta/v1/VehiclePositions";
const RSS = "https://www.octranspo.com/en/feeds/updates-en/";
const RAIL = /^R?[124](-|$)/; // trains "1-373" etc., replacement buses "R1"; no regular bus routes share these ids

export default {
  async fetch(req, env) {
    const headers = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json", "Cache-Control": "max-age=10" };
    // ponytail: edge cache (15 s feed, 2 min RSS), so every visitor shares one upstream call per colo
    const [r, rss] = await Promise.all([
      fetch(FEED, { headers: { "Ocp-Apim-Subscription-Key": env.OCT_KEY }, cf: { cacheTtl: 15, cacheEverything: true } }),
      fetch(RSS, { headers: { "User-Agent": "otrain-map" }, cf: { cacheTtl: 120, cacheEverything: true } }).then((x) => (x.ok ? x.text() : "")).catch(() => ""),
    ]);
    if (!r.ok) return new Response(JSON.stringify({ error: `upstream ${r.status}` }), { status: 502, headers });
    const buf = new Uint8Array(await r.arrayBuffer());
    if (new URL(req.url).searchParams.has("debug")) return new Response(JSON.stringify(decodeFeed(buf, true)), { headers });
    return new Response(JSON.stringify({ ...decodeFeed(buf), alerts: parseAlerts(rss) }), { headers });
  },
};

// O-Train items from the RSS: an "Important Service Notification" or a title naming O-Train / Line N / R N.
// Lines come from the affectedRoutes tag and the title. (Plain detours that merely list "1 O-Train" are skipped.)
const decode = (t) => t.replace(/<!\[CDATA\[|\]\]>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#0?39;|&rsquo;/g, "'").replace(/&nbsp;/g, " ").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&amp;/g, "&");
export function parseAlerts(xml) {
  const alerts = [];
  for (const [, item] of xml.matchAll(/<item>([\s\S]*?)<\/item>/g)) {
    const tag = (n) => decode((item.match(new RegExp(`<${n}>([\\s\\S]*?)</${n}>`)) || [, ""])[1]).trim();
    const title = tag("title"), text = tag("description").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    const routes = [...item.matchAll(/affectedRoutes-([^<]*)/g)].flatMap((m) => m[1].split(/,\s*/));
    // Real tags look like "affectedRoutes-1 O-Train, 57, 61" (seen in archived feeds).
    const lines = new Set(routes.map((x) => x.trim().match(/^R?([124])\b/)?.[1]).filter(Boolean));
    const named = /O-Train|\bLine\s*[124]\b|\bR[124]\b/i.test(title);
    if (!named && !(lines.size && /Important Service Notification/i.test(item))) continue;
    for (const m of title.matchAll(/\b(?:Line\s*|R)([124])\b/gi)) lines.add(m[1]);
    alerts.push({ title, text: text.slice(0, 280), link: tag("link"), date: tag("pubDate"), lines: [...lines].sort() });
  }
  return alerts;
}

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
