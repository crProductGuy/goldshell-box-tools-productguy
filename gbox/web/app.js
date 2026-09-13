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
function parseMinerInfo(txt) {
  const n = k => { const v = kv(txt, k); return v === null ? null : parseFloat(v); };
  return { elapsed: n("Device Elapsed"), mhsAv: n("MHS av"), mhs20: n("MHS 20s"), accepted: n("Accepted"), rejected: n("Rejected"),
    hwErrors: n("Hardware Errors"), hwPct: n("Device Hardware%"), clock: n("clock"), fan0: n("fan0"), fan1: n("fan1"),
    chipTemp: n("tstemp-0"), boardTemp: n("tstemp-2"), rebootcnt: n("rebootcnt"), overheat: n("overheat") };
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
async function fetchAll(base, token, slowEvery) {
  const c = fetchAll.cache || (fetchAll.cache = {}), now = Date.now(), stale = !c.t || now - c.t > (slowEvery || 60000);
  const mi = await apiText(base, token, "dbg/minerinfo");
  const ic = await apiText(base, token, "dbg/icinfo");
  if (stale) { c.st = await apiText(base, token, "mcb/setting"); c.status = await apiText(base, token, "mcb/status"); c.hist = await apiText(base, token, "cpb/hshistory"); c.t = now; }
  return { info: parseMinerInfo(mi), boards: parseBoards(ic), setting: JSON.parse(c.st), status: JSON.parse(c.status), history: JSON.parse(c.hist) };
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
const PLAN_RE = /^\s*(\d+)\s*MHz\s+([\d.]+)\s*V\s+(\d+)\s*RPM\s+(\d+)\s*RPM\s*$/;
function parsePlan(plan) {
  const m = PLAN_RE.exec(plan || "");
  if (!m) throw new Error("not a power plan string: " + plan);
  return { mhz: parseInt(m[1]), volts: parseFloat(m[2]), fanA: parseInt(m[3]), fanB: parseInt(m[4]) };
}
function formatPlan(p) { return p.mhz + " MHz " + String(p.volts) + " V " + p.fanA + " RPM " + p.fanB + " RPM"; }
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
function presetList(setting) {
  return (setting.powerplans || []).map(p => {
    let mhz = null;
    try { mhz = parsePlan(p.info).mhz; } catch (e) {}
    return { level: p.level, info: p.info, mhz, unverified: !(mhz > 0) };
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
    ": the watchdog judges nothing until the miner answers twice in a row, or " + (timed ? minutes + " min pass" : "you press Release");
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
function markerGlyph(label) {
  return label.startsWith("service") ? "S" : label.startsWith("power") ? "P" : label.startsWith("watchdog") ? "W" : label.startsWith("hold") ? "H" : "▼";
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
function powerTile(service) {
  const p = (service && service.power) || {};
  if (!p.configured) return { value: "no plug", sub: "see docs/power-cycle.md" };
  const name = (p.alias ? p.alias + " · " : "") + (p.model || "?");
  if (p.state == null) return { value: "unreachable", sub: name };
  if (!p.meter || p.watts == null) return { value: p.state, sub: name + " · no meter" };
  const rated = service.rated && service.rated.rated_watts;
  return { value: Math.round(p.watts) + " W", sub: name + (rated ? " · of " + Math.round(rated) + " W rated" : "") };
}
// Rows of the service log (api/log.csv) for the charts and the interventions table. Columns by header name, so an older
// log without a column still parses; a blank watts cell is null (the plug did not answer, or no meter), never 0.
// Rows that failed to sample are kept with ok:false so an outage is visible; their numbers are NaN.
function envRowsFrom(text) {
  const lines = (text || "").trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const head = lines[0].split(","), ix = k => head.indexOf(k);
  const it = ix("time"), ih = ix("http"), if0 = ix("fan0"), if1 = ix("fan1"), ic = ix("tstemp0"), ib = ix("tstemp2"), iw = ix("watts");
  const ie = ix("hwerr"), ia = ix("accepted"), ir = ix("rebootcnt");
  const num = s => (s === "" || s === undefined) ? NaN : +s;
  return lines.slice(1).map(l => l.split(",")).filter(r => r.length > ib && r[it])
    .map(r => ({ t: new Date(r[it].replace(" ", "T")).getTime(), ok: r[ih] === "ok",
      fan0: num(r[if0]), fan1: num(r[if1]), chip: num(r[ic]), board: num(r[ib]),
      hwerr: num(r[ie]), accepted: num(r[ia]), rebootcnt: num(r[ir]),
      watts: (iw < 0 || r.length <= iw || r[iw] === "") ? null : +r[iw] }))
    .filter(r => !isNaN(r.t));
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
      if ((x = /^cycled (#\d+ today: .*)$/.exec(msg))) { kind = "cycle"; what = "power cycle " + x[1]; result = cameBack(t); }
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
      else if ((x = /^released, (miner back after .*)$/.exec(msg))) { who = "service"; what = "hold released: " + x[1]; }
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
// Rated figures per Goldshell model, for the "% of rated" axes. Same table as gbox/models.py (a test keeps them identical);
// keyed by the /mcb/status model string, looked up ignoring case, spaces and hyphens.
const MODELS = {
  "Goldshell-SCBox": { name: "SC-BOX", rated_mhs: 900000.0, rated_watts: 200.0, fans: 2, fan_max_rpm: 4900.0, boards: 1,
    source: "Goldshell spec via retailer listings (900 GH/s, 200 W); fan max observed on one unit", verified_string: true },
  "Goldshell-SCBox II": { name: "SC-BOX II", rated_mhs: 1900000.0, rated_watts: 400.0, fans: 2, fan_max_rpm: null, boards: 1,
    source: "retailer listings (kryptex, d-central, miningnow); model string not read from a unit", verified_string: false },
  "Goldshell-SCLITE": { name: "SC Lite", rated_mhs: 4400000.0, rated_watts: 950.0, fans: null, fan_max_rpm: 2200.0, boards: null,
    source: "goldshell.company/sclite spec table; model string from Maveth/goldshell-config (fw 2.2.0)", verified_string: false },
};
const modelKey = m => String(m || "").toLowerCase().replace(/[ \-_]/g, "");
const MODELS_BY_KEY = Object.fromEntries(Object.entries(MODELS).map(([k, v]) => [modelKey(k), v]));
function ratedFor(model) { return MODELS_BY_KEY[modelKey(model)] || null; }
function pctOf(value, rated) { return (value === null || value === undefined || !rated || rated <= 0) ? null : 100 * value / rated; }
// The miner's hashrate buffer as the chart draws it: leading zeros (slots a boot wiped) dropped, values in the display unit.
// A lone sample comes back as one point; the caller says so instead of drawing a path that has no length.
function chartData(hist) {
  const [unit, div] = hashUnit(Math.max.apply(null, hist)), vals = hist.map(v => v / div);
  const first = vals.findIndex(v => v > 0);
  return { unit: unit, div: div, data: first < 0 ? [] : vals.slice(first) };
}
// The page's own version, shown in the header: opened as a file there is no service to ask. tests/test_app_js.py keeps it equal to gbox.__version__.
const VERSION = "0.4.3";
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
  s += plug ? "power cycle after two failed restarts and " + lad.after_minutes + " min down, then " + lad.settle_minutes + " min to settle; caps " + lad.max_restarts_per_day + " restarts and " + lad.max_cycles_per_day + " cycles a day. "
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
  const twice = "the miner answers twice in a row" + (hold.ok_streak ? " (" + hold.ok_streak + " so far)" : "");
  return head + ": nothing is judged until " + twice + ", or " + (hold.until ? hold.minutes_left + " min pass" : "you press Release") + ".";
}
if (typeof module !== "undefined") module.exports = { VERSION, clockLabel, newestFirst, ladderLine, encryptPassword, login, fetchAll, apiText, apiPut, parseMinerInfo, parseBoards, chipHealth, hashUnit,
  parsePlan, formatPlan, clockRange, planRequest, fanRange, fanTargetRequest, presetList, presetRequest, restartRequest, settingDiff, describeRequest, eventMarkers,
  powerActionRequest, holdRequest, holdReleaseRequest, holdLine,
  markerGlyph, powerLine, TRIAL_COLUMNS, trialDuration, trialCells, trialStatus, chartData, MODELS, ratedFor, pctOf,
  powerTile, envRowsFrom, lastHour, recentHashrate, interventions, interventionCounts };

// ---- presentation (skipped under Node, where the data layer above is unit-tested) ----
if (typeof document !== "undefined") {
const $ = id => document.getElementById(id);
const fmt = (v, d) => (v === null || v === undefined || isNaN(v)) ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
let baseline = null, lastAccepted = null, lastAcceptedChange = Date.now(), lastHistory = null, lastHistoryAt = 0, fanPct = null, unauthorizedStreak = 0, lastFanRead = 0;
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
    ? "Off and cycle ask for the password; on does not. Each starts a hold: the watchdog judges nothing until the miner answers twice in a row. Hold alone covers an outage you make by hand, such as pulling the cord."
    : "No plug configured (gbox power init), so only Hold and Release here. Press Hold before you pull the cord, so the watchdog does not read the outage as a freeze.";
}
// The Power tile and the watts section's caption; the section itself only shows with a plug configured.
function renderPower() {
  const pt = powerTile(service);
  $("watts").textContent = pt.value; $("plugname").textContent = pt.sub;
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
    const data = await fetchAll(base(), token);
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

function render(d) {
  const info = d.info, chips = d.boards.flat(), setting = d.setting, status = d.status, now = Date.now();
  if (!baseline) baseline = { t: now, rebootcnt: info.rebootcnt, hwErrors: info.hwErrors, accepted: info.accepted, chips: Object.fromEntries(chips.map(c => [c.chip, c])) };
  if (info.accepted !== lastAccepted) { lastAccepted = info.accepted; lastAcceptedChange = now; }
  const stalledMin = (now - lastAcceptedChange) / 60000, minutes = Math.max((now - baseline.t) / 60000, 0.01);
  const rbd = info.rebootcnt - baseline.rebootcnt;

  // status badge: state always carries a word, never color alone
  if (stalledMin >= 5) setBadge("bad", "STALLED · no new shares for " + Math.floor(stalledMin) + " min");
  else if (rbd > 0 && rbd / minutes > 0.5) setBadge("warn", "RESET LOOP · board resetting repeatedly");
  else if (info.chipTemp >= 85) setBadge("warn", "HOT · chips " + fmt(info.chipTemp) + " °C");
  else setBadge("ok", "hashing");

  // Every window is named with a clock time the viewer can see: the boot (from uptime), the last hour (from the
  // service log when served, else the miner's own buffer), or, as a file with no log, the moment this page opened.
  const booted = info.elapsed ? clockLabel(now - info.elapsed * 1000) : null, sinceBoot = "since boot" + (booted ? " " + booted : "");
  const opened = clockLabel(baseline.t), hour = envRows ? lastHour(envRows, now) : null;
  const [u20, d20] = hashUnit(info.mhs20), [uav, dav] = hashUnit(info.mhsAv), rh = recentHashrate(d.history, 60);
  $("mhs20").textContent = fmt(info.mhs20 / d20, d20 === 1 ? 0 : 1); $("unit20").textContent = u20 + " · 20 s reading";
  $("k_av").textContent = "Hashrate " + sinceBoot;
  $("mhsav").textContent = fmt(info.mhsAv / dav, dav === 1 ? 0 : 1);
  $("unitav").textContent = uav + (rh !== null ? " · last hour " + fmt(rh / dav, dav === 1 ? 0 : 1) : "");
  $("hwpct").textContent = fmt(info.hwPct, 1) + " %";
  const recentBad = info.hwErrors - baseline.hwErrors, recentAcc = info.accepted - baseline.accepted, pct = (b, a) => fmt(100 * b / (b + a), 1) + " %";
  $("hwrecent").textContent = sinceBoot + " · " + (hour && hour.bad + hour.accepted > 0 ? "last hour " + pct(hour.bad, hour.accepted)
    : recentAcc > 0 ? pct(recentBad, recentAcc) + " since " + opened + " (page opened)" : "since " + opened + " (page opened): no samples yet");
  $("t_hw").className = "tile" + (info.hwPct >= 15 ? " critical" : info.hwPct >= 8 ? " serious" : "");
  $("rebootcnt").textContent = fmt(info.rebootcnt);
  $("rbdelta").textContent = sinceBoot + " · " + (hour ? fmt(hour.resets) + " in the last hour" : "+" + fmt(rbd) + " since " + opened + " (page opened)");
  $("t_rb").className = "tile" + ((hour ? hour.resets > 0 : rbd > 0) ? " critical" : "");
  $("chipsub").textContent = "good and bad nonces " + sinceBoot + "; bad/min since " + opened + " (page opened)";
  $("chiptemp").textContent = fmt(info.chipTemp) + " °C"; $("boardtemp").textContent = "board sensor (what the stock UI shows) " + fmt(info.boardTemp, 1) + " °C";
  $("t_temp").className = "tile" + (info.chipTemp >= 85 ? " critical" : info.chipTemp >= 78 ? " serious" : "");
  $("fans").textContent = (fanPct === null ? "" : fmt(fanPct) + " % · ") + fmt(info.fan0) + " / " + fmt(info.fan1);
  $("fansub").textContent = (fanPct === null ? "" : "% · ") + "RPM fan0 / fan1 · target " + setting.temp_target + " °C";
  $("accepted").textContent = fmt(info.accepted); $("rejected").textContent = "rejected " + fmt(info.rejected);
  const planText = setting.manual ? setting.manualPowerplan : "preset " + setting.select;
  $("clock").textContent = fmt(info.clock) + " MHz"; $("plan").textContent = "plan " + planText;
  const up = info.elapsed || 0, upText = Math.floor(up / 3600) + " h " + Math.floor(up % 3600 / 60) + " min";
  lastModel = status.model || null;
  $("title").textContent = status.model || "Goldshell Box"; document.title = (status.model || "Goldshell Box") + " status";
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

  renderChips(d.boards, minutes); renderControls(setting); lastHistory = d.history; lastHistoryAt = now; renderChart(d.history);
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
function rightAxis(xR, y, top, max, rated, title) {
  let s = "";
  [0, 25, 50, 75, 100].forEach(p => {
    const v = p / 100 * rated;
    if (v > max + 1e-9) return;
    s += "<line class=\"raxis\" x1=\"" + xR + "\" x2=\"" + (xR + 4) + "\" y1=\"" + y(v).toFixed(1) + "\" y2=\"" + y(v).toFixed(1) + "\"/>" +
      "<text class=\"rlbl\" x=\"" + (xR + 7) + "\" y=\"" + (y(v) + 4).toFixed(1) + "\">" + p + "%</text>";
  });
  return s + "<text class=\"rlbl\" x=\"" + (xR + 46) + "\" y=\"" + (top - 10) + "\" text-anchor=\"end\">" + title + "</text>";
}
function renderChart(hist) {
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
  s += "<text x=\"" + (L - 6) + "\" y=\"" + (T - 12) + "\" text-anchor=\"end\">" + unit + "</text>";
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
    tip.style.left = Math.min(x(i) * r.width / W + 12, r.width - 150) + "px"; tip.style.top = (y(data[i]) * r.height / H - 30) + "px";
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
async function refreshEnv() {
  if (!service) { $("envchart").innerHTML = "<p class=\"note\">Fan and temperature history needs the gbox service: run <code>gbox serve</code> and open the page from the address it prints.</p>"; return; }
  try {
    const r = await fetch("api/log.csv", { cache: "no-store" });
    if (!r.ok) throw new Error("no samples yet");
    envRows = envRowsFrom(await r.text());
    renderEnv(); renderWatts(); renderInterventions();
  } catch (e) { $("envchart").innerHTML = "<p class=\"note\">No logger samples yet (" + e.message + ").</p>"; }
}
// The window both log charts share: as far back as the miner's own hashrate buffer reaches, at least an hour.
function logSpanMin() { return Math.max(lastHistory.filter(v => v > 0).length * SAMPLE_MIN, 60); }
// Shared scaffold for the log charts: stacked panels on one time axis, series that break at a gap or a missing value,
// event markers, an optional right-hand "% of rated" axis per panel, and one hover tooltip. Each panel:
// { label, min, max, step, series: [[rowKey, cssClass, name]...], rated: { value, title } | null }.
function drawPanels(box, panels, rows, spanMin, now, tipText, aria) {
  const W = Math.max(box.clientWidth, 320), L = 44, R = 12, PH = 130, GAP = 34, T = 24, B = 48, n = panels.length;
  const H = T + n * PH + (n - 1) * GAP + B, RW = panels.some(p => p.rated) ? 44 : 0, xR = W - R - RW, y0 = T + n * PH + (n - 1) * GAP;
  const xOf = t => L + (xR - L) * (1 - (now - t) / (spanMin * 60000));
  const has = v => v !== null && v !== undefined && !isNaN(v);
  let s = "<svg width=\"" + W + "\" height=\"" + H + "\" viewBox=\"0 0 " + W + " " + H + "\" role=\"img\" aria-label=\"" + aria + "\">";
  panels.forEach((p, i) => {
    const top = T + i * (PH + GAP), y = v => top + PH * (1 - (Math.min(Math.max(v, p.min), p.max) - p.min) / (p.max - p.min));
    for (let g = p.min; g <= p.max + 1e-9; g += p.step) s += "<line class=\"grid\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + y(g) + "\" y2=\"" + y(g) + "\"/><text x=\"" + (L - 6) + "\" y=\"" + (y(g) + 4) + "\" text-anchor=\"end\">" + fmt(g) + "</text>";
    s += "<text x=\"" + (L - 6) + "\" y=\"" + (top - 10) + "\" text-anchor=\"end\">" + p.label + "</text>";
    p.series.forEach(([key, cls, name], idx) => {
      let d = "", prev = null, last = null;
      rows.forEach(r => {
        if (!has(r[key])) { prev = null; return; }
        d += (prev !== null && r.t - prev < 180000 ? "L" : "M") + xOf(r.t).toFixed(1) + " " + y(r[key]).toFixed(1); prev = r.t; last = r;
      });
      if (!last) return;
      s += "<path class=\"line " + cls + "\" d=\"" + d + "\"/><text class=\"lbl\" x=\"" + (xOf(last.t) - 4) + "\" y=\"" + (y(last[key]) + (idx ? 15 : -6)) + "\" text-anchor=\"end\">" + name + " " + fmt(last[key]) + "</text>";
    });
    if (p.rated) s += rightAxis(xR, y, top, p.max, p.rated.value, p.rated.title);
  });
  const xAtMin = m => L + (xR - L) * (1 - m / spanMin), labelEvery = W < 700 ? 120 : 60;
  s += "<line class=\"tick\" x1=\"" + L + "\" x2=\"" + xR + "\" y1=\"" + y0 + "\" y2=\"" + y0 + "\"/>";
  for (let m = 0; m <= spanMin; m += 5) {
    const major = m % 60 === 0, mid = m % 15 === 0, len = major ? 9 : mid ? 6 : 3, xx = xAtMin(m);
    s += "<line class=\"tick" + (major ? " major" : "") + "\" x1=\"" + xx + "\" x2=\"" + xx + "\" y1=\"" + y0 + "\" y2=\"" + (y0 + len) + "\"/>";
    if (m === 0) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"end\">now</text><text x=\"" + xx + "\" y=\"" + (y0 + 34) + "\" text-anchor=\"end\">" + clockLabel(now) + "</text>";
    else if (m % labelEvery === 0 && xx > L + 24) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"middle\">-" + (m / 60) + " h</text>";
  }
  // markers: what the buttons, the watchdog and the plug did, and each service start (S); hover for the event text
  const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  eventMarks.filter(m => now - m.t <= spanMin * 60000 && m.t <= now).forEach(m => {
    const xx = xOf(m.t);
    s += "<line class=\"mark\" x1=\"" + xx.toFixed(1) + "\" x2=\"" + xx.toFixed(1) + "\" y1=\"" + (T - 4) + "\" y2=\"" + y0 + "\"><title>" + esc(new Date(m.t).toLocaleTimeString() + " " + m.label) + "</title></line>" +
      "<text class=\"marklbl\" x=\"" + (xx + 3).toFixed(1) + "\" y=\"" + (T + 4) + "\">" + markerGlyph(m.label) + "</text>";
  });
  const id = box.id;
  s += "<line class=\"cross\" id=\"" + id + "-cx\" y1=\"" + T + "\" y2=\"" + y0 + "\" style=\"display:none\"/></svg><div class=\"tip\" id=\"" + id + "-tip\"></div>";
  box.innerHTML = s;
  const svg = box.querySelector("svg"), tip = $(id + "-tip"), cx = $(id + "-cx");
  svg.onmousemove = e => {
    const rct = svg.getBoundingClientRect(), px = (e.clientX - rct.left) * W / rct.width, tAt = now - (1 - (px - L) / (xR - L)) * spanMin * 60000;
    let best = rows[0]; rows.forEach(r => { if (Math.abs(r.t - tAt) < Math.abs(best.t - tAt)) best = r; });
    const xx = xOf(best.t); cx.setAttribute("x1", xx); cx.setAttribute("x2", xx); cx.style.display = "";
    tip.style.display = "block"; tip.textContent = tipText(best);
    tip.style.left = Math.min(xx * rct.width / W + 12, rct.width - 330) + "px"; tip.style.top = (e.clientY - rct.top - 30) + "px";
  };
  svg.onmouseleave = () => { tip.style.display = "none"; cx.style.display = "none"; };
}
function renderEnv() {
  const box = $("envchart"); if (!envRows || !lastHistory) return;
  const spanMin = logSpanMin(), now = Date.now();
  const rows = envRows.filter(r => r.ok && now - r.t <= spanMin * 60000);
  if (rows.length < 2) { box.innerHTML = "<p class=\"note\">no logger samples in this window yet</p>"; return; }
  const rated = ratedFor(lastModel), maxRpm = rated && rated.fan_max_rpm;
  const fanMax = niceMax(Math.max(Math.max.apply(null, rows.map(r => Math.max(r.fan0, r.fan1))) * 1.05, maxRpm ? maxRpm * 1.1 : 0)) || 5000;
  const panels = [
    { label: "RPM", min: 0, max: fanMax, step: fanMax / 5, series: [["fan0", "", "fan0"], ["fan1", "s2", "fan1"]], rated: maxRpm ? { value: maxRpm, title: "% of max RPM" } : null },
    { label: "°C", min: 20, max: 100, step: 20, series: [["chip", "", "chip"], ["board", "s2", "board"]], rated: null } ];
  drawPanels(box, panels, rows, spanMin, now, best => new Date(best.t).toLocaleTimeString() + " · fans " + fmt(best.fan0) + " / " + fmt(best.fan1) + " RPM" +
    (maxRpm ? " (" + fmt(100 * Math.max(best.fan0, best.fan1) / maxRpm) + "% of max)" : "") + " · chip " + fmt(best.chip) + " °C · board " + fmt(best.board, 1) + " °C",
    "fan speed and temperature history");
  $("fanpctnote").textContent = maxRpm ? "Right axis: RPM as a share of " + fmt(maxRpm) + " RPM, the " + rated.name + "'s maximum (observed, not the duty cycle the Fans tile shows)."
    : (lastModel ? "No maximum fan speed on record for " + lastModel + ", so no percent axis." : "");
}
// Power at the wall, from the watts column: failed samples stay in (the plug answers while the miner is down), a
// missing reading breaks the line rather than drawing zero.
function renderWatts() {
  const box = $("wattchart"); if (!envRows || !lastHistory || !service || !service.power || !service.power.configured) return;
  const spanMin = logSpanMin(), now = Date.now();
  const rows = envRows.filter(r => now - r.t <= spanMin * 60000), withW = rows.filter(r => r.watts !== null);
  if (withW.length < 2) { box.innerHTML = "<p class=\"note\">no plug readings in this window yet</p>"; return; }
  const rated = service.rated && service.rated.rated_watts;
  const wMax = niceMax(Math.max(Math.max.apply(null, withW.map(r => r.watts)) * 1.05, rated ? rated * 1.1 : 0)) || 300;
  const panels = [{ label: "W", min: 0, max: wMax, step: wMax / 5, series: [["watts", "", "watts"]], rated: rated ? { value: rated, title: "% of rated" } : null }];
  drawPanels(box, panels, rows, spanMin, now, best => new Date(best.t).toLocaleTimeString() + " · " +
    (best.watts === null ? "no reading" : fmt(best.watts) + " W" + (rated ? " (" + fmt(100 * best.watts / rated) + "% of rated)" : "")) + (best.ok ? "" : " · miner not answering"),
    "power at the wall");
}
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastHistory) { renderChart(lastHistory); renderEnv(); renderWatts(); } }, 150); });
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
  const presets = presetList(setting), sel = $("presetsel");
  fillSelect(sel, presets.map(p => p.level), setting.select,
    lvl => { const p = presets.find(q => q.level === lvl); return "preset " + lvl + ": " + p.info + (p.unverified ? " (unverified)" : "") + (!setting.manual && lvl === setting.select ? " (now)" : ""); });
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
$("confirm").addEventListener("keydown", e => {
  // never press-through: Enter does nothing here, not even on a focused button (a keydown preventDefault stops the click)
  if (e.key === "Enter") { e.preventDefault(); return; }
  if (e.key === "Escape" && !$("ccancel").disabled) closeConfirm();
});
probeService().then(() => poll()).then(() => { refreshEnv(); refreshEvents(); refreshTrials(); });
setInterval(poll, 10000); setInterval(serviceTick, 60000);
} // end of presentation
