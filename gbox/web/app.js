// Goldshell Box dashboard. Works opened as a file (live status straight from the miner) and
// served by `gbox serve` (adds the fan/temperature history, the event log and the token hand-off).

// ---- data layer: no DOM access here ----
const KEY = new TextEncoder().encode("!!!!!!!!!!!!!!!!");
async function encryptPassword(pw, subtle) {
  // The firmware wants AES-128-CBC, zero IV, ZERO padding, hex. WebCrypto only does PKCS#7, so
  // encrypt the zero-padded text and drop the trailing PKCS#7 block: the CBC chain is identical up to there.
  const raw = new TextEncoder().encode(pw), padLen = Math.max(16, Math.ceil(raw.length / 16) * 16);
  const padded = new Uint8Array(padLen); padded.set(raw);
  const key = await subtle.importKey("raw", KEY, { name: "AES-CBC" }, false, ["encrypt"]);
  const ct = new Uint8Array(await subtle.encrypt({ name: "AES-CBC", iv: new Uint8Array(16) }, key, padded));
  return Array.from(ct.slice(0, padLen), b => b.toString(16).padStart(2, "0")).join("");
}
async function login(base, pw, subtle) {
  const enc = await encryptPassword(pw, subtle);
  const r = await fetch(base + "/user/login?username=admin&password=" + enc + "&cipher=true");
  if (!r.ok) throw new Error("login failed: HTTP " + r.status);
  const tok = (await r.json())["JWT Token"];
  if (typeof tok !== "string" || tok.split(".").length !== 3) throw new Error("login rejected (wrong password?)");
  return tok;
}
async function apiText(base, token, path, attempt) {
  const r = await fetch(base + "/" + path, { headers: { Authorization: "Bearer " + token } });
  if (r.status === 401) {
    // The token check races when another client (the gbox poller) hits the miner at the same moment.
    // Retry twice before calling it a bad session.
    attempt = attempt || 0;
    if (attempt < 2) { await new Promise(res => setTimeout(res, 700 * (attempt + 1))); return apiText(base, token, path, attempt + 1); }
    throw new Error("401");
  }
  if (!r.ok) throw new Error(path + ": HTTP " + r.status);
  return r.text();
}
function kv(txt, key) {
  const esc = key.replace(/[-%]/g, "\\$&");
  const m = txt.match(new RegExp("\\[" + esc + "\\] => ([^\\n]+)"));
  return m ? m[1].trim() : null;
}
// Per-board record shape, mirroring gbox/api.py's _MINERINFO_FIELDS exactly (same key names, camelCase).
const MINERINFO_FIELDS = {
  elapsed: "Device Elapsed", mhsAv: "MHS av", mhs20: "MHS 20s", accepted: "Accepted", rejected: "Rejected",
  hwErrors: "Hardware Errors", hwPct: "Device Hardware%", clock: "clock", fan0: "fan0", fan1: "fan1",
  chipTemp: "tstemp-0", chipTemp1: "tstemp-1", boardTemp: "tstemp-2", rebootcnt: "rebootcnt", overheat: "overheat",
};
const PGA_RE = /^\[PGA(\d+)\] =>/gm;
function numOrNull(v) { return v === null ? null : parseFloat(v); }
// fan0, fan1, ... until the first gap (fan0..fan7 at most). Mirrors api.py's _fan_list.
function fanList(read) {
  const fans = [];
  for (let n = 0; n < 8; n++) {
    const v = read(n);
    if (v === null) break;
    fans.push(v);
  }
  return fans;
}
// One per-board record from a `[PGAn] =>` block (or, for a firmware that writes none, the whole text).
// voltage/current are unit-level: on the text transport they appear once, in [STATUS], outside any [PGAn]
// block, so they are read from unitText (the whole response) rather than from block. Mirrors api.py's _board_from_kv.
function boardFromKv(block, unitText, index) {
  const b = {};
  for (const name in MINERINFO_FIELDS) b[name] = numOrNull(kv(block, MINERINFO_FIELDS[name]));
  b.board = index;
  b.nonced = numOrNull(kv(block, "Nonced"));
  b.fans = fanList(n => numOrNull(kv(block, "fan" + n)));
  b.voltageMv = numOrNull(kv(unitText, "voltage"));
  b.currentMa = numOrNull(kv(unitText, "current"));
  return b;
}
// One dict per `[PGAn] =>` block of /dbg/minerinfo, in order. No block: one dict from the whole text (a
// firmware that writes no PGA headers still gets today's behaviour). Mirrors api.py's parse_minerinfo_boards.
function parseMinerInfoBoards(txt) {
  PGA_RE.lastIndex = 0;
  const heads = [];
  let m;
  while ((m = PGA_RE.exec(txt))) heads.push({ index: parseInt(m[1]), start: m.index });
  if (!heads.length) return [boardFromKv(txt, txt, 0)];
  return heads.map((h, i) => boardFromKv(txt.slice(h.start, i + 1 < heads.length ? heads[i + 1].start : txt.length), txt, h.index));
}
// Index of the board with the highest chipTemp (ties keep the earlier board); 0 when none is known.
function hottestIndex(boards) {
  let bestI = 0, bestT = null;
  boards.forEach((b, i) => { if (b.chipTemp !== null && (bestT === null || b.chipTemp > bestT)) { bestI = i; bestT = b.chipTemp; } });
  return bestI;
}
// The dict every caller of parseMinerInfo uses. One board: that board's fields, unchanged. Several: sums for
// hashrates, shares and errors; hwPct as errors over nonces (percent) when every board's nonce count is known,
// else the mean of the boards' own percentages; elapsed the max; clock the first non-None; rebootcnt and
// overheat the max; chipTemp, chipTemp1 and boardTemp from the hottest board (max chipTemp); fan0/fan1 the
// first two of the unit's fans. New keys: fans (list), nboards, hotBoard (index), wattsDc (mV*mA/1e6, or null).
// Mirrors api.py's board_totals exactly.
function boardTotals(boards) {
  let out = {};
  if (boards.length === 1) {
    for (const name in MINERINFO_FIELDS) out[name] = boards[0][name];
  } else {
    const sum = vals => vals.length ? vals.reduce((a, b) => a + b, 0) : null;
    const elapsed = boards.map(b => b.elapsed).filter(v => v !== null);
    out.elapsed = elapsed.length ? Math.max.apply(null, elapsed) : null;
    ["mhsAv", "mhs20", "accepted", "rejected", "hwErrors"].forEach(key => { out[key] = sum(boards.map(b => b[key]).filter(v => v !== null)); });
    const nonced = boards.map(b => b.nonced);
    if (nonced.every(n => n !== null) && sum(nonced) > 0 && boards.every(b => b.hwErrors !== null)) {
      out.hwPct = 100.0 * sum(boards.map(b => b.hwErrors)) / sum(nonced);
    } else {
      const pcts = boards.map(b => b.hwPct).filter(v => v !== null);
      out.hwPct = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : null;
    }
    const withClock = boards.find(b => b.clock !== null);
    out.clock = withClock ? withClock.clock : null;
    const rebootcnt = boards.map(b => b.rebootcnt).filter(v => v !== null);
    out.rebootcnt = rebootcnt.length ? Math.max.apply(null, rebootcnt) : null;
    const overheat = boards.map(b => b.overheat).filter(v => v !== null);
    out.overheat = overheat.length ? Math.max.apply(null, overheat) : null;
    const hot = boards[hottestIndex(boards)];
    out.chipTemp = hot.chipTemp; out.chipTemp1 = hot.chipTemp1; out.boardTemp = hot.boardTemp;
    const fans0 = boards[0].fans;
    out.fan0 = fans0.length > 0 ? fans0[0] : null;
    out.fan1 = fans0.length > 1 ? fans0[1] : null;
  }
  out.fans = boards.length ? boards[0].fans : [];
  out.nboards = boards.length;
  out.hotBoard = hottestIndex(boards);
  const v = boards[0].voltageMv, c = boards[0].currentMa;
  out.wattsDc = (v !== null && c !== null) ? v * c / 1e6 : null;
  return out;
}
function parseMinerInfo(txt) { return boardTotals(parseMinerInfoBoards(txt)); }
// A per-board record from /api/boards (the Python service's snake_case dict) or from parseMinerInfoBoards
// (camelCase), normalized to one row shape for the boards table.
function boardRow(b) {
  const g = (camel, snake) => (b[camel] !== undefined ? b[camel] : b[snake]);
  return { board: g("board", "board"), mhs20: g("mhs20", "mhs_20s"), mhsAv: g("mhsAv", "mhs_av"),
    accepted: g("accepted", "accepted"), rejected: g("rejected", "rejected"), hwPct: g("hwPct", "hw_pct"),
    chipTemp: g("chipTemp", "chip_temp"), boardTemp: g("boardTemp", "board_temp"), rebootcnt: g("rebootcnt", "rebootcnt") };
}
function parseBoards(icinfoText) {
  return JSON.parse(JSON.parse(icinfoText).body).drawdata.map(board => board.map(c => ({ chip: c.chipindex, good: c.perf, bad: c.hwerr })));
}
function chipHealth(c, best) {
  const total = c.good + c.bad, badPct = total ? 100 * c.bad / total : 0;
  if (c.good < 0.3 * best || badPct > 30) return "dead";
  if (c.good < 0.7 * best || c.bad > 40) return "weak";
  return "";
}
function hashUnit(mhs) { const v = mhs || 0; return v >= 1e6 ? ["TH/s", 1e6] : v >= 1e3 ? ["GH/s", 1e3] : ["MH/s", 1]; }
// Requests go to the miner one at a time, never concurrently (token-check race, see docs/firmware-api.md).
// Slow-changing endpoints are re-read only every `slowEvery` ms.
async function fetchAll(base, token, slowEvery, served) {
  const c = fetchAll.cache || (fetchAll.cache = {}), now = Date.now(), stale = !c.t || now - c.t > (slowEvery || 60000);
  const mi = await apiText(base, token, "dbg/minerinfo");
  // A 401 that persists on /dbg/icinfo loses the per-chip table, not the whole page: the SC5 Pro II's icinfo
  // support is unproven (task 5's icinfo tolerance is the service-side twin of this).
  let boards = [];
  try { boards = parseBoards(await apiText(base, token, "dbg/icinfo")); } catch (e) { boards = []; }
  if (stale) { c.st = await apiText(base, token, "mcb/setting"); c.status = await apiText(base, token, "mcb/status"); c.hist = await apiText(base, token, "cpb/hshistory"); c.t = now; }
  let boards4028 = null;
  if (served) { try { const r = await fetch("api/boards", { cache: "no-store" }); boards4028 = r.ok ? await r.json() : null; } catch (e) { boards4028 = null; } }
  return { info: parseMinerInfo(mi), miBoards: parseMinerInfoBoards(mi), boards, boards4028,
    setting: JSON.parse(c.st), status: JSON.parse(c.status), history: JSON.parse(c.hist) };
}
async function apiPut(base, token, path, body, attempt) {
  // Same 401 handling as apiText. A PUT that got 401 was not applied, so retrying it is safe.
  const headers = { Authorization: "Bearer " + token };
  if (body !== null && body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(base + "/" + path, { method: "PUT", headers, body: body === null || body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401) {
    attempt = attempt || 0;
    if (attempt < 2) { await new Promise(res => setTimeout(res, 700 * (attempt + 1))); return apiPut(base, token, path, body, attempt + 1); }
    throw new Error("401");
  }
  if (!r.ok) throw new Error("PUT " + path + ": HTTP " + r.status);
  return r.text();
}

// ---- the buttons: each builds the exact request the confirm dialog shows and the page then sends ----
// A request is { method, path, body (null for none), summary, event, password, restart }. `password` says whether
// the dialog asks for the miner password again; `event` is the line reported to the gbox service on success.
// The power plan string in every dialect seen so far (docs/firmware-api.md, "Power plan dialects"; same as gbox/api.py):
//   box "575 MHz 0.41 V 90 RPM 90 RPM" (SC-BOX), mv_pv "625 MHz 9100 V 40 RPM 40 RPM PV 9400" (SC Lite),
//   float_pv "750 MHz 0.41 V 50 RPM 50 RPM PV 9400" (the HS Box's documented form). voltsText keeps the volts token
//   verbatim so formatPlan(parsePlan(s)) is s, and the clock button sends a unit only a plan in the form it wrote itself.
const PLAN_RE = /^\s*(\d+)\s*MHz\s+([\d.]+)\s*V\s+(\d+)\s*RPM\s+(\d+)\s*RPM(?:\s+PV\s+(\d+))?\s*$/;
function parsePlan(plan) {
  const m = PLAN_RE.exec(typeof plan === "string" ? plan : "");
  if (!m || isNaN(parseFloat(m[2]))) throw new Error("not a power plan string: " + plan);
  const pv = m[5] === undefined ? null : parseInt(m[5]);
  return { mhz: parseInt(m[1]), volts: parseFloat(m[2]), fanA: parseInt(m[3]), fanB: parseInt(m[4]), pv: pv, voltsText: m[2],
           dialect: pv === null ? "box" : (m[2].includes(".") ? "float_pv" : "mv_pv") };
}
function formatPlan(p) {
  const v = (p.voltsText === undefined || p.voltsText === null) ? String(p.volts) : p.voltsText;
  return p.mhz + " MHz " + v + " V " + p.fanA + " RPM " + p.fanB + " RPM" + ((p.pv === undefined || p.pv === null) ? "" : " PV " + p.pv);
}
function withMhz(plan, mhz) { return formatPlan(Object.assign(parsePlan(plan), { mhz: mhz })); }
function presetPlan(setting, level) {
  const plans = setting.powerplans || [], hit = plans.find(p => p.level === level) || plans[0];
  return hit ? hit.info : null;
}
function currentPlan(setting) { return setting.manual ? setting.manualPowerplan : presetPlan(setting, setting.select); }
function clockRange(setting) {
  let max = 0;
  (setting.powerplans || []).forEach(p => { try { max = Math.max(max, parsePlan(p.info).mhz); } catch (e) {} });
  let current = null;
  try { current = parsePlan(currentPlan(setting)).mhz; } catch (e) {}
  return { min: 300, max: max || 725, step: 25, current };
}
function planRequest(setting, mhz) {
  const range = clockRange(setting);
  if (typeof mhz !== "number" || !Number.isInteger(mhz) || mhz % range.step || mhz < range.min || mhz > range.max)
    throw new Error("clock must be a multiple of " + range.step + " between " + range.min + " and " + range.max + " MHz");
  if (setting.manual && range.current === mhz) throw new Error("the clock is already " + mhz + " MHz");
  let cur;
  try { cur = parsePlan(setting.manualPowerplan); } catch (e) { cur = parsePlan(presetPlan(setting, 0)); }
  const was = currentPlan(setting), plan = formatPlan(Object.assign({}, cur, { mhz }));
  const body = Object.assign({}, setting, { manual: true, manualPowerplan: plan });
  return { method: "PUT", path: "mcb/setting", body, password: true, restart: false, changes: settingDiff(setting, body),
    summary: "plan " + was + (setting.manual ? "" : " (preset)") + " → " + plan + " (manual)",
    event: "clock set to " + mhz + " MHz (plan \"" + plan + "\", was \"" + was + "\")" };
}
function fanRange(setting) {
  const t = Array.isArray(setting.temp_targets) && setting.temp_targets.length >= 2 ? setting.temp_targets : [65, 75];
  return { min: t[0], max: t[1], current: setting.temp_target === undefined ? null : setting.temp_target };
}
function fanTargetRequest(setting, temp) {
  const range = fanRange(setting);
  if (typeof temp !== "number" || !Number.isInteger(temp) || temp < range.min || temp > range.max)
    throw new Error("fan target must be a whole number between " + range.min + " and " + range.max + " C on this firmware");
  if (range.current === temp) throw new Error("the fan target is already " + temp + " C");
  const body = Object.assign({}, setting, { temp_target: temp });
  return { method: "PUT", path: "mcb/setting", body, password: false, restart: false, changes: settingDiff(setting, body),
    summary: "fan target " + range.current + " °C → " + temp + " °C (board sensor)",
    event: "fan target set to " + temp + " C (was " + range.current + ")" };
}
// The firmware's preset table. A 0 MHz plan is probably an idle mode; nobody has tested it, so it is flagged.
// profile.plan_names (the model table's stock-UI names, e.g. the SC5 Pro II's "Hashrate Mode") names a level
// when the model has one on record; name is null otherwise, including for every model without a profile.
function presetList(setting, profile) {
  const names = profile && profile.plan_names;
  return (setting.powerplans || []).map(p => {
    let mhz = null;
    try { mhz = parsePlan(p.info).mhz; } catch (e) {}
    const row = { level: p.level, info: p.info, mhz, unverified: !(mhz > 0) };
    if (profile !== undefined) row.name = names ? (names[p.level] || null) : null;   // no profile arg at all: today's exact shape
    return row;
  });
}
function presetRequest(setting, level) {
  const preset = presetList(setting).find(p => p.level === level);
  if (!preset) throw new Error("no preset level " + level + " on this firmware");
  if (!setting.manual && setting.select === level) throw new Error("already on preset " + level);
  const was = setting.manual ? "manual \"" + setting.manualPowerplan + "\"" : "preset " + setting.select + " (" + presetPlan(setting, setting.select) + ")";
  const body = Object.assign({}, setting, { select: level, manual: false });
  return { method: "PUT", path: "mcb/setting", body, password: true, restart: false, changes: settingDiff(setting, body),
    summary: "plan " + currentPlan(setting) + (setting.manual ? " (manual)" : " (preset " + setting.select + ")") + " → " + preset.info + " (preset " + level + ")",
    event: "switched to preset " + level + " (" + preset.info + "); was " + was };
}
function restartRequest() {
  return { method: "PUT", path: "mcb/restart", body: null, password: true, restart: true, changes: [],
    summary: "soft restart: the controller reboots and hashing resumes after 60-90 s", event: "soft restart sent" };
}
// Requests to the gbox service rather than the miner (kind "service"): the plug and the hold. Off and cycle carry the
// password in the encrypted form the miner's own login takes; the service checks it by logging in. On needs none:
// turning a miner on is what the watchdog already does unasked. See docs/power-hold-proposal.md.
function powerActionRequest(action, service, offSeconds) {
  if (!["off", "on", "cycle"].includes(action)) throw new Error("action must be off, on or cycle");
  const p = (service && service.power) || {}, lad = (service && service.ladder) || {};
  const plug = (p.alias ? "'" + p.alias + "'" : "the plug") + " (" + (p.model || "?") + ")";
  const idle = lad.idle_watts != null ? lad.idle_watts : 100;
  const reading = p.meter && p.watts != null ? "reading " + Math.round(p.watts) + " W now" + (p.watts >= idle ? " (that looks like a miner hashing, not a hung one)" : "") : "no meter";
  const settle = lad.settle_minutes != null ? lad.settle_minutes + " min" : "the settle gap";
  const body = { action };
  let summary, title;
  if (action === "off") { title = "Switch the plug off"; summary = "switch off " + plug + ", " + reading + "; the miner stays off, unjudged, until you press On"; }
  else if (action === "on") { title = "Switch the plug on"; summary = "switch on " + plug + "; the watchdog waits " + settle + " for the boot before judging anything"; }
  else {
    title = "Power cycle"; if (offSeconds != null) body.off_seconds = offSeconds;
    summary = "cut power to " + plug + " for " + (offSeconds != null ? offSeconds : "a few") + " s, then restore it; " + reading + "; the watchdog waits " + settle + " for the boot";
  }
  return { kind: "service", method: "POST", path: "api/power", body, password: action !== "on", changes: [], restart: false, title, event: null, summary };
}
function holdRequest(minutes, reason) {
  const r = reason || "", timed = minutes != null;
  const summary = (timed ? "hold for " + minutes + " min" : "hold with no expiry") + (r ? " (" + r + ")" : "") +
    ": the watchdog judges nothing until the miner hashes twice in a row, or " + (timed ? minutes + " min pass" : "you press Release");
  return { kind: "service", method: "POST", path: "api/hold", body: { minutes: timed ? minutes : null, reason: r }, password: false, changes: [], restart: false, title: "Hold", event: null, summary };
}
function holdReleaseRequest() {
  return { kind: "service", method: "POST", path: "api/hold/release", body: {}, password: false, changes: [], restart: false, title: "Release the hold", event: null,
    summary: "release the hold: the watchdog judges again once it has a fresh window of samples" };
}
// Top-level fields that differ between what the miner holds and what will be sent: the part of the body to read.
function settingDiff(before, after) {
  const keys = Object.keys(Object.assign({}, before, after));
  return keys.filter(k => JSON.stringify(before[k]) !== JSON.stringify(after[k])).map(k => ({ key: k, from: before[k], to: after[k] }));
}
function describeRequest(base, req) {
  return req.method + " " + base + "/" + req.path + "\n" + (req.body === null || req.body === undefined ? "(no body)" : JSON.stringify(req.body, null, 1));
}
// Event-log lines worth a marker on the fan chart: what the buttons did, what the watchdog did, what the plug did,
// and each service start, so a gap in the traces reads as "the service was down" rather than "the miner was".
function eventMarkers(text) {
  const out = [];
  (text || "").split(/\r?\n/).forEach(line => {
    const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) ((?:dashboard|watchdog|power|hold): .*|service: started .*)$/.exec(line);
    if (m) out.push({ t: new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime(), label: m[7] });
  });
  return out;
}
// Which glyph row an event line sits on, on the 24-hour charts: "top" (above the plot: P for a plug cycle or a
// deliberate switch from the page, H for a hold's start), "in" (inside the top edge: ▼ W S), or null for a line
// that gets no mark at all (plug back or unreachable, a hold's release: they stay in the interventions table).
function markerRow(label) {
  if (label.startsWith("power: ")) return /^power: (cycled|switched)/.test(label) ? "top" : null;
  if (label.startsWith("hold: ")) return label.startsWith("hold: started") ? "top" : null;
  return "in";
}
// Glyphs closer than minGap px to the previous kept glyph are dropped (their lines stay), so a storm reads as one
// letter over a comb of lines. Returns one boolean per x, in order.
function dropClose(xs, minGap) {
  let last = -Infinity;
  return xs.map(x => { if (x - last < minGap) return false; last = x; return true; });
}
function markerGlyph(label) {
  return label.startsWith("service") ? "S" : label.startsWith("power") ? "P" : label.startsWith("watchdog") ? "W" : label.startsWith("hold") ? "H" : "▼";
}
// One vocabulary, used by the key under every chart, the marker hover titles, the explainer and the Terms section:
// a board reset is the miner's own doing (drawn as bars, never a marker); a soft restart (W, or ▼ when you pressed it)
// and a power cycle (P) are what gbox did to the miner. Same order as the ladder.
const MARKER_KEY = [["▼", "you, from Controls"], ["W", "watchdog soft restart"], ["P", "plug power cycle"], ["S", "service start"], ["H", "hold"]];
function markerKind(label) { const g = markerGlyph(label); return (MARKER_KEY.find(k => k[0] === g) || MARKER_KEY[0])[1]; }
function markerTitle(t, label) { return new Date(t).toLocaleTimeString() + " · " + markerKind(label) + " · " + label; }
// The key's items for a chart: marker glyphs (or one line about worded labels), the resets bar swatch, the alarm band swatch.
function chartKey(opts) {
  const o = opts || {}, items = [];
  if (o.words) items.push({ text: "labels: what you or the plug did, by name" });
  else { MARKER_KEY.forEach(k => items.push({ glyph: k[0], text: k[1] })); items.push({ text: "P and H sit above the plot, ▼ W S inside its top edge; a run of the same mark shows one letter" }); }
  if (o.bars) items.push({ swatch: "bar", text: "board resets: the miner reinitializing its own hashboard; nothing gbox did" });
  if (o.band) items.push({ swatch: "band", text: "alarm: a board reset, or bad share over 1%, in that bucket" });
  if (o.temps) {      // the temperature panel's three series and its guide line, hottest last, as the plan's colour table
    items.push({ swatch: "amber", text: "chips avg: the board's mean chip temperature, from the cgminer log" });
    items.push({ swatch: "hot", text: "hottest chip: sustained level, the 5-minute median of 5-second readings" });
    items.push({ swatch: "hotband", text: "up to that bucket's peak reading" });
    items.push({ swatch: "guide", text: "serious at " + o.temps + " °C sustained" });
  }
  return items;
}
// The plug, as /api/health reports it: appended to the service line. Empty without a plug.
function powerLine(service) {
  const p = (service && service.power) || {};
  if (!p.configured) return "";
  const state = p.busy ? "cycling" : p.state == null ? null : (p.state === "off" && p.off_by_you ? "off by you" : p.state);
  const reading = state === null ? "unreachable" :
    state + ", " + (p.meter && p.watts != null ? Math.round(p.watts) + " W" : "no meter");
  return " · plug " + (p.model || "?") + " " + reading + ", " + (p.cycle ? "armed" : "dry run") + ", " +
    p.cycles_today + (p.cycles_today === 1 ? " cycle" : " cycles") + " today";
}
// ---- clock trials table (rows come from /api/trials; rollup rows carry bad_pct_min/max and segments, segment rows carry bad_pct) ----
const TRIAL_COLUMNS = ["clock", "fan target", "from", "held", "worst chip, bad share", "bad/hour", "board resets", "HW error", "accepted/hr", "hashrate", "watts", "GH/s per W", "chip temp · fans"];
function trialDuration(minutes) {
  const m = Math.round(minutes);
  if (m < 60) return m + " min";
  const h = Math.floor(m / 60), d = Math.floor(h / 24);
  return (d ? d + "d " : "") + (h % 24) + "h " + String(m % 60).padStart(d ? 2 : 1, "0") + "m";
}
function trialCells(r) {
  const hi = r.bad_pct_max === undefined ? r.bad_pct : r.bad_pct_max, lo = r.bad_pct_min === undefined ? r.bad_pct : r.bad_pct_min;
  let chip = "none flagged", chipCls = "";
  if (r.worst_chip !== null && r.worst_chip !== undefined && hi !== null) {
    const range = (lo === null || Math.abs(hi - lo) < 0.05) ? hi.toFixed(1) + "%" : lo.toFixed(1) + "-" + hi.toFixed(1) + "%";
    chip = "chip " + r.worst_chip + " " + (r.bad_partial ? "≥" : "") + range;
    chipCls = hi > 5 ? "critical" : hi > 1 ? "serious" : "";
  }
  const [unit, div] = hashUnit(r.mhs);
  const held = trialDuration(r.minutes) + (r.segments > 1 ? " · " + r.segments + " segs" : "") + (r.last ? " · live" : "");
  return [
    { text: r.clock + " MHz", cls: "" },
    { text: r.fan_target === null || r.fan_target === undefined ? "?" : r.fan_target + " °C", cls: "" },
    { text: r.start.slice(5, 16), cls: "" },
    { text: held, cls: "" },
    { text: chip, cls: chipCls },
    { text: r.bad_per_hour.toFixed(1), cls: "" },
    { text: String(r.resets), cls: r.resets > 0 ? "critical" : "" },
    { text: (r.hw_approx ? "~" : "") + r.hw_pct.toFixed(2) + "%", cls: "" },
    { text: String(Math.round(r.accepted_per_hour)), cls: "" },
    { text: Math.round(r.mhs / div) + " " + unit, cls: "" },
    { text: r.watts === null || r.watts === undefined ? "?" : Math.round(r.watts) + " W", cls: "" },       // "?": no meter reading in that run
    { text: r.gh_per_w === null || r.gh_per_w === undefined ? "?" : r.gh_per_w.toFixed(2), cls: "" },
    { text: r.chip_temp.toFixed(1) + " °C · " + Math.round(r.fan_rpm) + " RPM" + (r.overheat ? " · " + r.overheat + " overheat" : ""), cls: r.overheat ? "serious" : "" },
  ];
}
// One line about a running `gbox trials run`, from its progress file (/api/trial); "" when none is running.
function trialStatus(run, nowMs) {
  if (!run || !run.step || !run.clocks) return "";
  const parseTs = s => { const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(s || ""); return m ? new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : NaN; };
  const hoursText = h => h % 1 === 0 ? h + "h" : trialDuration(h * 60);
  const progress = run.status === "holding" && !isNaN(parseTs(run.step_started))
    ? trialDuration(Math.max(0, nowMs - parseTs(run.step_started)) / 60000) + " of " + hoursText(run.hours) + " held"
    : "settling, then " + hoursText(run.hours) + " held";
  return "Trial running: step " + run.step + " of " + run.clocks.length + ", " + run.clock + " MHz, " + progress + ", ends at " + run.end + " MHz. " +
    "Started from the command line; Ctrl-C there stops it.";
}
// The Power tile: the wall reading with the plug's name and model, or what stands in for them.
// title is the tile's tooltip text; only the firmware-DC fallback sets one.
function powerTile(service, info) {
  const p = (service && service.power) || {};
  if (!p.configured) {
    if (info && typeof info.wattsDc === "number") {
      return { value: "≈ " + Math.round(info.wattsDc) + " W DC (firmware)", sub: "",
        title: "the firmware's own voltage x current; DC side, not the wall; the unit of the current field is inferred, not documented" };
    }
    return { value: "no plug", sub: "see docs/power-cycle.md" };
  }
  const name = (p.alias ? p.alias + " · " : "") + (p.model || "?");
  if (p.state == null) return { value: "unreachable", sub: name };
  if (!p.meter || p.watts == null) return { value: p.state, sub: name + " · no meter" };
  const rated = service.rated && service.rated.rated_watts;
  return { value: Math.round(p.watts) + " W", sub: name + (rated ? " · of " + Math.round(rated) + " W rated" : "") };
}
// Same rounding/grouping as the page's own fmt(), usable from the pure data layer (no DOM) for testable tiles.
function fmtNum(v, d) { return (v === null || v === undefined || (typeof v === "number" && isNaN(v))) ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
// The Fans tile's value/sub text. showPct (the firmware fan-controller duty cycle) only ever shows when the
// profile has a fan_max_rpm on record; the SC5 Pro II's is null, so its tile never claims a percent of nothing.
function fansTileText(fans, fanPct, fanMaxRpm) {
  const showPct = fanPct !== null && fanPct !== undefined && !!fanMaxRpm;
  const value = (showPct ? fmtNum(fanPct) + " % · " : "") + fans.map(f => fmtNum(f)).join(" / ");
  const labels = fans.map((_, i) => "fan" + i).join(" / ");
  const sub = (showPct ? "% · " : "") + "RPM " + labels;
  return { value, sub };
}
// The Hottest-chip tile's second line when there is no served log yet (the !hot branch of renderHottest).
// One board: today's "chips avg N °C · N °C board sensor" unchanged. Several: names the hottest and coolest boards.
function hotsubText(info, boards) {
  const b = v => "<b class=\"v2\">" + v + "</b>";
  const board = b(fmtNum(info.boardTemp, 1) + " °C") + " board sensor";
  if (!boards || boards.length <= 1) return "chips avg " + b(fmtNum(info.chipTemp) + " °C") + " · " + board;
  let coolest = null;
  boards.forEach(x => { if (x.chipTemp !== null && (coolest === null || x.chipTemp < coolest.chipTemp)) coolest = x; });
  const coolestPart = coolest ? " · coolest board " + coolest.board + " " + b(fmtNum(coolest.chipTemp) + " °C") : "";
  return "chips avg " + b(fmtNum(info.chipTemp) + " °C") + " on board " + info.hotBoard + " (hottest)" + coolestPart + " · " + board;
}
// Rows of the service log (api/log.csv) for the charts and the interventions table. Columns by header name, so an older
// log without a column still parses; a blank watts cell is null (the plug did not answer, or no meter), never 0.
// Rows that failed to sample are kept with ok:false so an outage is visible; their numbers are NaN.
function envRowsFrom(text) {
  const lines = (text || "").trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const head = lines[0].split(","), ix = k => head.indexOf(k);
  const it = ix("time"), ih = ix("http"), if0 = ix("fan0"), if1 = ix("fan1"), ic = ix("tstemp0"), ib = ix("tstemp2"), iw = ix("watts");
  const ie = ix("hwerr"), ia = ix("accepted"), ir = ix("rebootcnt"), iel = ix("elapsed");
  const ihp = ix("hot_peak"), ihl = ix("hot_level"), ica = ix("chip_avg");   // 0.7.0: set on the rows that read the cgminer log
  const num = s => (s === "" || s === undefined) ? NaN : +s;
  const opt = (r, i) => (i < 0 || r.length <= i || r[i] === "") ? null : +r[i];
  return lines.slice(1).map(l => l.split(",")).filter(r => r.length > ib && r[it])
    .map(r => ({ t: new Date(r[it].replace(" ", "T")).getTime(), ok: r[ih] === "ok",
      fan0: num(r[if0]), fan1: num(r[if1]), chip: num(r[ic]), board: num(r[ib]), elapsed: num(r[iel]),
      hwerr: num(r[ie]), accepted: num(r[ia]), rebootcnt: num(r[ir]),
      watts: opt(r, iw), hot_peak: opt(r, ihp), hot_level: opt(r, ihl), chip_avg: opt(r, ica) }))
    .filter(r => !isNaN(r.t));
}
// The Hottest chip tile (0.7.0), from the service log's cgminer-log columns. The flag sits on the sustained level (the
// median of the miner's 5-second readings over one read), never on the peak: on the unit this was built against the
// peak reads 90+ a few times an hour at normal operation while the level sits at 81 to 82, so a flag on the peak would
// never go out. Highs are over the rows since `bootT` (the miner's boot, from its uptime), or over every served row
// without one. null when no row carries the columns; stale when the newest read is over 15 minutes old.
const HOT_STALE_MS = 15 * 60000;
function hottestChip(rows, now, temps, bootT) {
  const read = (rows || []).filter(r => r.ok && r.hot_level !== null && r.hot_level !== undefined).sort((a, b) => a.t - b.t);
  if (!read.length) return null;
  const last = read[read.length - 1], t = temps || {};
  const serious = t.hot_serious == null ? 85 : t.hot_serious, critical = t.hot_critical == null ? 90 : t.hot_critical;
  let levelHigh = null, peakHigh = null;
  read.filter(r => bootT == null || r.t >= bootT).forEach(r => {
    if (levelHigh === null || r.hot_level > levelHigh.v) levelHigh = { v: r.hot_level, t: r.t };
    if (r.hot_peak !== null && (peakHigh === null || r.hot_peak > peakHigh.v)) peakHigh = { v: r.hot_peak, t: r.t };
  });
  const stale = now - last.t > HOT_STALE_MS;
  return { level: last.hot_level, peak: last.hot_peak, chipAvg: last.chip_avg, t: last.t, stale: stale,
    cls: stale ? "" : last.hot_level >= critical ? "critical" : last.hot_level >= serious ? "serious" : "",
    levelHigh: levelHigh, peakHigh: peakHigh, serious: serious, critical: critical };
}
// The last hour from the service log, for the tiles: board resets, bad nonces and accepted shares as sums of the
// row-to-row increments (a boot restarts every counter at zero, so a plain difference would go negative). The last ok
// row before the hour is the base when there is one. null when the log has no interval inside the hour to measure.
function lastHour(rows, now, minutes) {
  const span = (minutes || 60) * 60000;
  const ok = (rows || []).filter(r => r.ok && !isNaN(r.rebootcnt)).sort((a, b) => a.t - b.t);
  const inside = ok.filter(r => r.t >= now - span && r.t <= now), before = ok.filter(r => r.t < now - span);
  const seq = (before.length ? [before[before.length - 1]] : []).concat(inside);
  if (!inside.length || seq.length < 2) return null;
  const inc = (a, b) => (isNaN(a) || isNaN(b)) ? 0 : (b >= a ? b - a : b);
  let resets = 0, bad = 0, accepted = 0;
  for (let i = 1; i < seq.length; i++) {
    resets += inc(seq[i - 1].rebootcnt, seq[i].rebootcnt); bad += inc(seq[i - 1].hwerr, seq[i].hwerr); accepted += inc(seq[i - 1].accepted, seq[i].accepted);
  }
  return { resets: resets, bad: bad, accepted: accepted, samples: inside.length };
}
// The mean of the last `minutes` positive samples of the miner's own per-minute buffer (MHS), or null. Works as a file too.
function recentHashrate(hist, minutes) {
  const v = (hist || []).filter(x => x > 0).slice(-minutes);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}
// Interventions: every event line where the software, or you, did something to the miner, newest first, each with what
// the log shows happened next. `rows` are envRowsFrom() rows (ok and failed samples) in any order.
const EVENT_RE = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) (\S+): (.*)$/;
function afterText(s) {
  if (s < 120) return s + " s";
  const m = Math.round(s / 60);
  return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + (m % 60) + " min";
}
function interventions(text, rows) {
  const sorted = (rows || []).slice().sort((a, b) => a.t - b.t);
  const cameBack = t => {                 // the first ok row after the first failed sample within 10 min of the event
    const down = sorted.find(r => r.t > t && r.t - t <= 600000 && !r.ok);
    if (!down) return "no outage seen in the log";
    const up = sorted.find(r => r.t > down.t && r.ok);
    return up ? "miner back after " + afterText(Math.round((up.t - t) / 1000)) : "still down at the end of the log";
  };
  const out = [];
  (text || "").split(/\r?\n/).forEach(line => {
    const m = EVENT_RE.exec(line);
    if (!m) return;
    const t = new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime(), src = m[7], msg = m[8];
    let who = null, kind = null, what = msg, result = "", x;
    if (src === "watchdog") {
      who = "watchdog";
      if ((x = /^restart #(\d+) sent \((.*)\)$/.exec(msg))) { kind = "restart"; what = "soft restart #" + x[1] + ": " + x[2]; result = cameBack(t); }
      else if ((x = /^restart attempt failed: (.*)$/.exec(msg))) { kind = "failed"; what = "soft restart failed: " + x[1]; }
      else if ((x = /^(.*), but (\d+ restarts in 24 h is the cap); not restarting$/.exec(msg))) { kind = "declined"; what = "declined: " + x[2] + " (" + x[1] + ")"; }
      else return;
    } else if (src === "power") {
      who = "plug";
      // both wordings: the line said "today" until 2026-09-18, when it became "in 24 h" (the counter was
      // always a rolling 24 h window). Old logs still render; mirrors SEED_RE in watchdog.py.
      if ((x = /^cycled (#\d+ (?:today|in 24 h): .*)$/.exec(msg))) { kind = "cycle"; what = "power cycle " + x[1]; result = cameBack(t); }
      else if ((x = /^would cycle now \((.*)\)$/.exec(msg))) { kind = "dryrun"; what = "would cycle (dry run): " + x[1]; }
      else if ((x = /^cycle failed: (.*)$/.exec(msg))) { kind = "failed"; what = "power cycle failed: " + x[1]; }
      else if ((x = /^switched (off|on) by (you|the schedule)(?: \((.*)\))?$/.exec(msg))) {
        who = x[2] === "you" ? "you" : "schedule"; kind = x[1]; what = "switched " + x[1] + (x[3] ? " (" + x[3] + ")" : ""); if (x[1] === "on") result = cameBack(t);
      }
      else if ((x = /^cycled by (you|the schedule)(?: \((.*)\))?: (.*)$/.exec(msg))) {
        who = x[1] === "you" ? "you" : "schedule"; kind = "cycle"; what = "power cycle" + (x[2] ? " (" + x[2] + ")" : "") + ": " + x[3]; result = cameBack(t);
      }
      else if ((x = /^switch (off|on) failed: (.*)$/.exec(msg))) { kind = "failed"; what = "switch " + x[1] + " failed: " + x[2]; }
      else return;
    } else if (src === "hold") {
      kind = "hold";
      if ((x = /^started by (you|the schedule) until \d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2}(?: \((.*)\))?$/.exec(msg))) { who = x[1] === "you" ? "you" : "schedule"; what = "hold until " + x[2] + (x[3] ? " (" + x[3] + ")" : ""); }
      else if ((x = /^started by (you|the schedule), no expiry(?: \((.*)\))?$/.exec(msg))) { who = x[1] === "you" ? "you" : "schedule"; what = "hold with no expiry" + (x[2] ? " (" + x[2] + ")" : ""); }
      else if (msg === "released by you") { who = "you"; what = "hold released"; }
      else if ((x = /^released, (miner (?:hashing again|back) after .*)$/.exec(msg))) { who = "service"; what = "hold released: " + x[1]; }
      else if (/^expired /.test(msg)) { who = "service"; what = "hold " + msg; }
      else return;
    } else if (src === "dashboard") {
      who = "you";
      if ((x = /^power: cycled by hand \((.*)\)$/.exec(msg))) { kind = "cycle"; what = "power cycle by hand (" + x[1] + ")"; result = cameBack(t); }
      else if ((x = /^power: switched (off|on) by hand (\(.*\))$/.exec(msg))) { kind = x[1]; what = "switched " + x[1] + " by hand " + x[2]; if (x[1] === "on") result = cameBack(t); }
      else if (/^soft restart/.test(msg)) { kind = "restart"; result = cameBack(t); }
      else kind = "action";
    } else if (src === "service") {
      if (!(x = /^started (v\S+?),/.exec(msg))) return;
      who = "service"; kind = "start"; what = "service started " + x[1];
    } else return;
    out.push({ t: t, who: who, kind: kind, what: what, result: result });
  });
  out.sort((a, b) => b.t - a.t);
  return out;
}
function interventionCounts(iv) {
  return { restarts: iv.filter(i => i.kind === "restart").length, cycles: iv.filter(i => i.kind === "cycle").length, actions: iv.filter(i => i.who === "you").length };
}
// The per-model table: rated figures for the "% of rated" axes and the capability profile behind the model seam
// (plan dialect, per-board source, whether /dbg/ answers, fan target, what the temperature target is). Same table as
// gbox/models.py, which documents the fields (a test keeps the two identical); keyed by the /mcb/status model string,
// looked up ignoring case, spaces and hyphens.
const MODELS = {
  "Goldshell-SCBox": { name: "SC-BOX", rated_mhs: 900000.0, rated_watts: 200.0, fans: 2, fan_max_rpm: 4900.0, boards: 1,
    source: "Goldshell spec via retailer listings (900 GH/s, 200 W); fan max observed on one unit", verified_string: true,
    plan_dialect: "box", board_source: "icinfo", dbg_expected: true, fan_target: true, temp_target_basis: "board_sensor",
    plan_names: null },
  "Goldshell-SCBox II": { name: "SC-BOX II", rated_mhs: 1900000.0, rated_watts: 400.0, fans: 2, fan_max_rpm: null, boards: 1,
    source: "retailer listings (kryptex, d-central, miningnow); model string not read from a unit; capabilities assumed as the SC-BOX's", verified_string: false,
    plan_dialect: "box", board_source: "icinfo", dbg_expected: true, fan_target: true, temp_target_basis: "board_sensor",
    plan_names: null },
  "Goldshell-SCLITE": { name: "SC Lite", rated_mhs: 4400000.0, rated_watts: 950.0, fans: null, fan_max_rpm: 2200.0, boards: null,
    source: "goldshell.company/sclite spec table; model string, plan dialect, devs endpoint, debug lock and fixed 85 C target from Maveth/goldshell-config (fw 2.2.0)", verified_string: false,
    plan_dialect: "mv_pv", board_source: "http_devs", dbg_expected: false, fan_target: false, temp_target_basis: "fixed",
    plan_names: null },
  "Goldshell-SC5ProⅡ": { name: "SC5 Pro II", rated_mhs: 14000000.0, rated_watts: 3300.0, fans: 4, fan_max_rpm: null, boards: 4,
    source: "Goldshell spec sheet 2026-09-15 (14 TH/s ±5%, 3300 W ±5%; low-power 10 TH/s at 2050 W); model string, plan dialect, PGA blocks, 4028 devs and plan names from a friend's unit (MCB_V3_3, fw 2.2.0, hw 30.50.SA)", verified_string: true,
    plan_dialect: "mv_pv", board_source: "icinfo", dbg_expected: true, fan_target: false, temp_target_basis: "fixed",
    plan_names: { 0: "Hashrate Mode", 2: "Low-power Mode", 3: "Idle Mode" } },
  "Goldshell-SC5Pro": { name: "SC5 Pro", rated_mhs: 11000000.0, rated_watts: 2820.0, fans: null, fan_max_rpm: null, boards: null,
    source: "Goldshell spec sheet 2026-09-15 (11 TH/s ±5%, 2820 W ±5%; low-power 8.8 TH/s at 2020 W); capabilities assumed as the SC5 Pro II's", verified_string: false,
    plan_dialect: "mv_pv", board_source: "icinfo", dbg_expected: true, fan_target: false, temp_target_basis: "fixed",
    plan_names: null },
};
const modelKey = m => String(m || "").toLowerCase().replace(/[ \-_]/g, "");
const MODELS_BY_KEY = Object.fromEntries(Object.entries(MODELS).map(([k, v]) => [modelKey(k), v]));
function ratedFor(model) { return MODELS_BY_KEY[modelKey(model)] || null; }
// The profile for a model not in the table: the SC-BOX's sampling path, no rated figures, nothing optional (models.UNKNOWN).
const UNKNOWN_PROFILE = { name: null, rated_mhs: null, rated_watts: null, fans: null, fan_max_rpm: null, boards: null,
  source: "not in the table; the SC-BOX's sampling path with every optional capability off", verified_string: false,
  plan_dialect: "box", board_source: "icinfo", dbg_expected: true, fan_target: false, temp_target_basis: "board_sensor",
  plan_names: null };
function profileFor(model) {
  const text = (typeof model === "string" && model) ? model : null, row = MODELS_BY_KEY[modelKey(model)];
  return row ? Object.assign({}, row, { known: true, model: text }) : Object.assign({}, UNKNOWN_PROFILE, { known: false, model: text, name: text });
}
// One line under the title when the model is not in the table; "" when it is, or before the model is known.
function modelNote(model) {
  if (!model || profileFor(model).known) return "";
  return model + " is not in gbox's model table: the page shows what the firmware answers, with no percent axes, and the service " +
    "samples it the SC-BOX way. A capture of your unit's answers (docs/capture-request.md in the repo) is what adds a model.";
}
function pctOf(value, rated) { return (value === null || value === undefined || !rated || rated <= 0) ? null : 100 * value / rated; }
// The miner's hashrate buffer as the chart draws it: leading zeros (slots a boot wiped) dropped, values in the display unit.
// A lone sample comes back as one point; the caller says so instead of drawing a path that has no length.
function chartData(hist) {
  const [unit, div] = hashUnit(Math.max.apply(null, hist)), vals = hist.map(v => v / div);
  const first = vals.findIndex(v => v > 0);
  return { unit: unit, div: div, data: first < 0 ? [] : vals.slice(first) };
}
// The page's own version, shown in the header: opened as a file there is no service to ask. tests/test_app_js.py keeps it equal to gbox.__version__.
// ---- the served charts over days (docs/charts-proposal.md): /api/series buckets as chart rows ----
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function parseStamp(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s || "");
  return m ? new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)).getTime() : NaN;
}
// One row per bucket for drawPanels, timed at the bucket's middle. Nulls stay null (a gap), never 0.
function seriesRows(s) {
  const half = (s.bucket_minutes || 30) * 30000;
  return (s.buckets || []).map(b => ({
    t: parseStamp(b.t) + half, label: b.t, ok: b.samples > 0, samples: b.samples, errors: b.errors,
    hashrate: b.hashrate, fan0: b.fan0, fan1: b.fan1, chip: b.chip_temp, board: b.board_temp, watts: b.watts, clock: b.clock,
    share: b.share, worst_share: b.worst ? b.worst.share : null, worst: b.worst, resets: b.resets, good: b.good, bad: b.bad,
    hot_peak: b.hot_peak, hot_level: b.hot_level, chip_avg: b.chip_avg,       // 0.7.0; undefined from an older service
  }));
}
const nfmt = (v, d) => (v === null || v === undefined || isNaN(v)) ? "—" : v.toLocaleString("en-US", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
function errorTip(row, bucketMin) {
  const from = clockLabel(row.t - bucketMin * 30000), to = clockLabel(row.t + bucketMin * 30000), win = from + " to " + to;
  if (!row.ok) return win + " · no samples";
  const clock = row.clock == null ? "" : " · " + nfmt(row.clock) + " MHz";
  if (row.bad == null) return win + " · counts need a previous sample" + clock;
  const w = row.worst ? " · worst chip " + w_text(row.worst) : "";
  return win + " · bad " + nfmt(row.bad) + " of " + nfmt(row.good + row.bad) + " (" + nfmt(row.share, 2) + "%)" + w + clock + " · " + nfmt(row.resets) + " board reset" + (row.resets === 1 ? "" : "s");
}
function w_text(w) { return w.chip + ": " + nfmt(w.bad) + " of " + nfmt(w.good + w.bad) + " (" + nfmt(w.share, 2) + "%)"; }
// The same bucket, led by what the panel under the cursor shows: resets first in the resets panel, the clock first in the clock panel.
function bucketWindow(row, bucketMin) { return clockLabel(row.t - bucketMin * 30000) + " to " + clockLabel(row.t + bucketMin * 30000); }
// A bucket worth an alarm band: bad share over 1% or any board reset, in a bucket that has samples. The three-day
// chart always drew these; since 2026-09-14 the 24-hour charts draw them too, after a 64-reset cold start at 13:13
// showed on the errors chart and nowhere else (the 5-minute means only showed its side effects).
function alarmBucket(r) { return !!(r.ok && ((r.share != null && r.share > 1) || (r.resets != null && r.resets > 0))); }
// " · N resets" for a tooltip, or "" when the bucket had none or cannot know.
function resetsSuffix(r) { return r.resets ? " · " + nfmt(r.resets) + " board reset" + (r.resets === 1 ? "" : "s") : ""; }
function resetsTip(row, bucketMin) {
  const win = bucketWindow(row, bucketMin);
  if (!row.ok) return win + " · no samples";
  const bad = row.bad == null ? "" : " · bad " + nfmt(row.bad) + " of " + nfmt(row.good + row.bad) + " (" + nfmt(row.share, 2) + "%)";
  return win + " · " + (row.resets == null ? "resets need a previous sample" : nfmt(row.resets) + " board reset" + (row.resets === 1 ? "" : "s")) + bad + (row.clock == null ? "" : " · " + nfmt(row.clock) + " MHz");
}
function clockTip(row, bucketMin) {
  const win = bucketWindow(row, bucketMin);
  if (!row.ok) return win + " · no samples";
  return win + (row.clock == null ? "" : " · " + nfmt(row.clock) + " MHz") + (row.share == null ? "" : " · bad " + nfmt(row.share, 2) + "%") + (row.resets == null ? "" : " · " + nfmt(row.resets) + " board reset" + (row.resets === 1 ? "" : "s"));
}
// The three facts above the errors chart: the worst half hour, resets over the window, the bad share over the last day.
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
function errorFacts(rows, bucketMin, now) {
  const ok = (rows || []).filter(r => r.ok);
  let worst = null;
  ok.forEach(r => { if (r.share != null && (!worst || r.share > worst.share)) worst = r; });
  const resets = ok.reduce((a, r) => a + (r.resets || 0), 0), resetWindows = ok.filter(r => r.resets > 0).length;
  const day = ok.filter(r => now - r.t <= 24 * 3600000 && r.bad != null);
  const good = day.reduce((a, r) => a + r.good, 0), bad = day.reduce((a, r) => a + r.bad, 0);
  return {
    worst: worst ? { t: worst.t, label: DAYS[new Date(worst.t).getDay()] + " " + clockLabel(worst.t - bucketMin * 30000), share: worst.share,
      chip: worst.worst ? worst.worst.chip : null, chipShare: worst.worst ? worst.worst.share : null, resets: worst.resets || 0 } : null,
    resets: resets, resetWindows: resetWindows, share24: good + bad > 0 ? 100 * bad / (good + bad) : null, day: day.length,
  };
}
// A worded marker for an event line, or "" for a line not worth a marker on the three-day chart.
function markerWords(label) {
  let m;
  if (/^power: cycled #\d+/.test(label)) return "plug cycle";
  if (/^power: cycled by/.test(label) || /^dashboard: power: cycled by hand/.test(label)) return "cycle";
  if (/^(power|dashboard: power): switched off/.test(label)) return "off";
  if (/^(power|dashboard: power): switched on/.test(label)) return "on";
  if ((m = /^dashboard: clock set to (\d+) MHz/.exec(label))) return "clock " + m[1];
  if ((m = /^dashboard: fan target set to (\d+)/.exec(label))) return "fan " + m[1];
  if ((m = /^dashboard: switched to preset (\d+)/.exec(label))) return "preset " + m[1];
  if (/^dashboard: soft restart/.test(label)) return "restart";
  // the watchdog's own restarts and service starts are many on a bad day and already read from the reset bars and
  // the interventions table; on the worded chart they would bury the owner's own actions
  if (/^watchdog: /.test(label) || /^service: /.test(label)) return "";
  if (/^hold: started/.test(label)) return "hold";
  if (/^dashboard: /.test(label)) return "note";
  return "";
}
// Tick and label plan for a time axis of `spanMin` minutes ending at `now`: hours-back labels for a few hours, wall-clock
// labels (with the date at midnight) for a day or more.
function axisTicks(spanMin, now, wide) {
  if (spanMin <= 720) {
    const every = wide ? 60 : 120, labels = [];
    for (let m = every; m <= spanMin; m += every) labels.push({ m: m, text: "-" + (m / 60) + " h" });
    return { tickEvery: 5, labels: labels };
  }
  const everyH = spanMin <= 1440 ? (wide ? 6 : 12) : (wide ? 12 : 24), labels = [], d = new Date(now);
  d.setMinutes(0, 0, 0);
  for (let t = d.getTime(); now - t <= spanMin * 60000; t -= 3600000) {
    const dt = new Date(t), h = dt.getHours(), m = Math.round((now - t) / 60000);
    if (m <= 0 || h % everyH !== 0) continue;
    labels.push({ m: m, text: clockLabel(t) + (h === 0 ? " " + MONTHS[dt.getMonth()] + " " + dt.getDate() : "") });
  }
  return { tickEvery: 60, labels: labels };
}
const VERSION = "0.7.4";
// The wall-clock time under a chart's "now" label: 24-hour, minutes only, so the last refresh reads at a glance.
function clockLabel(t) { const d = new Date(t); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }
// The service log as the page shows it: newest line on top, like the interventions table, so a short window shows what matters.
function newestFirst(text) { return (text || "").split(/\r?\n/).filter(l => l.trim()).reverse().join("\n"); }
// What the watchdog will do to a hung miner, from /api/health's `ladder` block, and where those numbers live.
function ladderLine(h) {
  const lad = h && h.ladder, w = (h && h.watchdog) || {};
  if (!lad) return "";
  if (!w.enabled) return "Watchdog off for this run (--no-watchdog, or \"enabled\": false in " + lad.config_path + ").";
  const plug = !!(h.power && h.power.configured);
  let s = "Ladder: soft restart after " + lad.unreachable_minutes + " min unreachable or " + lad.stall_minutes + " min of frozen shares, a second one " + lad.min_gap_minutes + " min later; ";
  const boot = plug && lad.boot_check_minutes ? " (under " + lad.boot_watts + " W " + lad.boot_check_minutes + " min after a cycle means it never booted: cycled again at once, once)" : "";
  s += plug ? "power cycle after two failed restarts and " + lad.after_minutes + " min down, then " + lad.settle_minutes + " min to settle" + boot + "; caps " + lad.max_restarts_per_day + " restarts and " + lad.max_cycles_per_day + " cycles a day. "
    : "no plug, so no power cycle (docs/power-cycle.md); cap " + lad.max_restarts_per_day + " restarts a day. ";
  const sch = lad.schedule ? " Schedule: off " + lad.schedule.off + ", on " + lad.schedule.on + ", " +
    (lad.schedule.days && lad.schedule.days.length ? lad.schedule.days.join(", ") : "every day") + " (power.schedule in the same file)." : "";
  return s + "Set in " + lad.config_path + " (watchdog" + (plug ? " and power blocks" : " block") + "); restart the service after editing." + sch;
}
// The running hold, as /api/health reports it, in one sentence for the Service section. Empty without one.
function holdLine(h) {
  const hold = h && h.hold;
  if (!hold) return "";
  const hhmm = s => (s || "").slice(11, 16), who = hold.source === "schedule" ? "the schedule" : "you";
  const head = (hold.until ? "Held until " + hhmm(hold.until) : "Held with no expiry") + (hold.reason ? " (" + hold.reason + ")" : "") + ", by " + who + " since " + hhmm(hold.since);
  const twice = "the miner hashes twice in a row" + (hold.ok_streak ? " (" + hold.ok_streak + " so far)" : "");
  return head + ": nothing is judged until " + twice + ", or " + (hold.until ? hold.minutes_left + " min pass" : "you press Release") + ".";
}
if (typeof module !== "undefined") module.exports = { VERSION, hottestChip, clockLabel, newestFirst, ladderLine, encryptPassword, login, fetchAll, apiText, apiPut, parseMinerInfo, parseBoards, chipHealth, hashUnit,
  parsePlan, formatPlan, withMhz, clockRange, planRequest, fanRange, fanTargetRequest, presetList, presetRequest, restartRequest, settingDiff, describeRequest, eventMarkers,
  powerActionRequest, holdRequest, holdReleaseRequest, holdLine, seriesRows, errorTip, resetsTip, clockTip, axisTicks, parseStamp, errorFacts, markerWords,
  markerGlyph, markerRow, dropClose, markerKind, markerTitle, chartKey, powerLine, TRIAL_COLUMNS, trialDuration, trialCells, trialStatus, chartData, MODELS, ratedFor, pctOf, alarmBucket, resetsSuffix, profileFor, modelNote,
  powerTile, envRowsFrom, lastHour, recentHashrate, interventions, interventionCounts,
  parseMinerInfoBoards, boardTotals, boardRow, hottestIndex, fmtNum, fansTileText, hotsubText };

// ---- presentation (skipped under Node, where the data layer above is unit-tested) ----
if (typeof document !== "undefined") {
const $ = id => document.getElementById(id);
const fmt = (v, d) => (v === null || v === undefined || isNaN(v)) ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
let baseline = null, lastAccepted = null, lastAcceptedChange = Date.now(), lastHistory = null, lastHistoryAt = 0, fanPct = null, unauthorizedStreak = 0, lastFanRead = 0, lastInfo = null;
let service = null, tokenHandedTo = null;   // service: /api/health payload when this page is served by gbox
let lastModel = null;                       // the miner's model string from /mcb/status, for the "% of rated" axes
const SAMPLE_MIN = 1; // minutes per history sample (verified 2026-09-05 against the miner buffer)
const store = {
  get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
  del: k => { try { localStorage.removeItem(k); } catch (e) {} } };

function host() { return $("host").value.trim(); }
function base() { const h = host(); return /^https?:\/\//.test(h) ? h : "http://" + h; }
function getToken() { return store.get("gbox_token"); }
function showLogin(msg) {
  if (msg) $("loginerr").textContent = msg;
  // The poll timer calls this every cycle. Once the dialog is open, never touch its fields or
  // focus again: someone is typing in it.
  if ($("overlay").classList.contains("show")) return;
  $("loginerr").textContent = msg || "";
  $("host2").value = host(); $("hostrow").hidden = !!host();
  $("overlay").classList.add("show"); (host() ? $("pw") : $("host2")).focus();
}
$("loginform").onsubmit = async e => {
  e.preventDefault();
  try {
    if (!host() && $("host2").value.trim()) { $("host").value = $("host2").value.trim(); store.set("gbox_host", host()); }
    if (!host()) throw new Error("enter the miner's address");
    if (!crypto.subtle) throw new Error("WebCrypto unavailable: open this file over file:// or http://localhost in Chrome/Firefox");
    const t = await login(base(), $("pw").value, crypto.subtle);
    store.set("gbox_token", t); $("pw").value = ""; $("overlay").classList.remove("show");
    tokenHandedTo = null; refresh();
  } catch (err) { $("loginerr").textContent = err.message; }
};
function setBadge(cls, text) { const b = $("badge"); b.className = "badge " + cls; b.textContent = "● " + text; }

// ---- service (only when served by gbox) ----
async function probeService() {
  try {
    const r = await fetch("api/health", { cache: "no-store" });
    service = r.ok ? await r.json() : null;
  } catch (e) { service = null; }
  $("svc").hidden = !service;
  $("svcnote").textContent = service ? " and the local gbox service" : "";
  if (service) {
    if (!host() && service.host) { $("host").value = service.host; }
    const w = service.watchdog || {};
    $("svcsub").textContent = "gbox " + service.version + " · poll every " + service.poll_interval + " s · " + service.samples + " samples, " + service.errors + " errors" +
      (service.latest_time ? " · last " + service.latest_time : "") + " · watchdog " + (w.enabled ? "on, " + w.restarts_today + " restarts today" : "off") +
      (service.has_token || service.can_login ? "" : " · waiting for login") + powerLine(service);
    if (service.last_error) $("svcsub").textContent += " · " + service.last_error;
    $("ladder").textContent = ladderLine(service);
  }
  renderPower(); renderHold(); renderPowerControls();
  return service;
}
// The hold banner in the Service section, and the Power block in Controls (served only: a browser cannot speak the plug's protocol).
function renderHold() { const t = holdLine(service); $("hold").textContent = t; $("hold").hidden = !t; }
function renderPowerControls() {
  $("powerctl").hidden = !service;
  if (!service) return;
  const p = service.power || {}, plug = !!p.configured;
  ["btnoff", "btnon", "btncycle"].forEach(id => { $(id).disabled = !plug || !!p.busy; });
  $("btnrelease").disabled = !service.hold;
  $("powerctlnote").textContent = plug
    ? "Off and cycle ask for the password; on does not. Each starts a hold: the watchdog judges nothing until the miner hashes twice in a row. Hold alone covers an outage you make by hand, such as pulling the cord."
    : "No plug configured (gbox power init), so only Hold and Release here. Press Hold before you pull the cord, so the watchdog does not read the outage as a freeze.";
}
// The Power tile and the watts section's caption; the section itself only shows with a plug configured.
function renderPower() {
  const pt = powerTile(service, lastInfo);
  $("watts").textContent = pt.value; $("plugname").textContent = pt.sub; $("t_power").title = pt.title || "";
  const p = (service && service.power) || {}, on = !!(service && p.configured);
  $("power").hidden = !on;
  if (!on) return;
  const rated = service.rated;
  $("powernote").textContent = "Read every poll from the plug the service watches: " + (p.alias || "(no name set on the plug)") + ", a TP-Link Kasa " + (p.model || "?") +
    (p.meter ? "" : " (no meter: nothing to draw)") + ". Empty where the plug did not answer." +
    (rated && rated.rated_watts ? " Right axis: percent of the " + rated.name + "'s rated " + Math.round(rated.rated_watts) + " W (Goldshell's figure, ±5%)." : "");
}
async function handOffToken() {
  const t = getToken();
  if (!service || !t || (service.has_token && tokenHandedTo === t)) return;
  try {
    const r = await fetch("api/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: t }) });
    if (r.ok) { tokenHandedTo = t; service.has_token = true; }
  } catch (e) { /* not served: nothing to hand off to */ }
}
let eventMarks = [], eventsText = "";   // {t, label} for the fan chart, and the raw log for the interventions table
async function refreshEvents() {
  if (!service) return;
  try {
    const text = await (await fetch("api/events", { cache: "no-store" })).text();
    $("events").textContent = newestFirst(text) || "(no events yet)";
    eventMarks = eventMarkers(text);
    eventsText = text;
    renderInterventions();
  } catch (e) {}
}
// The interventions table: the event log filtered to what changed the miner, joined to the samples for the outcome.
function renderInterventions() {
  const sec = $("interventions");
  if (!service) { sec.hidden = true; return; }
  sec.hidden = false;
  const iv = interventions(eventsText, envRows || []), c = interventionCounts(iv);
  $("ivsub").textContent = c.restarts + " soft restart" + (c.restarts === 1 ? "" : "s") + ", " + c.cycles + " power cycle" + (c.cycles === 1 ? "" : "s") +
    ", " + c.actions + " of yours, in the log the service keeps";
  const body = $("ivtab").tBodies[0]; body.innerHTML = "";
  if (!iv.length) {
    const tr = document.createElement("tr"), td = document.createElement("td");
    td.colSpan = 4; td.className = "empty"; td.textContent = "Nothing yet: no restart, cycle or dashboard action in the log."; tr.appendChild(td); body.appendChild(tr);
    return;
  }
  iv.forEach(i => {
    const tr = document.createElement("tr"); tr.className = i.kind;
    [[new Date(i.t).toLocaleString(), "when"], [i.who, "who"], [i.what, ""], [i.result, ""]].forEach(([text, cls]) => {
      const td = document.createElement("td"); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}
async function reportEvent(message, restart) {
  // Served: one line in the service event log (and a marker on the fan chart). Standalone: nothing to report to.
  if (!service) return false;
  try {
    const r = await fetch("api/event", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, restart: !!restart }) });
    return r.ok;
  } catch (e) { return false; }
}

async function refresh() {
  const token = getToken();
  if (!host()) { setBadge("idle", "no miner address"); showLogin(); return; }
  if (!token) { setBadge("idle", "not logged in"); showLogin(); return; }
  try {
    const data = await fetchAll(base(), token, undefined, !!service);
    if (Date.now() - lastFanRead > 60000) { await refreshFan(); lastFanRead = Date.now(); }
    unauthorizedStreak = 0; render(data); $("err").textContent = "";
    handOffToken();
  } catch (e) {
    if (e.message === "401") {
      unauthorizedStreak++; console.warn("miner answered 401 (" + unauthorizedStreak + " in a row) at " + new Date().toLocaleTimeString());
      if (unauthorizedStreak < 3) { $("err").textContent = "Miner rejected the session token (" + unauthorizedStreak + "/3), retrying"; return; }
      store.del("gbox_token"); unauthorizedStreak = 0; setBadge("idle", "session rejected"); showLogin("The miner rejected the session token three times in a row. Log in again."); return;
    }
    $("err").textContent = "Last error " + new Date().toLocaleTimeString() + ": " + e.message;
    setBadge("bad", /fetch|network/i.test(e.message) ? "UNREACHABLE" : "error");
  }
}

// The Hottest chip tile: the sustained level as the number and the flag, the peak and the chip average beside it, the
// since-boot highs beneath. Without a log read yet (or as a file, with no service), the firmware's own chip field
// stands in on the second line, named for what it is: the average, not the hottest.
function renderHottest(info, hot, booted, boards) {
  const b = v => "<b class=\"v2\">" + v + "</b>", board = b(fmt(info.boardTemp, 1) + " °C") + " board sensor";
  if (!hot) {
    $("hottest").textContent = "—";
    $("hotsub").innerHTML = hotsubText(info, boards);
    $("hotboot").textContent = service ? "hottest chip: waiting for the first cgminer-log read" : "hottest chip needs the gbox service";
    $("t_temp").className = "tile"; return;
  }
  $("hottest").textContent = hot.stale ? "—" : fmt(hot.level) + " °C";
  $("hotsub").innerHTML = (hot.stale ? "log read overdue since " + clockLabel(hot.t) + " · "
    : "peak " + b(fmt(hot.peak)) + " · chips avg " + b(fmt(hot.chipAvg)) + " · ") + board;
  $("hotboot").textContent = (booted ? "since boot" : "in the served log") + ": level high " + fmt(hot.levelHigh.v) + " °C at " + clockLabel(hot.levelHigh.t) +
    (hot.peakHigh ? ", peak " + fmt(hot.peakHigh.v) + " at " + clockLabel(hot.peakHigh.t) : "");
  $("t_temp").className = "tile" + (hot.cls ? " " + hot.cls : "");
}
function render(d) {
  const info = d.info, chips = d.boards.flat(), setting = d.setting, status = d.status, now = Date.now();
  const miBoards = d.miBoards || [], boardsList = (d.boards4028 && d.boards4028.length ? d.boards4028 : miBoards).map(boardRow);
  lastInfo = info;
  if (!baseline) baseline = { t: now, rebootcnt: info.rebootcnt, hwErrors: info.hwErrors, accepted: info.accepted, chips: Object.fromEntries(chips.map(c => [c.chip, c])) };
  if (info.accepted !== lastAccepted) { lastAccepted = info.accepted; lastAcceptedChange = now; }
  const stalledMin = (now - lastAcceptedChange) / 60000, minutes = Math.max((now - baseline.t) / 60000, 0.01);
  const rbd = info.rebootcnt - baseline.rebootcnt;
  const hot = envRows ? hottestChip(envRows, now, service && service.temps, info.elapsed ? now - info.elapsed * 1000 : null) : null;

  // status badge: state always carries a word, never color alone
  if (stalledMin >= 5) setBadge("bad", "STALLED · no new shares for " + Math.floor(stalledMin) + " min");
  else if (rbd > 0 && rbd / minutes > 0.5) setBadge("warn", "RESET LOOP · board resetting repeatedly");
  else if (hot && hot.cls === "critical") setBadge("bad", "HOT · hottest chip " + fmt(hot.level) + " °C sustained");
  else if (hot && hot.cls === "serious") setBadge("warn", "HOT · hottest chip " + fmt(hot.level) + " °C sustained");
  else setBadge("ok", "hashing");

  // Every window is named with a clock time the viewer can see: the boot (from uptime), the last hour (from the
  // service log when served, else the miner's own buffer), or, as a file with no log, the moment this page opened.
  const booted = info.elapsed ? clockLabel(now - info.elapsed * 1000) : null, sinceBoot = "since boot" + (booted ? " " + booted : "");
  const opened = clockLabel(baseline.t), hour = envRows ? lastHour(envRows, now) : null;
  const [u20, d20] = hashUnit(info.mhs20), [uav, dav] = hashUnit(info.mhsAv), rh = recentHashrate(d.history, 60);
  $("mhs20").textContent = fmt(info.mhs20 / d20, d20 === 1 ? 0 : 1) + " " + u20; $("unit20").textContent = "20 s reading";
  $("k_av").textContent = "Hashrate " + sinceBoot;
  $("mhsav").textContent = fmt(info.mhsAv / dav, dav === 1 ? 0 : 1) + " " + uav;
  $("unitav").textContent = rh !== null ? "last hour " + fmt(rh / dav, dav === 1 ? 0 : 1) + " " + uav : "";
  $("hwpct").textContent = fmt(info.hwPct, 1) + " %";
  const recentBad = info.hwErrors - baseline.hwErrors, recentAcc = info.accepted - baseline.accepted, pct = (b, a) => fmt(100 * b / (b + a), 1) + " %";
  $("hwrecent").textContent = sinceBoot + " · " + (hour && hour.bad + hour.accepted > 0 ? "last hour " + pct(hour.bad, hour.accepted)
    : recentAcc > 0 ? pct(recentBad, recentAcc) + " since " + opened + " (page opened)" : "since " + opened + " (page opened): no samples yet");
  $("t_hw").className = "tile" + (info.hwPct >= 15 ? " critical" : info.hwPct >= 8 ? " serious" : "");
  $("rebootcnt").textContent = fmt(info.rebootcnt);
  $("rbdelta").textContent = sinceBoot + " · " + (hour ? fmt(hour.resets) + " in the last hour" : "+" + fmt(rbd) + " since " + opened + " (page opened)");
  $("t_rb").className = "tile" + ((hour ? hour.resets > 0 : rbd > 0) ? " critical" : "");
  $("chipsub").textContent = chips.length === 0 && miBoards.length
    ? "per-chip counts need /dbg/icinfo, which did not answer on this unit"
    : "good and bad nonces " + sinceBoot + "; bad/min since " + opened + " (page opened)";
  renderHottest(info, hot, !!info.elapsed, miBoards);
  const rated = ratedFor(lastModel), fanMaxRpm = rated && rated.fan_max_rpm;
  const ft = fansTileText(info.fans && info.fans.length ? info.fans : [info.fan0, info.fan1], fanPct, fanMaxRpm);
  $("fans").textContent = ft.value;
  $("fansub").innerHTML = ft.sub + " · target <b class=\"v2\">" + Number(setting.temp_target) + " °C</b>";
  $("accepted").textContent = fmt(info.accepted); $("rejected").textContent = "rejected " + fmt(info.rejected);
  const planText = setting.manual ? setting.manualPowerplan : "preset " + setting.select;
  $("clock").textContent = fmt(info.clock) + " MHz"; $("plan").textContent = "plan " + planText;
  const up = info.elapsed || 0, upText = Math.floor(up / 3600) + " h " + Math.floor(up % 3600 / 60) + " min";
  lastModel = status.model || null;
  $("title").textContent = status.model || "Goldshell Box"; document.title = (status.model || "Goldshell Box") + " status";
  const note = modelNote(status.model); $("modelnote").textContent = note; $("modelnote").hidden = !note;
  $("meta").textContent = "fw " + status.firmware + " · up " + upText + (booted ? " since " + booted : "") + " · gbox " + VERSION;
  $("updated").textContent = "updated " + new Date().toLocaleTimeString();

  const dl = $("info"); dl.innerHTML = "";
  [["model", status.model], ["firmware", status.firmware], ["hardware", status.hardware], ["controller", status.mcbversion],
   ["power plan", planText + (setting.manual ? " (manual)" : "")],
   ["fan target temp", setting.temp_target + " °C (steers on the board sensor, not the chips)"],
   ["boards / chips", d.boards.length + " / " + chips.length],
   ["overheat shutdown", setting.tempcontrol === undefined ? "—" : (setting.tempcontrol ? "on" : "OFF (the firmware will not stop the miner when it overheats)")],
   ["uptime", upText], ["overheat flag", info.overheat]]
   .forEach(kvp => { const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = kvp[0]; dd.textContent = kvp[1]; dl.append(dt, dd); });

  renderChips(d.boards, minutes); renderBoards(boardsList); renderControls(setting); renderPower(); lastHistory = d.history; lastHistoryAt = now; renderChart(d.history);
}
// The boards table: hidden on a one-board unit (today's SC-BOX/SC Lite), rows above Chips otherwise, the
// hottest row marked. Reads /api/boards (the Python service's per-board list) when a served page has one,
// else the boards this page parsed itself from /dbg/minerinfo.
function renderBoards(boards) {
  const sec = $("boardssec"); if (!sec) return;
  sec.hidden = boards.length <= 1;
  if (boards.length <= 1) return;
  const hotI = boards.reduce((best, b, i) => (b.chipTemp !== null && (best === -1 || b.chipTemp > boards[best].chipTemp)) ? i : best, -1);
  const tb = $("boardstab").querySelector("tbody"); tb.innerHTML = "";
  boards.forEach((b, i) => {
    const tr = document.createElement("tr"); if (i === hotI) tr.className = "hot";
    tr.innerHTML = "<td>" + b.board + (i === hotI ? " (hottest)" : "") + "</td><td>" + fmt(b.mhs20 / 1e6, 2) + " TH/s</td><td>" + fmt(b.mhsAv / 1e6, 2) +
      " TH/s</td><td>" + fmt(b.accepted) + "</td><td>" + fmt(b.rejected) + "</td><td>" + fmt(b.hwPct, 1) + " %</td><td>" + fmt(b.chipTemp) +
      " °C</td><td>" + fmt(b.boardTemp, 1) + " °C</td><td>" + fmt(b.rebootcnt) + "</td>";
    tb.append(tr);
  });
}

function renderChips(boards, minutes) {
  const tb = $("chips").querySelector("tbody"); tb.innerHTML = "";
  boards.forEach((chips, b) => {
    const maxGood = Math.max.apply(null, chips.map(c => c.good)) || 1;
    if (boards.length > 1) { const tr = document.createElement("tr"); tr.className = "board"; tr.innerHTML = "<td colspan=\"6\">board " + b + "</td>"; tb.append(tr); }
    chips.forEach(c => {
      const b0 = baseline.chips[c.chip] || c, badRate = (c.bad - b0.bad) / minutes, badPct = 100 * c.bad / Math.max(c.bad + c.good, 1);
      const tr = document.createElement("tr"); tr.className = chipHealth(c, maxGood);
      tr.innerHTML = "<td>" + c.chip + "</td><td>" + fmt(c.good) + "</td><td>" + fmt(c.bad) + "</td><td>" + fmt(badPct, 1) +
        "</td><td>" + fmt(badRate, 2) + "</td><td class=\"bar\"><div style=\"width:" + (100 * c.good / maxGood) + "%\"></div></td>";
      tb.append(tr);
    });
  });
}

function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1)))); return Math.ceil(v / p * 2) / 2 * p; }
// Right-hand axis in percent of a rated figure: ticks at 0/25/50/75/100 that fall inside the panel, and a title.
function rightAxis(xR, y, titleY, max, rated, title) {
  let s = "";
  [0, 25, 50, 75, 100].forEach(p => {
    const v = p / 100 * rated;
    if (v > max + 1e-9) return;
    s += "<line class=\"raxis\" x1=\"" + xR + "\" x2=\"" + (xR + 4) + "\" y1=\"" + y(v).toFixed(1) + "\" y2=\"" + y(v).toFixed(1) + "\"/>" +
      "<text class=\"rlbl\" x=\"" + (xR + 7) + "\" y=\"" + (y(v) + 4).toFixed(1) + "\">" + p + "%</text>";
  });
  return s + "<text class=\"rlbl axis\" x=\"" + (xR + 46) + "\" y=\"" + titleY + "\" text-anchor=\"end\">" + title + "</text>";
}
// Tooltip placement: to the right of the cursor, or to its left when the text would run off the chart's right edge.
function placeTip(tip, xPx, yPx, boxWidth) {
  const tw = tip.offsetWidth || 200;
  tip.style.left = (xPx + 12 + tw > boxWidth ? Math.max(0, xPx - 12 - tw) : xPx + 12) + "px";
  tip.style.top = yPx + "px";
}
function renderChart(hist) {
  if (service && series24) return renderHashrateSeries();
  const box = $("chart"), { unit, div, data } = chartData(hist);
  if (data.length === 0) { box.innerHTML = "<p class=\"note\">no history yet</p>"; return; }
  if (data.length === 1) {
    box.innerHTML = "<p class=\"note\">one sample so far, " + Math.round(data[0]) + " " + unit + ": a boot wipes the miner's own buffer, and the graph starts at the second sample, a minute from now.</p>";
    return;
  }
  const rated = ratedFor(lastModel), ratedV = rated ? rated.rated_mhs / div : null;
  const W = Math.max(box.clientWidth, 320), H = 246, L = 44, R = 12, T = 26, B = 48, RW = ratedV ? 44 : 0, xR = W - R - RW;
  const yMax = niceMax(Math.max(Math.max.apply(null, data) * 1.05, ratedV ? ratedV * 1.1 : 0)), step = yMax / 4;
  const x = i => L + (xR - L) * i / Math.max(data.length - 1, 1), y = v => T + (H - T - B) * (1 - v / yMax);
  const path = data.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  let s = "<svg width=\"" + W + "\" height=\"" + H + "\" viewBox=\"0 0 " + W + " " + H + "\" role=\"img\" aria-label=\"hashrate history\">";
  for (let g = 0; g <= yMax + 1e-9; g += step) s += "<line class=\"grid\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + y(g) + "\" y2=\"" + y(g) + "\"/><text x=\"" + (L - 6) + "\" y=\"" + (y(g) + 4) + "\" text-anchor=\"end\">" + fmt(g, step < 10 ? 1 : 0) + "</text>";
  s += "<path class=\"area\" d=\"" + path + " L" + x(data.length - 1).toFixed(1) + " " + y(0) + " L" + x(0) + " " + y(0) + " Z\"/><path class=\"line\" d=\"" + path + "\"/>";
  const y0 = H - B, minutesBack = i => (data.length - 1 - i) * SAMPLE_MIN, xAtMin = m => x(data.length - 1 - m / SAMPLE_MIN);
  s += "<line class=\"tick\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + y0 + "\" y2=\"" + y0 + "\"/>";
  const span = minutesBack(0), labelEvery = W < 700 ? 120 : 60;
  for (let m = 0; m <= span; m += 5) {
    const major = m % 60 === 0, mid = m % 15 === 0, len = major ? 9 : mid ? 6 : 3, xx = xAtMin(m);
    s += "<line class=\"tick" + (major ? " major" : "") + "\" x1=\"" + xx + "\" x2=\"" + xx + "\" y1=\"" + y0 + "\" y2=\"" + (y0 + len) + "\"/>";
    if (m === 0) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"end\">now</text><text x=\"" + xx + "\" y=\"" + (y0 + 34) + "\" text-anchor=\"end\">" + clockLabel(lastHistoryAt || Date.now()) + "</text>";
    else if (m % labelEvery === 0 && xx > L + 24) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"middle\">-" + (m / 60) + " h</text>";
  }
  s += "<text class=\"axis\" x=\"" + (L - 6) + "\" y=\"" + (T - 12) + "\" text-anchor=\"end\">" + unit + "</text>";
  if (ratedV) s += rightAxis(xR, y, T, yMax, ratedV, "% of rated");
  s += "<line class=\"cross\" id=\"cx\" y1=\"" + T + "\" y2=\"" + (H - B) + "\" style=\"display:none\"/><circle class=\"dot\" id=\"cdot\" r=\"4\" style=\"display:none\"/></svg><div class=\"tip\" id=\"tip\"></div>";
  box.innerHTML = s;
  const svg = box.querySelector("svg"), tip = $("tip"), cx = $("cx"), dot = $("cdot");
  svg.onmousemove = e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width, i = Math.round((px - L) / (xR - L) * (data.length - 1));
    if (i < 0 || i >= data.length) return;
    cx.setAttribute("x1", x(i)); cx.setAttribute("x2", x(i)); cx.style.display = "";
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(data[i])); dot.style.display = "";
    const pct = ratedV ? " · " + fmt(100 * data[i] / ratedV) + "% of rated" : "";
    tip.style.display = "block"; tip.textContent = fmt(data[i], div === 1 ? 0 : 1) + " " + unit + pct + " · " + minutesBack(i) + " min ago";
    placeTip(tip, x(i) * r.width / W, y(data[i]) * r.height / H - 30, r.width);
  };
  svg.onmouseleave = () => { tip.style.display = "none"; cx.style.display = "none"; dot.style.display = "none"; };
}

$("host").value = store.get("gbox_host") || "";
$("host").onchange = () => { store.set("gbox_host", host()); baseline = null; fetchAll.cache = null; poll(); };
$("relogin").onclick = () => { store.del("gbox_token"); tokenHandedTo = null; refresh(); };
// fan duty cycle lives only in the fan controller's log (a few hundred KB), so it is read once a minute, not every refresh
async function refreshFan() {
  const token = getToken(); if (!token) return;
  try {
    const tail = (await apiText(base(), token, "dbg/fanctrllog")).slice(-4000);
    const all = [...tail.matchAll(/fan0: (\d+)(?: ==> (\d+))?\)/g)];
    if (all.length) fanPct = parseInt(all[all.length - 1][2] || all[all.length - 1][1]);
  } catch (e) { /* the log is appended while it is served, so a length-mismatch read now and then is normal; keep the last value */ }
}
// ---- fans + temperature from the service's log.csv ----
let envRows = null;
let series24 = null, series72 = null;   // /api/series for the 24-hour charts and the three-day errors chart (served only)
const SERVED_SPAN = 1440, ERR_SPAN = 4320;
function servedOpts(box, spanMin, bucketMin) { return { gapMs: bucketMin * 60000 * 1.5, bucketMin: bucketMin, ticks: axisTicks(spanMin, Date.now(), box.clientWidth >= 700), band: alarmBucket }; }
// The key under a chart: the marker glyphs, the resets bar and the alarm band, from chartKey.
function renderKey(id, opts) {
  const el = $(id); if (!el) return;
  const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  el.innerHTML = chartKey(opts).map(i => "<span>" + (i.glyph ? "<b>" + esc(i.glyph) + "</b>" : i.swatch ? "<i class=\"sw" + i.swatch + "\"></i>" : "") + esc(i.text) + "</span>").join("");
  el.hidden = false;
}
// The board-resets panel under a served chart: one bar per 5-minute bucket, the same bars the three-day chart draws per half hour.
function resetsPanel(rows) {
  const most = Math.max.apply(null, rows.map(r => r.resets).filter(v => v !== null && v !== undefined).concat([0]));
  const max = niceMax(Math.max(most, 4) * 1.1);
  return { label: "resets", min: 0, max: max, step: max / 4, series: [], bars: "resets", rated: null };
}
async function refreshEnv() {
  if (!service) { $("envchart").innerHTML = "<p class=\"note\">Fan and temperature history needs the gbox service: run <code>gbox serve</code> and open the page from the address it prints.</p>"; return; }
  try {
    const r = await fetch("api/log.csv?tail=3000", { cache: "no-store" });        // the tiles' last hour and the interventions join: a day of rows is plenty
    if (!r.ok) throw new Error("no samples yet");
    envRows = envRowsFrom(await r.text());
    try {
      const [a, b] = await Promise.all([fetch("api/series?hours=24&bucket=5", { cache: "no-store" }), fetch("api/series?hours=72&bucket=30", { cache: "no-store" })]);
      series24 = a.ok ? await a.json() : null; series72 = b.ok ? await b.json() : null;
    } catch (e) { series24 = series72 = null; }
    renderEnv(); renderWatts(); renderErrors(); renderInterventions(); if (lastHistory) renderChart(lastHistory);
  } catch (e) { $("envchart").innerHTML = "<p class=\"note\">No logger samples yet (" + e.message + ").</p>"; }
}
// The errors chart: bad share of all chips and of the worst chip, the clock as a step, board resets as bars, over three days.
function renderErrors() {
  const sec = $("errors"), box = $("errchart");
  if (!service || !series72) { sec.hidden = true; return; }
  sec.hidden = false;
  const rows = seriesRows(series72), now = Date.now(), spanMin = ERR_SPAN, bm = series72.bucket_minutes;
  if (rows.filter(r => r.ok).length < 2) { box.innerHTML = "<p class=\"note\">no logger samples in this window yet</p>"; return; }
  const mx = k => Math.max.apply(null, rows.map(r => r[k]).filter(v => v !== null && v !== undefined).concat([0]));
  const shareMax = niceMax(Math.max(mx("share"), mx("worst_share"), 0.5) * 1.05), clockMax = niceMax(mx("clock") * 1.1) || 800, resetMax = niceMax(Math.max(mx("resets"), 4) * 1.1);
  const clocks = rows.map(r => r.clock).filter(v => v !== null && v !== undefined), clockMin = clocks.length && Math.min.apply(null, clocks) >= 400 ? 300 : 0;
  const panels = [
    { label: "% bad", min: 0, max: shareMax, step: shareMax / 4, series: [["share", "", "all chips"], ["worst_share", "s2", "worst chip"]], rated: null, tip: best => errorTip(best, bm) },
    { label: "MHz", min: clockMin, max: clockMax, step: (clockMax - clockMin) / 4, series: [["clock", "s3", "clock", { step: true, fill: true }]], rated: null, tip: best => clockTip(best, bm) },
    { label: "resets", min: 0, max: resetMax, step: resetMax / 4, series: [], bars: "resets", rated: null, tip: best => resetsTip(best, bm) } ];
  const opts = Object.assign(servedOpts(box, spanMin, bm), { words: true, band: alarmBucket });
  drawPanels(box, panels, rows, spanMin, now, best => errorTip(best, bm), "errors over three days", opts);
  renderKey("errkey", { words: true, bars: true, band: true });
  renderErrorFacts(errorFacts(rows, bm, now));
}
// The three facts above the errors chart. The worst half hour is red when its share is over 1% or it had resets.
function renderErrorFacts(f) {
  const box = $("errfacts"); box.innerHTML = "";
  const fact = (k, v, sub, cls) => { const d = document.createElement("div"); d.className = "fact" + (cls ? " " + cls : "");
    d.innerHTML = "<div class=\"k\"></div><div class=\"v\"></div><div class=\"s\"></div>";
    d.children[0].textContent = k; d.children[1].textContent = v; d.children[2].textContent = sub; box.appendChild(d); };
  if (f.worst) {
    const w = f.worst, bad = w.share > 1 || w.resets > 0;
    fact("Worst half hour", w.label + " · " + fmt(w.share, 2) + "% bad", (w.chip ? "chip " + w.chip + " at " + fmt(w.chipShare, 1) + "%, " : "") + fmt(w.resets) + " reset" + (w.resets === 1 ? "" : "s"), bad ? "critical" : "");
  } else fact("Worst half hour", "—", "no counted samples yet", "");
  fact("Board resets, 3 days", fmt(f.resets), f.resetWindows ? "in " + f.resetWindows + " half hour" + (f.resetWindows === 1 ? "" : "s") : "none", f.resets > 0 ? "serious" : "");
  fact("Bad share, last 24 h", f.share24 == null ? "—" : fmt(f.share24, 2) + "%", f.share24 == null ? "no counted samples" : "all chips, " + f.day + " half hours", "");
}
// The hashrate chart when served: 24 hours of 5-minute means from the log, with the rated axis.
function renderHashrateSeries() {
  const box = $("chart"), rows = seriesRows(series24), now = Date.now(), spanMin = SERVED_SPAN;
  const vals = rows.map(r => r.hashrate).filter(v => v !== null && v !== undefined);
  if (vals.length < 2) { box.innerHTML = "<p class=\"note\">no logger samples in this window yet</p>"; return; }
  const [unit, div] = hashUnit(Math.max.apply(null, vals)), rated = ratedFor(lastModel), ratedV = rated ? rated.rated_mhs / div : null;
  const scaled = rows.map(r => Object.assign({}, r, { h: r.hashrate === null || r.hashrate === undefined ? null : r.hashrate / div }));
  const yMax = niceMax(Math.max(Math.max.apply(null, vals) / div * 1.05, ratedV ? ratedV * 1.1 : 0));
  drawPanels(box, [{ label: unit, min: 0, max: yMax, step: yMax / 4, series: [["h", "", unit]], rated: ratedV ? { value: ratedV, title: "% of rated" } : null }], scaled, spanMin, now,
    best => clockLabel(best.t) + " · " + (best.h === null ? "no samples" : fmt(best.h, div === 1 ? 0 : 1) + " " + unit + (ratedV ? " (" + fmt(100 * best.h / ratedV) + "% of rated)" : "")) + resetsSuffix(best),
    "hashrate over 24 hours", servedOpts(box, spanMin, series24.bucket_minutes));
  $("hashsub").textContent = "last 24 hours from the gbox service log, 5-minute means of the miner's 20 s reading; the tiles above are live";
  renderKey("hashkey", { band: true });
}
// The window both log charts share: as far back as the miner's own hashrate buffer reaches, at least an hour.
function logSpanMin() { return Math.max(lastHistory.filter(v => v > 0).length * SAMPLE_MIN, 60); }
// Shared scaffold for the log charts: stacked panels on one time axis, series that break at a gap or a missing value,
// event markers, an optional right-hand "% of rated" axis per panel, and one hover tooltip. Each panel:
// { label, min, max, step, series: [[rowKey, cssClass, name]...], rated: { value, title } | null }.
function drawPanels(box, panels, rows, spanMin, now, tipText, aria, opts) {
  const o = opts || {}, gapMs = o.gapMs || 180000, tk = o.ticks || null;
  const W = Math.max(box.clientWidth, 320), L = 44, R = 12, PH = 130, GAP = 34, T = o.words ? 92 : 40, B = 48, n = panels.length;   // T 40: room for the P/H row above the plot (24 until 0.7.1)
  const H = T + n * PH + (n - 1) * GAP + B, RW = panels.some(p => p.rated) ? 44 : 0, xR = W - R - RW, y0 = T + n * PH + (n - 1) * GAP;
  const xOf = t => L + (xR - L) * (1 - (now - t) / (spanMin * 60000));
  const has = v => v !== null && v !== undefined && !isNaN(v);
  let s = "<svg width=\"" + W + "\" height=\"" + H + "\" viewBox=\"0 0 " + W + " " + H + "\" role=\"img\" aria-label=\"" + aria + "\">";
  if (o.band) {
    // alarm bands: one faint band down every panel for each bucket the caller flags, so an incident is found by scanning one strip
    const bw = Math.max(3, (xR - L) * (o.bucketMin || 5) / spanMin);
    rows.forEach(r => { if (o.band(r)) s += "<rect class=\"band\" x=\"" + (xOf(r.t) - bw / 2).toFixed(1) + "\" y=\"" + (T - 14) + "\" width=\"" + bw.toFixed(1) + "\" height=\"" + (y0 - T + 14) + "\"/>"; });
  }
  panels.forEach((p, i) => {
    const top = T + i * (PH + GAP), y = v => top + PH * (1 - (Math.min(Math.max(v, p.min), p.max) - p.min) / (p.max - p.min));
    for (let g = p.min; g <= p.max + 1e-9; g += p.step) s += "<line class=\"grid\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + y(g) + "\" y2=\"" + y(g) + "\"/><text x=\"" + (L - 6) + "\" y=\"" + (y(g) + 4) + "\" text-anchor=\"end\">" + fmt(g, p.step < 1 ? 1 : 0) + "</text>";
    s += "<rect class=\"panelhl\" id=\"" + box.id + "-hl" + i + "\" x=\"" + L + "\" y=\"" + (top - 14) + "\" width=\"" + (xR - L) + "\" height=\"" + (PH + 14) + "\" style=\"display:none\"/>";
    if (i > 0) s += "<line class=\"sep\" x1=\"" + (L - 40) + "\" x2=\"" + (W - R) + "\" y1=\"" + (top - GAP / 2) + "\" y2=\"" + (top - GAP / 2) + "\"/>";
    const ly = i ? top - 10 : top - 26;         // the first panel's labels sit above the P/H row; later panels' in the gap between panels
    s += "<text class=\"axis\" x=\"" + (L - 6) + "\" y=\"" + ly + "\" text-anchor=\"end\">" + p.label + "</text>";
    if (p.range) {
      // a band between two series (the hottest chip's sustained level and its peak): one filled path per contiguous run
      const [lo, hi, rcls] = p.range; let up = "", down = [], prevT = null, d = "";
      const flush = () => { if (up && down.length) d += up + down.reverse().map(pt => "L" + pt).join("") + "Z"; up = ""; down = []; };
      rows.forEach(r => {
        if (!has(r[lo]) || !has(r[hi])) { flush(); prevT = null; return; }
        const X = xOf(r.t).toFixed(1), Yhi = y(r[hi]).toFixed(1);
        if (prevT === null || r.t - prevT >= gapMs) { flush(); up = "M" + X + " " + Yhi; } else up += "L" + X + " " + Yhi;
        down.push(X + " " + y(r[lo]).toFixed(1)); prevT = r.t;
      });
      flush();
      if (d) s += "<path class=\"" + rcls + "\" d=\"" + d + "\"/>";
    }
    (p.guides || []).forEach(g => {
      const gy = y(g.value).toFixed(1);
      s += "<line class=\"" + g.cls + "\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + gy + "\" y2=\"" + gy + "\"/>";
      if (g.text) s += "<text class=\"guidelbl\" x=\"" + (L + 4) + "\" y=\"" + (y(g.value) - 4).toFixed(1) + "\">" + g.text + "</text>";
    });
    const labels = [];
    (p.series || []).forEach(([key, cls, name, style], idx) => {
      let d = "", area = "", prev = null, last = null, yPrev = null, runStart = null, base = y(p.min).toFixed(1);
      const closeRun = () => { if (runStart !== null && last) area += "L" + xOf(last.t).toFixed(1) + " " + base + "L" + runStart + " " + base + "Z"; runStart = null; };
      rows.forEach(r => {
        if (!has(r[key])) { if (prev !== null) closeRun(); prev = null; return; }
        const X = xOf(r.t).toFixed(1), Y = y(r[key]).toFixed(1);
        if (prev !== null && r.t - prev < gapMs) { const seg = (style && style.step ? "L" + X + " " + yPrev : "") + "L" + X + " " + Y; d += seg; area += seg; }
        else { closeRun(); d += "M" + X + " " + Y; area += "M" + X + " " + Y; runStart = X; }
        prev = r.t; yPrev = Y; last = r;
      });
      closeRun();
      if (!last) return;
      if (style && style.fill) s += "<path class=\"area\" d=\"" + area + "\"/>";   // the clock as a block: each contiguous run filled to the baseline
      s += "<path class=\"line " + cls + "\" d=\"" + d + "\"/>";
      labels.push({ x: xOf(last.t) - 4, y: y(last[key]) + (idx ? 15 : -6), text: name + " " + fmt(last[key], p.step < 1 ? 2 : 0) });
    });
    // every line is named at its right end; with three lines close together the names are pushed apart, not stacked
    labels.sort((a, b) => a.y - b.y).forEach((l, i) => { if (i && l.y - labels[i - 1].y < 13) l.y = labels[i - 1].y + 13; });
    labels.forEach(l => { s += "<text class=\"lbl\" x=\"" + l.x + "\" y=\"" + l.y.toFixed(1) + "\" text-anchor=\"end\">" + l.text + "</text>"; });
    if (p.bars) {
      // one bar per bucket from the baseline, the tallest labeled with its count
      const bw = Math.max(2, (xR - L) * (o.bucketMin || 5) / spanMin * 0.7);
      let tallest = null;
      rows.forEach(r => { const v = r[p.bars]; if (!has(v) || v <= 0) return; if (!tallest || v > tallest[p.bars]) tallest = r;
        s += "<rect class=\"bar\" x=\"" + (xOf(r.t) - bw / 2).toFixed(1) + "\" y=\"" + y(v).toFixed(1) + "\" width=\"" + bw.toFixed(1) + "\" height=\"" + (y(0) - y(v)).toFixed(1) + "\"/>"; });
      if (tallest) s += "<text class=\"barlbl\" x=\"" + xOf(tallest.t).toFixed(1) + "\" y=\"" + (y(tallest[p.bars]) - 4).toFixed(1) + "\" text-anchor=\"middle\">" + fmt(tallest[p.bars]) + "</text>";
    }
    if (p.rated) s += rightAxis(xR, y, ly, p.max, p.rated.value, p.rated.title);
  });
  const xAtMin = m => L + (xR - L) * (1 - m / spanMin), labelEvery = W < 700 ? 120 : 60;
  const tickEvery = tk ? tk.tickEvery : 5, majorEvery = tickEvery >= 60 ? 360 : 60, midEvery = tickEvery >= 60 ? 180 : 15;
  s += "<line class=\"tick\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + y0 + "\" y2=\"" + y0 + "\"/>";
  for (let m = 0; m <= spanMin; m += tickEvery) {
    const major = m % majorEvery === 0, mid = m % midEvery === 0, len = major ? 9 : mid ? 6 : 3, xx = xAtMin(m);
    s += "<line class=\"tick" + (major ? " major" : "") + "\" x1=\"" + xx + "\" x2=\"" + xx + "\" y1=\"" + y0 + "\" y2=\"" + (y0 + len) + "\"/>";
    if (m === 0) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"end\">now</text><text x=\"" + xx + "\" y=\"" + (y0 + 34) + "\" text-anchor=\"end\">" + clockLabel(now) + "</text>";
    else if (!tk && m % labelEvery === 0 && xx > L + 24) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"middle\">-" + (m / 60) + " h</text>";
  }
  if (tk) tk.labels.forEach(l => { const xx = xAtMin(l.m); if (xx > L + 30 && xx < xR - 40) s += "<text x=\"" + xx.toFixed(1) + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"middle\">" + l.text + "</text>"; });
  // markers: what the buttons, the watchdog and the plug did, and each service start (S); hover for the event text
  const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  let lastMarkX = -999;
  const marks = eventMarks.filter(m => now - m.t <= spanMin * 60000 && m.t <= now).sort((a, b) => a.t - b.t);
  // two glyph rows on the 24-hour charts (0.7.1): P and H above the plot, ▼ W S inside its top edge, each row
  // keeping one letter per run of close marks (dropClose); the worded chart keeps its rotated words
  const rowOf = marks.map(m => o.words ? "words" : markerRow(m.label));
  const keep = {};
  ["top", "in"].forEach(row => { const idx = marks.map((m, i) => i).filter(i => rowOf[i] === row); dropClose(idx.map(i => xOf(marks[i].t)), 6).forEach((k, j) => { keep[idx[j]] = k; }); });
  marks.forEach((m, i) => {
    const xx = xOf(m.t), row = rowOf[i], words = o.words ? markerWords(m.label) : null;
    if (!row) return;                                   // a line that gets no mark on this chart
    if (o.words && !words) return;                      // on the worded chart, lines not worth a marker get none
    if (o.words && xx - lastMarkX < 6) return;          // and a second marker inside the same few pixels is dropped
    const y1 = row === "top" ? T - 20 : T - 4;
    s += "<line class=\"mark\" x1=\"" + xx.toFixed(1) + "\" x2=\"" + xx.toFixed(1) + "\" y1=\"" + y1 + "\" y2=\"" + y0 + "\"><title>" + esc(markerTitle(m.t, m.label)) + "</title></line>";
    if (o.words) {                                      // words, rotated, on the side away from a close neighbour
      const side = xx - lastMarkX < 14 ? 11 : -3; lastMarkX = xx;
      s += "<text class=\"marklbl words\" x=\"" + (xx + side).toFixed(1) + "\" y=\"" + (T - 6) + "\" text-anchor=\"end\" transform=\"rotate(-90 " + (xx + side).toFixed(1) + " " + (T - 6) + ")\">" + esc(words) + "</text>";
    } else if (keep[i]) s += "<text class=\"marklbl\" x=\"" + (xx + 3).toFixed(1) + "\" y=\"" + (row === "top" ? T - 12 : T + 4) + "\">" + markerGlyph(m.label) + "</text>";
  });
  const id = box.id;
  s += "<line class=\"cross\" id=\"" + id + "-cx\" y1=\"" + T + "\" y2=\"" + y0 + "\" style=\"display:none\"/></svg><div class=\"tip\" id=\"" + id + "-tip\"></div>";
  box.innerHTML = s;
  const svg = box.querySelector("svg"), tip = $(id + "-tip"), cx = $(id + "-cx"), hls = panels.map((p, i) => $(id + "-hl" + i));
  svg.onmousemove = e => {
    const rct = svg.getBoundingClientRect(), px = (e.clientX - rct.left) * W / rct.width, py = (e.clientY - rct.top) * H / rct.height;
    const tAt = now - (1 - (px - L) / (xR - L)) * spanMin * 60000;
    let best = rows[0]; rows.forEach(r => { if (Math.abs(r.t - tAt) < Math.abs(best.t - tAt)) best = r; });
    // the panel under the cursor leads the tooltip and is lifted; anywhere in a panel's height counts, not only its bars
    let pi = 0; panels.forEach((p, i) => { const top = T + i * (PH + GAP); if (py >= top - GAP / 2) pi = i; });
    hls.forEach((h, i) => { if (h) h.style.display = i === pi ? "" : "none"; });
    const xx = xOf(best.t); cx.setAttribute("x1", xx); cx.setAttribute("x2", xx); cx.style.display = "";
    tip.style.display = "block"; tip.textContent = panels[pi].tip ? panels[pi].tip(best) : tipText(best);
    placeTip(tip, xx * rct.width / W, e.clientY - rct.top - 30, rct.width);
  };
  svg.onmouseleave = () => { tip.style.display = "none"; cx.style.display = "none"; hls.forEach(h => { if (h) h.style.display = "none"; }); };
}
function renderEnv() {
  const box = $("envchart"); if (!envRows || !lastHistory) return;
  const long = series24 && service ? seriesRows(series24) : null, now = Date.now();
  const spanMin = long ? SERVED_SPAN : logSpanMin();
  const rows = long ? long : envRows.filter(r => r.ok && now - r.t <= spanMin * 60000);
  if (rows.filter(r => r.ok).length < 2) { box.innerHTML = "<p class=\"note\">no logger samples in this window yet</p>"; return; }
  if (long) $("envsub").textContent = "last 24 hours from the gbox service log, 5-minute means; board resets per 5 minutes below";
  const rated = ratedFor(lastModel), maxRpm = rated && rated.fan_max_rpm;
  const fanMax = niceMax(Math.max(Math.max.apply(null, rows.map(r => Math.max(r.fan0, r.fan1))) * 1.05, maxRpm ? maxRpm * 1.1 : 0)) || 5000;
  // 0.7.0: with cgminer-log columns in the served buckets, the panel shows board, chips average and hottest chip (heat
  // order), a band from the sustained level up to the peak, and the serious line; otherwise the firmware's two fields
  const hotRows = !!long && rows.some(r => r.hot_level !== null && r.hot_level !== undefined);
  const temps = (service && service.temps) || {}, serious = temps.hot_serious == null ? 85 : temps.hot_serious;
  const hotSuffix = r => (!hotRows || r.hot_level == null) ? "" : " · chips avg " + fmt(r.chip_avg) + " · hottest " + fmt(r.hot_level) + " (peak " + fmt(r.hot_peak) + ")";
  const panels = [
    { label: "RPM", min: 0, max: fanMax, step: fanMax / 5, series: [["fan0", "", "fan0"], ["fan1", "s2", "fan1"]], rated: maxRpm ? { value: maxRpm, title: "% of max RPM" } : null },
    hotRows
      ? { label: "°C", min: 20, max: 100, step: 20, series: [["board", "", "board"], ["chip_avg", "amber", "chips avg"], ["hot_level", "hot", "hottest"]],
          range: ["hot_level", "hot_peak", "hotband"], guides: [{ value: serious, cls: "guide", text: "serious " + serious }], rated: null }
      : { label: "°C", min: 20, max: 100, step: 20, series: [["chip", "", "chip"], ["board", "s2", "board"]], rated: null } ];
  if (long) panels.push(resetsPanel(rows));      // board resets per 5 minutes, next to the fans and temperatures they disturb
  drawPanels(box, panels, rows, spanMin, now, best => (long ? clockLabel(best.t) : new Date(best.t).toLocaleTimeString()) + (best.ok === false ? " · no samples" : " · fans " + fmt(best.fan0) + " / " + fmt(best.fan1) + " RPM" +
    (maxRpm ? " (" + fmt(100 * Math.max(best.fan0, best.fan1) / maxRpm) + "% of max)" : "") + " · chip " + fmt(best.chip) + " °C · board " + fmt(best.board, 1) + " °C" + hotSuffix(best) + (long ? resetsSuffix(best) : "")),
    "fan speed and temperature history", long ? servedOpts(box, spanMin, series24.bucket_minutes) : null);
  if (long) renderKey("envkey", { bars: true, band: true, temps: hotRows ? serious : null }); else $("envkey").hidden = true;
  $("fanpctnote").textContent = maxRpm ? "Right axis: RPM as a share of " + fmt(maxRpm) + " RPM, the " + rated.name + "'s maximum (observed, not the duty cycle the Fans tile shows)."
    : (lastModel ? "No maximum fan speed on record for " + lastModel + ", so no percent axis." : "");
}
// Power at the wall, from the watts column: failed samples stay in (the plug answers while the miner is down), a
// missing reading breaks the line rather than drawing zero.
function renderWatts() {
  const box = $("wattchart"); if (!envRows || !lastHistory || !service || !service.power || !service.power.configured) return;
  const long = series24 ? seriesRows(series24) : null, now = Date.now();
  const spanMin = long ? SERVED_SPAN : logSpanMin();
  const rows = long ? long : envRows.filter(r => now - r.t <= spanMin * 60000), withW = rows.filter(r => r.watts !== null && r.watts !== undefined);
  if (withW.length < 2) { box.innerHTML = "<p class=\"note\">no plug readings in this window yet</p>"; return; }
  if (long) $("wattsub").textContent = "last 24 hours from the gbox service log, 5-minute means";
  const rated = service.rated && service.rated.rated_watts;
  const wMax = niceMax(Math.max(Math.max.apply(null, withW.map(r => r.watts)) * 1.05, rated ? rated * 1.1 : 0)) || 300;
  const panels = [{ label: "W", min: 0, max: wMax, step: wMax / 5, series: [["watts", "", "watts"]], rated: rated ? { value: rated, title: "% of rated" } : null }];
  drawPanels(box, panels, rows, spanMin, now, best => (long ? clockLabel(best.t) : new Date(best.t).toLocaleTimeString()) + " · " +
    (best.watts === null || best.watts === undefined ? "no reading" : fmt(best.watts) + " W" + (rated ? " (" + fmt(100 * best.watts / rated) + "% of rated)" : "")) + (best.ok ? "" : long ? " · no samples" : " · miner not answering") + (long ? resetsSuffix(best) : ""),
    "power at the wall", long ? servedOpts(box, spanMin, series24.bucket_minutes) : null);
  if (long) renderKey("wattkey", { band: true }); else $("wattkey").hidden = true;
}
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastHistory) { renderChart(lastHistory); renderEnv(); renderWatts(); renderErrors(); } }, 150); });
// This page sends the miner one request at a time (token-check race, see docs/firmware-api.md): the poll cycle and
// the buttons share one `busy` flag. A poll that finds the page busy is skipped; a button waits for the poll to end.
let busy = false;
async function withMiner(fn) {
  while (busy) await new Promise(res => setTimeout(res, 100));
  busy = true;
  try { return await fn(); } finally { busy = false; }
}
async function poll() { if (busy) return; await withMiner(refresh); }
// ---- clock trials ----
let trialData = null;
async function refreshTrials() {
  $("trials").hidden = !service;
  if (!service) return;
  try {
    const r = await fetch("api/trials", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    trialData = await r.json();
  } catch (e) { trialData = null; $("trialsnote").textContent = "could not read the trials table (" + e.message + ")"; }
  renderTrials();
  try {
    const run = await (await fetch("api/trial", { cache: "no-store" })).json();
    const line = trialStatus(run, Date.now());
    $("trialrun").textContent = line; $("trialrun").hidden = !line;
  } catch (e) { $("trialrun").hidden = true; }
}
function renderTrials() {
  const head = $("trialtab").tHead.rows[0], body = $("trialtab").tBodies[0];
  if (!head.children.length) TRIAL_COLUMNS.forEach(c => { const th = document.createElement("th"); th.textContent = c; head.appendChild(th); });
  body.innerHTML = "";
  if (!trialData) return;
  const bySeg = $("trialsegs").checked;
  const rows = bySeg ? trialData.segments.filter(s => !s.short) : trialData.rollup;
  const hidden = trialData.segments.filter(s => s.short).length;
  $("trialsnote").textContent = (hidden ? hidden + " run" + (hidden === 1 ? "" : "s") + " under " + trialData.min_minutes + " min hidden · " : "") +
    (rows.length ? rows.length + " row" + (rows.length === 1 ? "" : "s") : "");
  if (!rows.length) {
    const tr = document.createElement("tr"), td = document.createElement("td");
    td.colSpan = TRIAL_COLUMNS.length; td.className = "empty";
    td.textContent = trialData.segments.length ? "Nothing held long enough yet: the first row appears after " + trialData.min_minutes + " minutes at one clock."
      : "No samples yet. The table fills in as the service logs.";
    tr.appendChild(td); body.appendChild(tr);
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement("tr");
    if (r.last) tr.className = "live";
    trialCells(r).forEach(c => { const td = document.createElement("td"); td.textContent = c.text; if (c.cls) td.className = c.cls; tr.appendChild(td); });
    body.appendChild(tr);
  });
}
$("trialsegs").onchange = renderTrials;
async function serviceTick() { await probeService(); await handOffToken(); await refreshEvents(); await refreshEnv(); await refreshTrials(); }

// ---- controls ----
// Flow: button -> re-read mcb/setting -> build the request -> dialog shows the exact request -> (password, re-checked
// by logging in again) -> PUT -> re-read to confirm -> report the event to the service. Enter never confirms.
let pending = null;
function fillSelect(sel, values, current, label) {
  const key = values.join(",");
  if (sel.dataset.key !== key) {
    sel.innerHTML = "";
    values.forEach(v => { const o = document.createElement("option"); o.value = v; o.textContent = label(v); sel.append(o); });
    sel.dataset.key = key; delete sel.dataset.touched;
  }
  if (!sel.dataset.touched && current !== null) sel.value = String(current);   // follow the miner until the user picks
}
function rangeList(min, max, step) { const out = []; for (let v = min; v <= max; v += step) out.push(v); return out; }
function renderControls(setting) {
  const cr = clockRange(setting), fr = fanRange(setting);
  fillSelect($("clocksel"), rangeList(cr.min, cr.max, cr.step), cr.current, v => v + " MHz" + (v === cr.current ? " (now)" : ""));
  fillSelect($("fansel"), rangeList(fr.min, fr.max, 1), fr.current, v => v + " °C" + (v === fr.current ? " (now)" : ""));
  const presets = presetList(setting, profileFor(lastModel)), sel = $("presetsel");
  fillSelect(sel, presets.map(p => p.level), setting.select,
    lvl => { const p = presets.find(q => q.level === lvl); return "preset " + lvl + ": " + (p.name ? p.name + " (" + p.info + ")" : p.info) + (p.unverified ? " (unverified)" : "") + (!setting.manual && lvl === setting.select ? " (now)" : ""); });
  const chosen = parseInt(sel.value);
  $("btnpreset").disabled = !setting.manual && chosen === setting.select;
  $("presetnote").textContent = (setting.manual
    ? "Clears the manual plan and puts the miner on the chosen firmware preset. "
    : "The miner is on preset " + setting.select + ". ")
    + "Asks for the password." + (presets.some(p => p.unverified) ? " A 0 MHz preset is probably an idle mode; nobody has tested it." : "");
}
function renderChanges(req) {
  const ul = $("cchanges"); ul.innerHTML = "";
  const show = v => typeof v === "string" ? "\"" + v + "\"" : JSON.stringify(v);
  (req.changes || []).forEach(c => { const li = document.createElement("li"); li.textContent = c.key + ": " + show(c.from) + " → " + show(c.to); ul.append(li); });
  const n = (req.changes || []).length;
  $("creqsum").textContent = req.body === null ? "the request has no body" : req.kind === "service" ? "show the full request (to the gbox service" + (req.password ? "; the encrypted password is added when you click" : "") + ")"
    : "show the full request (" + n + " of " + Object.keys(req.body).length + " fields change; the rest is sent back unchanged)";
  $("creq").parentNode.open = req.body === null;
}
// A request to the gbox service (plug or hold): no settings fetch first, since the miner may be off; the dialog is the same.
function openServiceConfirm(req) {
  if (!service) { $("err").textContent = "No gbox service behind this page, so the plug and the hold are out of reach."; return; }
  $("err").textContent = "";
  pending = { req, title: req.title };
  $("ctitle").textContent = req.title; $("csummary").textContent = req.summary; $("creq").textContent = describeRequest(location.origin, req); renderChanges(req);
  $("cpwrow").hidden = !req.password; $("cpw").value = "";
  $("cstatus").textContent = ""; $("cstatus").className = "note";
  $("cok").disabled = false; $("cok").hidden = false; $("ccancel").disabled = false; $("ccancel").textContent = "cancel";
  $("confirm").classList.add("show"); (req.password ? $("cpw") : $("ccancel")).focus();
}
async function runServiceRequest(req, say) {
  const body = Object.assign({}, req.body);
  if (req.password) {
    if (!$("cpw").value) throw new Error("enter the miner password");
    say("sending; the service checks the password with the miner…");
    body.password_hex = await encryptPassword($("cpw").value, crypto.subtle);
    $("cpw").value = "";
  } else say("sending…");
  const r = await fetch(req.path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = "HTTP " + r.status;
    try { msg = (await r.json()).error || msg; } catch (e) {}
    throw new Error(msg);
  }
}
async function openConfirm(title, build) {
  const token = getToken();
  if (!host() || !token) { showLogin(); return; }
  $("err").textContent = "";
  try {
    // build against what the miner holds right now, not the cached copy
    const fresh = await withMiner(() => apiText(base(), token, "mcb/setting"));
    if (fetchAll.cache) fetchAll.cache.st = fresh;
    const req = build(JSON.parse(fresh));
    pending = { req, title };
    $("ctitle").textContent = title; $("csummary").textContent = req.summary; $("creq").textContent = describeRequest(base(), req); renderChanges(req);
    $("cpwrow").hidden = !req.password; $("cpw").value = "";
    $("cstatus").textContent = ""; $("cstatus").className = "note";
    $("cok").disabled = false; $("cok").hidden = false; $("ccancel").disabled = false; $("ccancel").textContent = "cancel";
    $("confirm").classList.add("show"); (req.password ? $("cpw") : $("ccancel")).focus();
  } catch (e) { $("err").textContent = "Could not prepare the request: " + (e.message === "401" ? "the miner rejected the session token" : e.message); }
}
function closeConfirm() { pending = null; $("cpw").value = ""; $("confirm").classList.remove("show"); }
async function runConfirmed() {
  if (!pending) return;
  const req = pending.req, say = (m, cls) => { $("cstatus").textContent = m; $("cstatus").className = cls || "note"; };
  $("cok").disabled = true; $("ccancel").disabled = true;
  try {
    if (req.kind === "service") {
      await runServiceRequest(req, say);
      say("Done. Logged in the service event log.", "note ok");
      $("cok").hidden = true; $("ccancel").disabled = false; $("ccancel").textContent = "close"; $("ccancel").focus();
      pending = null;
      probeService().then(refreshEvents);
      return;
    }
    let token = getToken();
    if (req.password) {
      if (!$("cpw").value) throw new Error("enter the miner password");
      say("checking the password with the miner…");
      token = await withMiner(() => login(base(), $("cpw").value, crypto.subtle));
      $("cpw").value = ""; store.set("gbox_token", token);
    }
    say("sending…");
    await withMiner(() => apiPut(base(), token, req.path, req.body));
    let result = "";
    if (req.path === "mcb/setting") {
      const after = JSON.parse(await withMiner(() => apiText(base(), token, "mcb/setting")));
      result = " The miner now reports plan " + currentPlan(after) + (after.manual ? " (manual)" : " (preset)") + ", fan target " + after.temp_target + " °C.";
      fetchAll.cache = null; ["clocksel", "fansel", "presetsel"].forEach(id => delete $(id).dataset.touched);
    }
    const logged = await reportEvent(req.event, req.restart);
    say("Done." + result + (logged ? " Logged in the service event log." : service ? " The service did not accept the event-log line." : " No gbox service here, so nothing was logged."), "note ok");
    if (req.restart) setBadge("idle", "restarting, back in 60-90 s");
    $("cok").hidden = true; $("ccancel").disabled = false; $("ccancel").textContent = "close"; $("ccancel").focus();
    pending = null;
    refreshEvents().then(refreshEnv).then(refreshTrials);
    setTimeout(poll, 800);
  } catch (e) {
    const msg = e.message === "401" ? "the miner rejected the session token; nothing was sent. Close this and log in again."
      : /login rejected/.test(e.message) ? "wrong password; nothing was sent."
      : e.message + (req.path === "mcb/setting" ? " Check the Miner panel before trying again." : "");
    say("Failed: " + msg, "err");
    $("cok").disabled = false; $("ccancel").disabled = false; if (req.password) { $("cpw").focus(); $("cpw").select(); }
  }
}
$("clocksel").onchange = () => { $("clocksel").dataset.touched = "1"; };
$("fansel").onchange = () => { $("fansel").dataset.touched = "1"; };
$("btnclock").onclick = () => openConfirm("Set the clock", s => planRequest(s, parseInt($("clocksel").value)));
$("btnfan").onclick = () => openConfirm("Set the fan target", s => fanTargetRequest(s, parseInt($("fansel").value)));
$("presetsel").onchange = () => { $("presetsel").dataset.touched = "1"; $("btnpreset").disabled = false; };
$("btnpreset").onclick = () => openConfirm("Use a firmware preset", s => presetRequest(s, parseInt($("presetsel").value)));
$("btnrestart").onclick = () => openConfirm("Soft restart", restartRequest);
$("btnoff").onclick = () => openServiceConfirm(powerActionRequest("off", service));
$("btnon").onclick = () => openServiceConfirm(powerActionRequest("on", service));
$("btncycle").onclick = () => openServiceConfirm(powerActionRequest("cycle", service, ((service || {}).ladder || {}).off_seconds));
$("btnhold").onclick = () => openServiceConfirm(holdRequest(parseInt($("holdsel").value), $("holdwhy").value.trim()));
$("btnrelease").onclick = () => openServiceConfirm(holdReleaseRequest());
$("ccancel").onclick = closeConfirm;
$("cok").onclick = runConfirmed;
// "what is this?" under each control: a click opens the explanation in place (touch and screen readers included)
document.querySelectorAll("button.why").forEach(b => {
  b.onclick = () => { const x = $(b.getAttribute("aria-controls")); const open = x.hidden; x.hidden = !open; b.setAttribute("aria-expanded", open ? "true" : "false"); };
});
$("confirm").addEventListener("keydown", e => {
  // never press-through: Enter does nothing here, not even on a focused button (a keydown preventDefault stops the click)
  if (e.key === "Enter") { e.preventDefault(); return; }
  if (e.key === "Escape" && !$("ccancel").disabled) closeConfirm();
});
probeService().then(() => poll()).then(() => { refreshEnv(); refreshEvents(); refreshTrials(); });
setInterval(poll, 10000); setInterval(serviceTick, 60000);
} // end of presentation
