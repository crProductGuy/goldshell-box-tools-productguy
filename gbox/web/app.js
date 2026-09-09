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
// Top-level fields that differ between what the miner holds and what will be sent: the part of the body to read.
function settingDiff(before, after) {
  const keys = Object.keys(Object.assign({}, before, after));
  return keys.filter(k => JSON.stringify(before[k]) !== JSON.stringify(after[k])).map(k => ({ key: k, from: before[k], to: after[k] }));
}
function describeRequest(base, req) {
  return req.method + " " + base + "/" + req.path + "\n" + (req.body === null || req.body === undefined ? "(no body)" : JSON.stringify(req.body, null, 1));
}
// Event-log lines worth a marker on the fan chart: what the buttons did and what the watchdog did.
function eventMarkers(text) {
  const out = [];
  (text || "").split(/\r?\n/).forEach(line => {
    const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) ((?:dashboard|watchdog): .*)$/.exec(line);
    if (m) out.push({ t: new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime(), label: m[7] });
  });
  return out;
}
// ---- clock trials table (rows come from /api/trials; rollup rows carry bad_pct_min/max and segments, segment rows carry bad_pct) ----
const TRIAL_COLUMNS = ["clock", "fan target", "from", "held", "worst chip, bad share", "bad/hour", "board resets", "HW error", "accepted/hr", "hashrate", "chip temp · fans"];
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
if (typeof module !== "undefined") module.exports = { encryptPassword, login, fetchAll, apiText, apiPut, parseMinerInfo, parseBoards, chipHealth, hashUnit,
  parsePlan, formatPlan, clockRange, planRequest, fanRange, fanTargetRequest, presetList, presetRequest, restartRequest, settingDiff, describeRequest, eventMarkers,
  TRIAL_COLUMNS, trialDuration, trialCells, trialStatus };

// ---- presentation (skipped under Node, where the data layer above is unit-tested) ----
if (typeof document !== "undefined") {
const $ = id => document.getElementById(id);
const fmt = (v, d) => (v === null || v === undefined || isNaN(v)) ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
let baseline = null, lastAccepted = null, lastAcceptedChange = Date.now(), lastHistory = null, fanPct = null, unauthorizedStreak = 0, lastFanRead = 0;
let service = null, tokenHandedTo = null;   // service: /api/health payload when this page is served by gbox
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
      (service.has_token || service.can_login ? "" : " · waiting for login");
    if (service.last_error) $("svcsub").textContent += " · " + service.last_error;
  }
  return service;
}
async function handOffToken() {
  const t = getToken();
  if (!service || !t || (service.has_token && tokenHandedTo === t)) return;
  try {
    const r = await fetch("api/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: t }) });
    if (r.ok) { tokenHandedTo = t; service.has_token = true; }
  } catch (e) { /* not served: nothing to hand off to */ }
}
let eventMarks = [];   // {t, label} for the fan chart, from the service event log
async function refreshEvents() {
  if (!service) return;
  try {
    const text = await (await fetch("api/events", { cache: "no-store" })).text();
    $("events").textContent = text.trim() || "(no events yet)";
    eventMarks = eventMarkers(text);
  } catch (e) {}
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

  const [u20, d20] = hashUnit(info.mhs20), [uav, dav] = hashUnit(info.mhsAv);
  $("mhs20").textContent = fmt(info.mhs20 / d20, d20 === 1 ? 0 : 1); $("unit20").textContent = u20;
  $("mhsav").textContent = fmt(info.mhsAv / dav, dav === 1 ? 0 : 1); $("unitav").textContent = uav;
  $("hwpct").textContent = fmt(info.hwPct, 1) + " %";
  const recentBad = info.hwErrors - baseline.hwErrors, recentAcc = info.accepted - baseline.accepted;
  $("hwrecent").textContent = recentAcc > 0 ? "recent " + fmt(100 * recentBad / (recentBad + recentAcc), 1) + " % (since page opened)" : "recent — (need more samples)";
  $("t_hw").className = "tile" + (info.hwPct >= 15 ? " critical" : info.hwPct >= 8 ? " serious" : "");
  $("rebootcnt").textContent = fmt(info.rebootcnt);
  $("rbdelta").textContent = "since page opened +" + fmt(rbd); $("t_rb").className = "tile" + (rbd > 0 ? " critical" : "");
  $("chiptemp").textContent = fmt(info.chipTemp) + " °C"; $("boardtemp").textContent = "board sensor (what the stock UI shows) " + fmt(info.boardTemp, 1) + " °C";
  $("t_temp").className = "tile" + (info.chipTemp >= 85 ? " critical" : info.chipTemp >= 78 ? " serious" : "");
  $("fans").textContent = (fanPct === null ? "" : fmt(fanPct) + " % · ") + fmt(info.fan0) + " / " + fmt(info.fan1);
  $("fansub").textContent = (fanPct === null ? "" : "% · ") + "RPM fan0 / fan1 · target " + setting.temp_target + " °C";
  $("accepted").textContent = fmt(info.accepted); $("rejected").textContent = "rejected " + fmt(info.rejected);
  const planText = setting.manual ? setting.manualPowerplan : "preset " + setting.select;
  $("clock").textContent = fmt(info.clock) + " MHz"; $("plan").textContent = "plan " + planText;
  const up = info.elapsed || 0, upText = Math.floor(up / 3600) + " h " + Math.floor(up % 3600 / 60) + " min";
  $("title").textContent = status.model || "Goldshell Box"; document.title = (status.model || "Goldshell Box") + " status";
  $("meta").textContent = "fw " + status.firmware + " · up " + upText;
  $("updated").textContent = "updated " + new Date().toLocaleTimeString();

  const dl = $("info"); dl.innerHTML = "";
  [["model", status.model], ["firmware", status.firmware], ["hardware", status.hardware], ["controller", status.mcbversion],
   ["power plan", planText + (setting.manual ? " (manual)" : "")],
   ["fan target temp", setting.temp_target + " °C (steers on the board sensor, not the chips)"],
   ["boards / chips", d.boards.length + " / " + chips.length],
   ["overheat shutdown", setting.tempcontrol === undefined ? "—" : (setting.tempcontrol ? "on" : "OFF (the firmware will not stop the miner when it overheats)")],
   ["uptime", upText], ["overheat flag", info.overheat]]
   .forEach(kvp => { const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = kvp[0]; dd.textContent = kvp[1]; dl.append(dt, dd); });

  renderChips(d.boards, minutes); renderControls(setting); lastHistory = d.history; renderChart(d.history);
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
function renderChart(hist) {
  const box = $("chart"), [unit, div] = hashUnit(Math.max.apply(null, hist)), vals = hist.map(v => v / div);
  const first = vals.findIndex(v => v > 0);
  if (first < 0) { box.innerHTML = "<p class=\"note\">no history yet</p>"; return; }
  const data = vals.slice(first), W = Math.max(box.clientWidth, 320), H = 232, L = 44, R = 12, T = 26, B = 34;
  const yMax = niceMax(Math.max.apply(null, data) * 1.05), step = yMax / 4;
  const x = i => L + (W - L - R) * i / Math.max(data.length - 1, 1), y = v => T + (H - T - B) * (1 - v / yMax);
  const path = data.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  let s = "<svg width=\"" + W + "\" height=\"" + H + "\" viewBox=\"0 0 " + W + " " + H + "\" role=\"img\" aria-label=\"hashrate history\">";
  for (let g = 0; g <= yMax + 1e-9; g += step) s += "<line class=\"grid\" x1=\"" + L + "\" x2=\"" + (W - R) + "\" y1=\"" + y(g) + "\" y2=\"" + y(g) + "\"/><text x=\"" + (L - 6) + "\" y=\"" + (y(g) + 4) + "\" text-anchor=\"end\">" + fmt(g, step < 10 ? 1 : 0) + "</text>";
  s += "<path class=\"area\" d=\"" + path + " L" + x(data.length - 1).toFixed(1) + " " + y(0) + " L" + x(0) + " " + y(0) + " Z\"/><path class=\"line\" d=\"" + path + "\"/>";
  const y0 = H - B, minutesBack = i => (data.length - 1 - i) * SAMPLE_MIN, xAtMin = m => x(data.length - 1 - m / SAMPLE_MIN);
  s += "<line class=\"tick\" x1=\"" + L + "\" x2=\"" + (W - R) + "\" y1=\"" + y0 + "\" y2=\"" + y0 + "\"/>";
  const span = minutesBack(0), labelEvery = W < 700 ? 120 : 60;
  for (let m = 0; m <= span; m += 5) {
    const major = m % 60 === 0, mid = m % 15 === 0, len = major ? 9 : mid ? 6 : 3, xx = xAtMin(m);
    s += "<line class=\"tick" + (major ? " major" : "") + "\" x1=\"" + xx + "\" x2=\"" + xx + "\" y1=\"" + y0 + "\" y2=\"" + (y0 + len) + "\"/>";
    if (m === 0) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"end\">now</text>";
    else if (m % labelEvery === 0 && xx > L + 24) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"middle\">-" + (m / 60) + " h</text>";
  }
  s += "<text x=\"" + (L - 6) + "\" y=\"" + (T - 12) + "\" text-anchor=\"end\">" + unit + "</text>";
  s += "<line class=\"cross\" id=\"cx\" y1=\"" + T + "\" y2=\"" + (H - B) + "\" style=\"display:none\"/><circle class=\"dot\" id=\"cdot\" r=\"4\" style=\"display:none\"/></svg><div class=\"tip\" id=\"tip\"></div>";
  box.innerHTML = s;
  const svg = box.querySelector("svg"), tip = $("tip"), cx = $("cx"), dot = $("cdot");
  svg.onmousemove = e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width, i = Math.round((px - L) / (W - L - R) * (data.length - 1));
    if (i < 0 || i >= data.length) return;
    cx.setAttribute("x1", x(i)); cx.setAttribute("x2", x(i)); cx.style.display = "";
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(data[i])); dot.style.display = "";
    tip.style.display = "block"; tip.textContent = fmt(data[i], div === 1 ? 0 : 1) + " " + unit + " · " + minutesBack(i) + " min ago";
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
    const txt = await r.text();
    const lines = txt.trim().split(/\r?\n/); const head = lines[0].split(",");
    const ix = k => head.indexOf(k);
    const it = ix("time"), ih = ix("http"), if0 = ix("fan0"), if1 = ix("fan1"), ic = ix("tstemp0"), ib = ix("tstemp2");
    envRows = lines.slice(1).map(l => l.split(",")).filter(r => r[ih] === "ok" && r.length > ib)
      .map(r => ({ t: new Date(r[it].replace(" ", "T")).getTime(), fan0: +r[if0], fan1: +r[if1], chip: +r[ic], board: +r[ib] }))
      .filter(r => !isNaN(r.t) && !isNaN(r.fan0));
    renderEnv();
  } catch (e) { $("envchart").innerHTML = "<p class=\"note\">No logger samples yet (" + e.message + ").</p>"; }
}
function renderEnv() {
  const box = $("envchart"); if (!envRows || !lastHistory) return;
  const spanMin = Math.max(lastHistory.filter(v => v > 0).length * SAMPLE_MIN, 60), now = Date.now();
  const rows = envRows.filter(r => now - r.t <= spanMin * 60000);
  if (rows.length < 2) { box.innerHTML = "<p class=\"note\">no logger samples in this window yet</p>"; return; }
  const W = Math.max(box.clientWidth, 320), L = 44, R = 12, PH = 130, GAP = 34, T = 24, B = 34, H = T + PH + GAP + PH + B;
  const xOf = r => L + (W - L - R) * (1 - (now - r.t) / (spanMin * 60000));
  const fanMax = niceMax(Math.max.apply(null, rows.map(r => Math.max(r.fan0, r.fan1))) * 1.05) || 5000;
  const panels = [
    { top: T, label: "RPM", min: 0, max: fanMax, step: fanMax / 5, series: [["fan0", "", "fan0"], ["fan1", "s2", "fan1"]] },
    { top: T + PH + GAP, label: "°C", min: 20, max: 100, step: 20, series: [["chip", "", "chip"], ["board", "s2", "board"]] } ];
  let s = "<svg width=\"" + W + "\" height=\"" + H + "\" viewBox=\"0 0 " + W + " " + H + "\" role=\"img\" aria-label=\"fan speed and temperature history\">";
  panels.forEach(p => {
    const y = v => p.top + PH * (1 - (Math.min(Math.max(v, p.min), p.max) - p.min) / (p.max - p.min));
    for (let g = p.min; g <= p.max + 1e-9; g += p.step) s += "<line class=\"grid\" x1=\"" + L + "\" x2=\"" + (W - R) + "\" y1=\"" + y(g) + "\" y2=\"" + y(g) + "\"/><text x=\"" + (L - 6) + "\" y=\"" + (y(g) + 4) + "\" text-anchor=\"end\">" + fmt(g) + "</text>";
    s += "<text x=\"" + (L - 6) + "\" y=\"" + (p.top - 10) + "\" text-anchor=\"end\">" + p.label + "</text>";
    p.series.forEach(([key, cls, name], idx) => {
      let d = "", prev = null;
      rows.forEach(r => { d += (prev && r.t - prev < 180000 ? "L" : "M") + xOf(r).toFixed(1) + " " + y(r[key]).toFixed(1); prev = r.t; });
      const last = rows[rows.length - 1];
      s += "<path class=\"line " + cls + "\" d=\"" + d + "\"/><text class=\"lbl\" x=\"" + (xOf(last) - 4) + "\" y=\"" + (y(last[key]) + (idx ? 15 : -6)) + "\" text-anchor=\"end\">" + name + " " + fmt(last[key]) + "</text>";
    });
  });
  const y0 = T + PH + GAP + PH, xAtMin = m => L + (W - L - R) * (1 - m / spanMin), labelEvery = W < 700 ? 120 : 60;
  s += "<line class=\"tick\" x1=\"" + L + "\" x2=\"" + (W - R) + "\" y1=\"" + y0 + "\" y2=\"" + y0 + "\"/>";
  for (let m = 0; m <= spanMin; m += 5) {
    const major = m % 60 === 0, mid = m % 15 === 0, len = major ? 9 : mid ? 6 : 3, xx = xAtMin(m);
    s += "<line class=\"tick" + (major ? " major" : "") + "\" x1=\"" + xx + "\" x2=\"" + xx + "\" y1=\"" + y0 + "\" y2=\"" + (y0 + len) + "\"/>";
    if (m === 0) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"end\">now</text>";
    else if (m % labelEvery === 0 && xx > L + 24) s += "<text x=\"" + xx + "\" y=\"" + (y0 + 20) + "\" text-anchor=\"middle\">-" + (m / 60) + " h</text>";
  }
  // markers: what the buttons and the watchdog did, hover for the event text
  const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  eventMarks.filter(m => now - m.t <= spanMin * 60000 && m.t <= now).forEach(m => {
    const xx = L + (W - L - R) * (1 - (now - m.t) / (spanMin * 60000));
    s += "<line class=\"mark\" x1=\"" + xx.toFixed(1) + "\" x2=\"" + xx.toFixed(1) + "\" y1=\"" + (T - 4) + "\" y2=\"" + y0 + "\"><title>" + esc(new Date(m.t).toLocaleTimeString() + " " + m.label) + "</title></line>" +
      "<text class=\"marklbl\" x=\"" + (xx + 3).toFixed(1) + "\" y=\"" + (T + 4) + "\">" + (m.label.startsWith("watchdog") ? "W" : "▼") + "</text>";
  });
  s += "<line class=\"cross\" id=\"ecx\" y1=\"" + T + "\" y2=\"" + y0 + "\" style=\"display:none\"/></svg><div class=\"tip\" id=\"etip\"></div>";
  box.innerHTML = s;
  const svg = box.querySelector("svg"), tip = $("etip"), cx = $("ecx");
  svg.onmousemove = e => {
    const rct = svg.getBoundingClientRect(), px = (e.clientX - rct.left) * W / rct.width, tAt = now - (1 - (px - L) / (W - L - R)) * spanMin * 60000;
    let best = rows[0]; rows.forEach(r => { if (Math.abs(r.t - tAt) < Math.abs(best.t - tAt)) best = r; });
    const xx = xOf(best); cx.setAttribute("x1", xx); cx.setAttribute("x2", xx); cx.style.display = "";
    tip.style.display = "block"; tip.textContent = new Date(best.t).toLocaleTimeString() + " · fans " + fmt(best.fan0) + " / " + fmt(best.fan1) + " RPM · chip " + fmt(best.chip) + " °C · board " + fmt(best.board, 1) + " °C";
    tip.style.left = Math.min(xx * rct.width / W + 12, rct.width - 330) + "px"; tip.style.top = (e.clientY - rct.top - 30) + "px";
  };
  svg.onmouseleave = () => { tip.style.display = "none"; cx.style.display = "none"; };
}
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastHistory) { renderChart(lastHistory); renderEnv(); } }, 150); });
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
  $("creqsum").textContent = req.body === null ? "the request has no body" : "show the full request (" + n + " of " + Object.keys(req.body).length + " fields change; the rest is sent back unchanged)";
  $("creq").parentNode.open = req.body === null;
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
