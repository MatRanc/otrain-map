// Run: node test_worker.mjs — encodes a tiny GTFS-RT feed by hand and checks decodeFeed.
import assert from "node:assert";
import { decodeFeed } from "./worker.js";
const vi = (n) => { const o = []; do { let b = n % 128; n = Math.floor(n / 128); o.push(n ? b | 128 : b); } while (n); return o; };
const tag = (no, wt) => vi(no * 8 + wt);
const s = (no, t) => { const b = [...new TextEncoder().encode(t)]; return [...tag(no, 2), ...vi(b.length), ...b]; };
const m = (no, b) => [...tag(no, 2), ...vi(b.length), ...b];
const f = (no, x) => { const d = new DataView(new ArrayBuffer(4)); d.setFloat32(0, x, true); return [...tag(no, 5), ...new Uint8Array(d.buffer)]; };
const v = (no, x) => [...tag(no, 0), ...vi(x)];
const veh = (route, lat) => m(2, [...s(1, "e"), ...m(4, [
  ...m(1, [...s(1, "trip9"), ...s(5, route), ...v(6, 1)]),
  ...m(2, [...f(1, lat), ...f(2, -75.7), ...f(3, 90)]),
  ...v(4, 1), ...v(5, 1760000000), ...s(7, "CD995"), ...m(8, [...s(1, "1105"), ...s(2, "Train 5")]),
])]);
const feed = new Uint8Array([...m(1, [...s(1, "2.0"), ...v(3, 1760000001)]), ...veh("1-373", 45.4), ...veh("95", 45.3), ...veh("2-354", 45.35)]);
const out = decodeFeed(feed);
assert.equal(out.timestamp, 1760000001);
assert.deepEqual(out.vehicles.map((x) => x.route_id), ["1-373", "2-354"]);
const t = out.vehicles[0];
assert.equal(t.trip_id, "trip9"); assert.equal(t.direction_id, 1); assert.equal(t.status, "STOPPED_AT");
assert.equal(t.id, "1105"); assert.equal(t.label, "Train 5"); assert.equal(t.stop_id, "CD995");
assert.ok(Math.abs(t.lat - 45.4) < 1e-5 && t.bearing === 90 && t.timestamp === 1760000000);
console.log("ok");
