// Unit tests for the data layer of gbox/web/app.js: the request builders behind the buttons.
// Run by tests/test_app_js.py under `python -m unittest` when Node is installed, or by hand:
//     node tests/app_test.js
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const app = require(path.join(__dirname, "..", "gbox", "web", "app.js"));
const setting = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "mcb_setting.json"), "utf8"));
const frozen = JSON.stringify(setting);

const tests = {
  "lastHour: counts over the last 60 min of the service log, summing increments so a boot's counter reset never goes negative"() {
    const t0 = new Date(2026, 8, 12, 15, 0, 0).getTime(), m = 60000;
    const row = (min, ok, hwerr, acc, rb) => ({ t: t0 + min * m, ok: ok, hwerr: hwerr, accepted: acc, rebootcnt: rb });
    const rows = [row(-90, true, 100, 1000, 5), row(-70, true, 110, 1100, 5),   // before the hour: the last one is the base
                  row(-50, true, 120, 1200, 5), row(-40, true, 125, 1300, 6),
                  row(-30, false, NaN, NaN, NaN),                               // a failed sample is skipped
                  row(-20, true, 2, 50, 0),                                     // the miner rebooted: counters start over
                  row(-10, true, 4, 150, 0)];
    assert.deepStrictEqual(app.lastHour(rows, t0), { resets: 1, bad: 19, accepted: 350, samples: 4 });
    assert.deepStrictEqual(app.lastHour(rows.slice(2), t0), { resets: 1, bad: 9, accepted: 250, samples: 4 });  // no base row: from the first inside
    assert.strictEqual(app.lastHour([], t0), null);
    assert.strictEqual(app.lastHour([row(-30, true, 1, 1, 1)], t0), null);          // one row is no interval
    assert.strictEqual(app.lastHour([row(-90, true, 1, 1, 1)], t0), null);          // nothing inside the hour
  },
  "recentHashrate: the mean of the last N positive samples of the miner's buffer, or null"() {
    assert.strictEqual(app.recentHashrate([0, 0, 700000, 800000], 60), 750000);
    assert.strictEqual(app.recentHashrate([600000, 700000, 800000], 2), 750000);
    assert.strictEqual(app.recentHashrate([0, 0], 60), null);
    assert.strictEqual(app.recentHashrate(null, 60), null);
  },
  "envRowsFrom carries the counters the tiles need for the last hour"() {
    const csv = "time,http,elapsed,mhs_av,mhs_20s,hwerr,hwerr_pct,accepted,rejected,clock,fan0,fan1,tstemp0,tstemp1,tstemp2,rebootcnt\n" +
      "2026-09-12 15:00:00,ok,100,1,1,7,0.1,5000,3,575,1200,1200,70,70,62,2\n";
    const r = app.envRowsFrom(csv)[0];
    assert.deepStrictEqual([r.hwerr, r.accepted, r.rebootcnt], [7, 5000, 2]);
  },
  "newestFirst: the service log reversed, newest line on top, blank lines dropped"() {
    assert.strictEqual(app.newestFirst("2026-09-12 10:00:00 a\n2026-09-12 11:00:00 b\n2026-09-12 12:00:00 c\n"),
      "2026-09-12 12:00:00 c\n2026-09-12 11:00:00 b\n2026-09-12 10:00:00 a");
    assert.strictEqual(app.newestFirst(""), "");
  },
  "ladderLine: the watchdog's timings and caps in one sentence, and where to change them"() {
    const lad = { stall_minutes: 5, unreachable_minutes: 2, min_gap_minutes: 5, max_restarts_per_day: 20,
      after_minutes: 5, settle_minutes: 20, max_cycles_per_day: 8, config_path: "C:\\u\\.gbox\\config.json" };
    assert.strictEqual(app.ladderLine({ watchdog: { enabled: true }, power: { configured: true }, ladder: lad }),
      "Ladder: soft restart after 2 min unreachable or 5 min of frozen shares, a second one 5 min later; " +
      "power cycle after two failed restarts and 5 min down, then 20 min to settle; caps 20 restarts and 8 cycles a day. " +
      "Set in C:\\u\\.gbox\\config.json (watchdog and power blocks); restart the service after editing.");
    assert.strictEqual(app.ladderLine({ watchdog: { enabled: true }, power: { configured: false }, ladder: Object.assign({}, lad, { after_minutes: null, max_cycles_per_day: null }) }),
      "Ladder: soft restart after 2 min unreachable or 5 min of frozen shares, a second one 5 min later; " +
      "no plug, so no power cycle (docs/power-cycle.md); cap 20 restarts a day. " +
      "Set in C:\\u\\.gbox\\config.json (watchdog block); restart the service after editing.");
    assert.strictEqual(app.ladderLine({ watchdog: { enabled: false }, ladder: lad }), "Watchdog off for this run (--no-watchdog, or \"enabled\": false in C:\\u\\.gbox\\config.json).");
    assert.strictEqual(app.ladderLine({ watchdog: { enabled: true } }), "");
    const sched = Object.assign({}, lad, { schedule: { off: "23:00", on: "06:00" } });
    assert.ok(app.ladderLine({ watchdog: { enabled: true }, power: { configured: true }, ladder: sched })
      .endsWith("restart the service after editing. Schedule: off 23:00, on 06:00, every day (power.schedule in the same file)."));
    assert.ok(app.ladderLine({ watchdog: { enabled: true }, power: { configured: true }, ladder: Object.assign({}, sched, { schedule: { off: "23:00", on: "06:00", days: ["mon", "fri"] } }) })
      .endsWith("Schedule: off 23:00, on 06:00, mon, fri (power.schedule in the same file)."));
  },
  "clockLabel: the wall-clock time under 'now', 24-hour, minutes only"() {
    assert.strictEqual(app.clockLabel(new Date(2026, 8, 12, 15, 7, 9).getTime()), "15:07");
    assert.strictEqual(app.clockLabel(new Date(2026, 8, 12, 0, 0, 0).getTime()), "00:00");
  },
  "VERSION is the page's own version string"() {
    assert.match(app.VERSION, /^\d+\.\d+\.\d+$/);
  },
  "clock range comes from the presets, current from the manual plan"() {
    assert.deepStrictEqual(app.clockRange(setting), { min: 300, max: 725, step: 25, current: 600 });
    const preset = Object.assign({}, setting, { manual: false });
    assert.strictEqual(app.clockRange(preset).current, 725);
  },
  "plan request keeps volts and fans, sets manual, does not touch the input"() {
    const r = app.planRequest(setting, 625);
    assert.strictEqual(r.method, "PUT");
    assert.strictEqual(r.path, "mcb/setting");
    assert.strictEqual(r.body.manualPowerplan, "625 MHz 0.41 V 90 RPM 90 RPM");
    assert.strictEqual(r.body.manual, true);
    assert.strictEqual(r.body.temp_target, 65);
    assert.strictEqual(r.password, true);
    assert.match(r.summary, /600 MHz .* 625 MHz/);
    assert.match(r.event, /^clock set to 625 MHz/);
    assert.strictEqual(JSON.stringify(setting), frozen);
  },
  "plan request takes volts and fans from preset 0 when there is no manual plan yet"() {
    const s = Object.assign({}, setting, { manual: false, manualPowerplan: "" });
    assert.strictEqual(app.planRequest(s, 500).body.manualPowerplan, "500 MHz 0.41 V 70 RPM 70 RPM");
  },
  "plan request rejects off-step and out-of-range clocks"() {
    for (const bad of [610, 275, 750, NaN, "600"]) assert.throws(() => app.planRequest(setting, bad), /multiple of 25/);
  },
  "fan target request changes only temp_target within the firmware range"() {
    const r = app.fanTargetRequest(setting, 70);
    assert.strictEqual(r.body.temp_target, 70);
    assert.strictEqual(r.body.manualPowerplan, setting.manualPowerplan);
    assert.strictEqual(r.password, false);
    assert.match(r.event, /^fan target set to 70 C \(was 65\)/);
    assert.deepStrictEqual(app.fanRange(setting), { min: 65, max: 75, current: 65 });
    for (const bad of [60, 80, 67.5]) assert.throws(() => app.fanTargetRequest(setting, bad), /between 65 and 75/);
  },
  "fan range falls back to 65-75 when the firmware does not say"() {
    const s = Object.assign({}, setting); delete s.temp_targets;
    assert.deepStrictEqual(app.fanRange(s), { min: 65, max: 75, current: 65 });
  },
  "preset list comes from the firmware, zero-clock plans flagged unverified"() {
    assert.deepStrictEqual(app.presetList(setting), [
      { level: 0, info: "725 MHz 0.41 V 70 RPM 70 RPM", mhz: 725, unverified: false },
      { level: 3, info: "0 MHz 0 V 70 RPM 70 RPM", mhz: 0, unverified: true }]);
  },
  "preset request selects a level, clears manual, keeps the manual plan string"() {
    const r = app.presetRequest(setting, 0);
    assert.strictEqual(r.body.manual, false);
    assert.strictEqual(r.body.select, 0);
    assert.strictEqual(r.body.manualPowerplan, setting.manualPowerplan);
    assert.strictEqual(r.password, true);
    assert.match(r.summary, /600 MHz .*manual.* 725 MHz .*preset 0/);
    assert.match(r.event, /^switched to preset 0 \(725 MHz 0\.41 V 70 RPM 70 RPM\); was manual "600 MHz/);
    const fromPreset = app.presetRequest(Object.assign({}, setting, { manual: false, select: 0 }), 3);
    assert.strictEqual(fromPreset.body.select, 3);
    assert.match(fromPreset.event, /was preset 0/);
  },
  "clock and fan requests reject a no-op so the dialog never offers an empty change"() {
    assert.throws(() => app.planRequest(setting, 600), /already 600 MHz/);
    assert.throws(() => app.fanTargetRequest(setting, 65), /already 65/);
    // a preset at the same MHz as the manual plan is still a change (manual -> preset)
    assert.doesNotThrow(() => app.planRequest(Object.assign({}, setting, { manual: false, select: 0 }), 725) === undefined);
  },
  "preset request rejects unknown levels and a no-op"() {
    assert.throws(() => app.presetRequest(setting, 1), /no preset level 1/);
    assert.throws(() => app.presetRequest(Object.assign({}, setting, { manual: false, select: 0 }), 0), /already on preset 0/);
  },
  "settingDiff lists changed top-level fields with old and new values"() {
    assert.deepStrictEqual(app.settingDiff(setting, app.fanTargetRequest(setting, 70).body), [{ key: "temp_target", from: 65, to: 70 }]);
    assert.deepStrictEqual(app.settingDiff(setting, app.presetRequest(setting, 0).body), [{ key: "manual", from: true, to: false }]);
    assert.deepStrictEqual(app.settingDiff(setting, setting), []);
  },
  "every settings request carries its diff; restart carries none"() {
    assert.deepStrictEqual(app.planRequest(setting, 625).changes.map(c => c.key), ["manualPowerplan"]);
    assert.deepStrictEqual(app.fanTargetRequest(setting, 70).changes.map(c => c.key), ["temp_target"]);
    assert.deepStrictEqual(app.presetRequest(setting, 3).changes.map(c => c.key), ["select", "manual"]);
    assert.deepStrictEqual(app.restartRequest().changes, []);
  },
  "restart request has no body and flags the watchdog"() {
    const r = app.restartRequest();
    assert.deepStrictEqual([r.method, r.path, r.body, r.password, r.restart], ["PUT", "mcb/restart", null, true, true]);
    assert.strictEqual(r.event, "soft restart sent");
  },
  "describeRequest shows the exact method, URL and body"() {
    const txt = app.describeRequest("http://192.168.1.100", app.planRequest(setting, 625));
    assert.match(txt, /^PUT http:\/\/192\.168\.1\.100\/mcb\/setting\n\{/);
    assert.match(txt, /"manualPowerplan": "625 MHz 0\.41 V 90 RPM 90 RPM"/);
    assert.strictEqual(app.describeRequest("http://m", app.restartRequest()), "PUT http://m/mcb/restart\n(no body)");
  },
  "event markers are parsed from the event log; a service start counts, other service lines do not"() {
    const log = "2026-09-06 10:00:00 service: started v0.1.0, miner 192.168.1.100, poll 30s, watchdog on\n" +
      "2026-09-06 10:00:05 service: session token received from the dashboard\n" +
      "2026-09-06 10:05:30 dashboard: clock set to 625 MHz\n" +
      "2026-09-06 11:10:00 watchdog: restart #1 sent (miner unreachable for 2 min)\nnot a log line\n";
    const marks = app.eventMarkers(log);
    assert.strictEqual(marks.length, 3);
    assert.strictEqual(marks[0].t, new Date(2026, 8, 6, 10, 0, 0).getTime());
    assert.match(marks[0].label, /^service: started v0\.1\.0/);
    assert.strictEqual(app.markerGlyph(marks[0].label), "S");
    assert.strictEqual(marks[1].t, new Date(2026, 8, 6, 10, 5, 30).getTime());
    assert.strictEqual(marks[1].label, "dashboard: clock set to 625 MHz");
    assert.match(marks[2].label, /^watchdog: restart #1/);
  },
  "power lines get a marker too, drawn as P"() {
    const log = "2026-09-09 22:39:00 power: would cycle now (miner unreachable for 2 min; 34 W before)\n" +
                "2026-09-09 22:40:00 power: plug back\n" +
                "2026-09-09 22:41:00 service: power plug HS110(US) 'x', meter yes, ARMED: the watchdog may cycle it\n";
    const marks = app.eventMarkers(log);
    assert.strictEqual(marks.length, 2);
    assert.match(marks[0].label, /^power: would cycle/);
    assert.strictEqual(app.markerGlyph(marks[0].label), "P");
    assert.strictEqual(app.markerGlyph("watchdog: restart #1 sent"), "W");
    assert.strictEqual(app.markerGlyph("dashboard: clock set to 575 MHz"), "▼");
  },
  "the service line says what the plug reads, or nothing without one"() {
    assert.strictEqual(app.powerLine({ power: { configured: false } }), "");
    assert.strictEqual(app.powerLine({}), "");
    const on = { configured: true, model: "HS110(US)", meter: true, state: "on", watts: 187.8, cycle: false, cycles_today: 0, last_reason: null };
    assert.strictEqual(app.powerLine({ power: on }), " · plug HS110(US) on, 188 W, dry run, 0 cycles today");
    const armed = Object.assign({}, on, { cycle: true, cycles_today: 1, state: "off", watts: 0 });
    assert.strictEqual(app.powerLine({ power: armed }), " · plug HS110(US) off, 0 W, armed, 1 cycle today");
    const noMeter = Object.assign({}, on, { model: "HS105(US)", meter: false, watts: null });
    assert.strictEqual(app.powerLine({ power: noMeter }), " · plug HS105(US) on, no meter, dry run, 0 cycles today");
    const dark = Object.assign({}, on, { state: null, watts: null });
    assert.strictEqual(app.powerLine({ power: dark }), " · plug HS110(US) unreachable, dry run, 0 cycles today");
  },
  "trial durations read as hours and minutes"() {
    assert.strictEqual(app.trialDuration(9.4), "9 min");
    assert.strictEqual(app.trialDuration(58), "58 min");
    assert.strictEqual(app.trialDuration(637.2), "10h 37m");
    assert.strictEqual(app.trialDuration(2899), "2d 0h 19m");
  },
  "trial row cells: rollup with a bad-share range, partial marker, approximate HW%"() {
    const row = { clock: 600, fan_target: 65, segments: 2, start: "2026-09-08 10:00:00", end: "2026-09-08 10:55:00", minutes: 54,
      last: false, worst_chip: 8, bad_pct_min: 11.11, bad_pct_max: 20, bad_partial: true, bad_per_hour: 106.2, resets: 2,
      hw_pct: 0.48, hw_approx: true, accepted_per_hour: 600, mhs: 755000, chip_temp: 70.4, fan_rpm: 2294, overheat: 1,
      watts: 200.4, watts_n: 25, gh_per_w: 3.767 };
    const cells = app.trialCells(row);
    assert.strictEqual(cells.length, app.TRIAL_COLUMNS.length);
    const text = cells.map(c => c.text);
    assert.strictEqual(text[0], "600 MHz");
    assert.strictEqual(text[1], "65 °C");
    assert.match(text[2], /^09-08 10:00/);
    assert.strictEqual(text[3], "54 min · 2 segs");
    assert.strictEqual(text[4], "chip 8 ≥11.1-20.0%");
    assert.strictEqual(text[5], "106.2");
    assert.strictEqual(text[6], "2");
    assert.strictEqual(text[7], "~0.48%");
    assert.strictEqual(text[8], "600");
    assert.strictEqual(text[9], "755 GH/s");
    assert.strictEqual(text[10], "200 W");
    assert.strictEqual(text[11], "3.77");
    assert.strictEqual(text[12], "70.4 °C · 2294 RPM · 1 overheat");
    assert.strictEqual(cells[6].cls, "critical");            // resets above zero
    assert.strictEqual(cells[4].cls, "critical");            // bad share above 5 percent
  },
  "trial row cells: clean segment, unknown fan target, live marker"() {
    const row = { clock: 575, fan_target: null, start: "2026-09-08 10:26:00", end: "2026-09-08 21:03:00", minutes: 637.2, last: true,
      worst_chip: null, bad_pct: null, bad_partial: false, bad_per_hour: 0, resets: 0, hw_pct: 0.31, hw_approx: false,
      accepted_per_hour: 595.4, mhs: 735000, chip_temp: 70.4, fan_rpm: 1389, overheat: 0, watts: null, watts_n: 0, gh_per_w: null };
    const text = app.trialCells(row).map(c => c.text);
    assert.strictEqual(text[1], "?");
    assert.strictEqual(text[3], "10h 37m · live");
    assert.strictEqual(text[4], "none flagged");
    assert.strictEqual(text[7], "0.31%");
    assert.strictEqual(text[8], "595");
    assert.strictEqual(text[10], "?");                       // logged before the plug: no watts
    assert.strictEqual(text[11], "?");
    assert.strictEqual(text[12], "70.4 °C · 1389 RPM");
    assert.strictEqual(app.trialCells(row)[6].cls, "");
    const mild = app.trialCells(Object.assign({}, row, { worst_chip: 8, bad_pct: 0.13, bad_per_hour: 0.8 }));
    assert.strictEqual(mild[4].text, "chip 8 0.1%");
    assert.strictEqual(mild[4].cls, "");
    assert.strictEqual(app.trialCells(Object.assign({}, row, { worst_chip: 8, bad_pct: 2.5 }))[4].cls, "serious");
  },
  "trial status line from the runner's progress file"() {
    assert.strictEqual(app.trialStatus({}, 0), "");
    assert.strictEqual(app.trialStatus(null, 0), "");
    const run = { clocks: [550, 575, 600], hours: 4, end: 550, step: 2, clock: 575, status: "holding",
      started: "2026-09-08 22:00:00", step_started: "2026-09-08 23:10:00", step_ends: "2026-09-09 03:20:00", checks: 14 };
    const now = new Date(2026, 8, 9, 0, 22, 0).getTime();
    assert.strictEqual(app.trialStatus(run, now),
      "Trial running: step 2 of 3, 575 MHz, 1h 12m of 4h held, ends at 550 MHz. Started from the command line; Ctrl-C there stops it.");
    const settling = Object.assign({}, run, { status: "settling", step_started: "2026-09-09 00:20:00" });
    assert.match(app.trialStatus(settling, now), /^Trial running: step 2 of 3, 575 MHz, settling/);
  },
  "power tile: watts with the plug's name and model, or what stands in for them"() {
    const on = { configured: true, model: "HS110(US)", alias: "workbench", meter: true, state: "on", watts: 187.4, cycle: true, cycles_today: 0 };
    assert.deepStrictEqual(app.powerTile({ power: on, rated: { rated_watts: 200 } }), { value: "187 W", sub: "workbench · HS110(US) · of 200 W rated" });
    assert.deepStrictEqual(app.powerTile({ power: on }), { value: "187 W", sub: "workbench · HS110(US)" });
    assert.deepStrictEqual(app.powerTile({ power: Object.assign({}, on, { alias: "" }) }), { value: "187 W", sub: "HS110(US)" });
    assert.deepStrictEqual(app.powerTile({ power: Object.assign({}, on, { meter: false, watts: null }) }), { value: "on", sub: "workbench · HS110(US) · no meter" });
    assert.deepStrictEqual(app.powerTile({ power: Object.assign({}, on, { state: null, watts: null }) }), { value: "unreachable", sub: "workbench · HS110(US)" });
    assert.deepStrictEqual(app.powerTile({ power: { configured: false } }), { value: "no plug", sub: "see docs/power-cycle.md" });
    assert.deepStrictEqual(app.powerTile(null), { value: "no plug", sub: "see docs/power-cycle.md" });
  },
  "log rows from the CSV: columns by name, watts null when the column is missing or blank, never 0"() {
    const head22 = "time,http,elapsed,mhs_av,mhs_20s,hwerr,hwerr_pct,accepted,rejected,clock,fan0,fan1,tstemp0,tstemp1,tstemp2,rebootcnt,weak_chips,nonces_good,nonces_bad,temp_target,overheat,watts";
    const csv = head22 + "\n" +
      "2026-09-10 16:20:16,ok,100,1,2,3,0.4,5,0,575.0,1200,1210,71,71,63.5,0,,1,2,65,0,187.2\n" +
      "2026-09-10 16:20:46,ok,130,1,2,3,0.4,5,0,575.0,1200,1210,71,71,63.5,0,,1,2,65,0,\n" +
      "2026-09-10 16:21:16,ERR:timeout,,,,,,,,,,,,,,,,,,,,38.7\n" +
      "not a row\n";
    const rows = app.envRowsFrom(csv);
    assert.strictEqual(rows.length, 3);
    assert.deepStrictEqual(rows[0], { t: new Date(2026, 8, 10, 16, 20, 16).getTime(), ok: true, fan0: 1200, fan1: 1210, chip: 71, board: 63.5, hwerr: 3, accepted: 5, rebootcnt: 0, watts: 187.2 });
    assert.strictEqual(rows[1].watts, null);
    assert.strictEqual(rows[2].ok, false);
    assert.strictEqual(rows[2].watts, 38.7);                 // the plug still answers while the miner is down
    assert.ok(isNaN(rows[2].fan0));
    const old = "time,http,elapsed,mhs_av,mhs_20s,hwerr,hwerr_pct,accepted,rejected,clock,fan0,fan1,tstemp0,tstemp1,tstemp2,rebootcnt,weak_chips\n" +
      "2026-09-08 10:00:00,ok,1,2,3,4,0.4,5,0,600.0,3000,3000,70,70,63,0,\n";
    assert.strictEqual(app.envRowsFrom(old)[0].watts, null);
    assert.strictEqual(app.envRowsFrom(old)[0].fan0, 3000);
    assert.deepStrictEqual(app.envRowsFrom(""), []);
  },
  "interventions: what the watchdog, the plug, the service and you did, newest first, with how long the miner took to come back"() {
    const T = (h, m, s, d) => new Date(2026, 8, d || 10, h, m, s).getTime();
    const row = (d, h, m, s, ok) => ({ t: T(h, m, s, d), ok: ok, fan0: 1200, fan1: 1200, chip: 70, board: 63, watts: ok ? 187 : 39 });
    const rows = [row(10, 12, 10, 43, true), row(10, 12, 11, 13, false), row(10, 12, 11, 43, false), row(10, 12, 12, 13, true),
                  row(9, 19, 2, 0, false), row(9, 19, 2, 30, false), row(9, 19, 40, 0, false), row(9, 21, 52, 0, true)];   // out of order on purpose
    const log = [
      "2026-09-09 19:02:07 watchdog: restart #1 sent (miner unreachable for 2 min)",
      "2026-09-09 20:29:00 watchdog: miner unreachable for 2 min, but 6 restarts in 24 h is the cap; not restarting",
      "2026-09-10 12:07:00 service: started v0.3.0, miner 192.0.2.5, poll 30s, watchdog on, power plug armed, listening on 127.0.0.1:8765",
      "2026-09-10 12:11:07 dashboard: power: cycled by hand (gbox power cycle; 187 W before)",
      "2026-09-10 12:12:42 service: started v0.3.0, miner 192.0.2.5, poll 30s, watchdog on, power plug armed, listening on 127.0.0.1:8765",
      "2026-09-10 12:33:13 power: cycled #1 today: off 15 s, on (miner unreachable for 2 min; 187 W before)",
      "2026-09-10 12:40:00 dashboard: clock set to 575 MHz",
      "2026-09-10 12:41:00 power: would cycle now (miner unreachable for 2 min; 34 W before)",
      "2026-09-10 12:42:00 watchdog: restart attempt failed: timed out (miner unreachable for 2 min)",
      "2026-09-10 12:43:00 power: plug back",
      "2026-09-10 12:44:00 service: session token received from the dashboard",
      "garbage line",
    ].join("\n") + "\n";
    const iv = app.interventions(log, rows);
    assert.deepStrictEqual(iv.map(i => i.who), ["watchdog", "plug", "you", "plug", "service", "you", "service", "watchdog", "watchdog"]);
    assert.deepStrictEqual(iv.map(i => i.kind), ["failed", "dryrun", "action", "cycle", "start", "cycle", "start", "declined", "restart"]);
    const byT = Object.fromEntries(iv.map(i => [i.t, i]));
    assert.strictEqual(byT[T(12, 11, 7)].what, "power cycle by hand (gbox power cycle; 187 W before)");
    assert.strictEqual(byT[T(12, 11, 7)].result, "miner back after 66 s");        // first ok row after the outage the event caused
    assert.strictEqual(byT[T(12, 33, 13)].what, "power cycle #1 today: off 15 s, on (miner unreachable for 2 min; 187 W before)");
    assert.strictEqual(byT[T(12, 33, 13)].result, "no outage seen in the log");       // rows do not cover it
    assert.strictEqual(byT[T(12, 40, 0)].what, "clock set to 575 MHz");
    assert.strictEqual(byT[T(12, 40, 0)].result, "");
    assert.strictEqual(byT[T(12, 41, 0)].what, "would cycle (dry run): miner unreachable for 2 min; 34 W before");
    assert.strictEqual(byT[T(12, 42, 0)].what, "soft restart failed: timed out (miner unreachable for 2 min)");
    assert.strictEqual(byT[T(12, 7, 0)].what, "service started v0.3.0");
    const r1 = iv[iv.length - 1];
    assert.strictEqual(r1.what, "soft restart #1: miner unreachable for 2 min");
    assert.strictEqual(r1.result, "miner back after 2 h 50 min");          // 19:02:07 -> the 21:52:00 ok row
    assert.strictEqual(byT[new Date(2026, 8, 9, 20, 29, 0).getTime()].what, "declined: 6 restarts in 24 h is the cap (miner unreachable for 2 min)");
    assert.deepStrictEqual(app.interventionCounts(iv), { restarts: 1, cycles: 2, actions: 2 });
  },
  "hold line: what is held, until when, by whom, and what lifts it; empty without a hold"() {
    const hold = { since: "2026-09-12 21:00:00", until: "2026-09-12 22:00:00", reason: "PSU swap", source: "page", minutes_left: 60, ok_streak: 0 };
    assert.strictEqual(app.holdLine({ hold }),
      "Held until 22:00 (PSU swap), by you since 21:00: nothing is judged until the miner answers twice in a row, or 60 min pass.");
    assert.strictEqual(app.holdLine({ hold: Object.assign({}, hold, { until: null, minutes_left: null, reason: "switched off" }) }),
      "Held with no expiry (switched off), by you since 21:00: nothing is judged until the miner answers twice in a row, or you press Release.");
    assert.strictEqual(app.holdLine({ hold: Object.assign({}, hold, { source: "schedule", reason: "", ok_streak: 1 }) }),
      "Held until 22:00, by the schedule since 21:00: nothing is judged until the miner answers twice in a row (1 so far), or 60 min pass.");
    assert.strictEqual(app.holdLine({ hold: null }), "");
    assert.strictEqual(app.holdLine(null), "");
  },
  "power action requests: off and cycle ask for the password, on does not; the summary reads the plug"() {
    const service = { power: { configured: true, alias: "workbench", model: "HS110(US)", meter: true, watts: 197.3, state: "on" },
                      ladder: { settle_minutes: 20, after_minutes: 5 } };
    const off = app.powerActionRequest("off", service);
    assert.deepStrictEqual(off, { kind: "service", method: "POST", path: "api/power", body: { action: "off" }, password: true, changes: [], restart: false,
      title: "Switch the plug off", event: null,
      summary: "switch off 'workbench' (HS110(US)), reading 197 W now (that looks like a miner hashing, not a hung one); the miner stays off, unjudged, until you press On" });
    const on = app.powerActionRequest("on", service);
    assert.strictEqual(on.password, false);
    assert.deepStrictEqual(on.body, { action: "on" });
    assert.strictEqual(on.summary, "switch on 'workbench' (HS110(US)); the watchdog waits 20 min for the boot before judging anything");
    const cycle = app.powerActionRequest("cycle", service, 15);
    assert.strictEqual(cycle.password, true);
    assert.deepStrictEqual(cycle.body, { action: "cycle", off_seconds: 15 });
    assert.strictEqual(cycle.summary, "cut power to 'workbench' (HS110(US)) for 15 s, then restore it; reading 197 W now (that looks like a miner hashing, not a hung one); the watchdog waits 20 min for the boot");
    const idle = app.powerActionRequest("off", { power: { configured: true, alias: "", model: "HS105(US)", meter: false, watts: null }, ladder: {} });
    assert.strictEqual(idle.summary, "switch off the plug (HS105(US)), no meter; the miner stays off, unjudged, until you press On");
    assert.throws(() => app.powerActionRequest("explode", service), /off, on or cycle/);
  },
  "hold requests: minutes and a reason, or no expiry; release has an empty body"() {
    assert.deepStrictEqual(app.holdRequest(20, "cable"), { kind: "service", method: "POST", path: "api/hold", body: { minutes: 20, reason: "cable" }, password: false,
      changes: [], restart: false, title: "Hold", event: null,
      summary: "hold for 20 min (cable): the watchdog judges nothing until the miner answers twice in a row, or 20 min pass" });
    assert.strictEqual(app.holdRequest(null, "").summary, "hold with no expiry: the watchdog judges nothing until the miner answers twice in a row, or you press Release");
    assert.deepStrictEqual(app.holdRequest(null, "").body, { minutes: null, reason: "" });
    assert.deepStrictEqual(app.holdReleaseRequest().body, {});
    assert.strictEqual(app.holdReleaseRequest().path, "api/hold/release");
    assert.strictEqual(app.holdReleaseRequest().password, false);
  },
  "interventions: holds and planned power read as yours (or the schedule's), and the service's own releases as the service's"() {
    const T = (h, m, s) => new Date(2026, 8, 12, h, m, s).getTime();
    const row = (h, m, s, ok) => ({ t: T(h, m, s), ok: ok, fan0: 1200, fan1: 1200, chip: 70, board: 63, watts: ok ? 197 : 0 });
    const rows = [row(21, 0, 30, true), row(21, 5, 30, false), row(21, 6, 0, false), row(21, 7, 0, true)];
    const log = [
      "2026-09-12 20:00:00 hold: started by you until 2026-09-12 21:00:00 (PSU swap)",
      "2026-09-12 20:12:00 hold: released, miner back after 12 min",
      "2026-09-12 20:20:00 hold: started by the schedule until 2026-09-12 20:40:00 (switched on, booting)",
      "2026-09-12 20:30:00 hold: released by you",
      "2026-09-12 20:40:00 hold: expired after 20 min with the miner still unreachable; watchdog resumed",
      "2026-09-12 21:05:00 power: switched off by you (page; 197 W before)",
      "2026-09-12 21:06:30 power: switched on by you (page)",
      "2026-09-12 21:10:00 power: cycled by you (gbox power cycle): off 15 s, on (197 W before)",
      "2026-09-12 21:11:00 power: switched off by the schedule (197 W before)",
      "2026-09-12 21:12:00 power: switch off failed: plug did not answer",
      "2026-09-12 21:13:00 service: hold picked up from the event log (no expiry): nothing judged until the miner is back",
    ].join("\n") + "\n";
    const iv = app.interventions(log, rows);
    assert.deepStrictEqual(iv.map(i => i.who), ["plug", "schedule", "you", "you", "you", "service", "you", "schedule", "service", "you"]);
    assert.deepStrictEqual(iv.map(i => i.kind), ["failed", "off", "cycle", "on", "off", "hold", "hold", "hold", "hold", "hold"]);
    const byT = Object.fromEntries(iv.map(i => [i.t, i]));
    assert.strictEqual(byT[T(20, 0, 0)].what, "hold until 21:00 (PSU swap)");
    assert.strictEqual(byT[T(20, 12, 0)].what, "hold released: miner back after 12 min");
    assert.strictEqual(byT[T(20, 30, 0)].what, "hold released");
    assert.strictEqual(byT[T(20, 40, 0)].what, "hold expired after 20 min with the miner still unreachable; watchdog resumed");
    assert.strictEqual(byT[T(21, 5, 0)].what, "switched off (page; 197 W before)");
    assert.strictEqual(byT[T(21, 5, 0)].result, "");
    assert.strictEqual(byT[T(21, 6, 30)].what, "switched on (page)");
    assert.strictEqual(byT[T(21, 6, 30)].result, "no outage seen in the log");
    assert.strictEqual(byT[T(21, 10, 0)].what, "power cycle (gbox power cycle): off 15 s, on (197 W before)");
    assert.strictEqual(byT[T(21, 11, 0)].what, "switched off (197 W before)");
    assert.strictEqual(byT[T(21, 12, 0)].what, "switch off failed: plug did not answer");
    assert.deepStrictEqual(app.interventionCounts(iv), { restarts: 0, cycles: 1, actions: 5 });
    assert.strictEqual(app.markerGlyph("hold: started by you until x"), "H");
    assert.strictEqual(app.eventMarkers("2026-09-12 20:00:00 hold: started by you until 2026-09-12 21:00:00 (PSU swap)\n").length, 1);
  },
  "interventions: the CLI's by-hand power lines arrive with the dashboard prefix and read as yours"() {
    const T = (h, m, s) => new Date(2026, 8, 12, h, m, s).getTime();
    const rows = [{ t: T(21, 5, 30), ok: false }, { t: T(21, 7, 0), ok: true }];
    const log = "2026-09-12 21:05:00 dashboard: power: switched off by hand (gbox power off; 188 W before)\n" +
                "2026-09-12 21:06:00 dashboard: power: switched on by hand (gbox power on)\n";
    const iv = app.interventions(log, rows);
    assert.deepStrictEqual(iv.map(i => [i.who, i.kind, i.what]), [["you", "on", "switched on by hand (gbox power on)"], ["you", "off", "switched off by hand (gbox power off; 188 W before)"]]);
    assert.strictEqual(iv[0].result, "no outage seen in the log");
    assert.strictEqual(iv[1].result, "");
  },
  "the service line says when the plug is off by you or cycling"() {
    const on = { configured: true, model: "HS110(US)", meter: true, state: "on", watts: 187.8, cycle: true, cycles_today: 0, last_reason: null };
    assert.strictEqual(app.powerLine({ power: Object.assign({}, on, { state: "off", watts: 0, off_by_you: true }) }), " · plug HS110(US) off by you, 0 W, armed, 0 cycles today");
    assert.strictEqual(app.powerLine({ power: Object.assign({}, on, { busy: "cycling" }) }), " · plug HS110(US) cycling, 188 W, armed, 0 cycles today");
  },
  "rated figures come from the model table, looked up loosely; unknown models get none"() {
    assert.strictEqual(app.ratedFor("Goldshell-SCBox").rated_watts, 200);
    assert.strictEqual(app.ratedFor(" goldshell scbox ").rated_mhs, 900000);
    assert.strictEqual(app.ratedFor("Goldshell-SCBoxII").name, "SC-BOX II");
    assert.strictEqual(app.ratedFor("Goldshell-KDBox"), null);
    assert.strictEqual(app.ratedFor(null), null);
    assert.strictEqual(app.pctOf(187, 200), 93.5);
    assert.strictEqual(app.pctOf(null, 200), null);
    assert.strictEqual(app.pctOf(187, null), null);
    assert.strictEqual(app.pctOf(187, 0), null);
  },
  "hashrate chart data: the buffer's leading zeros are dropped and a lone sample is reported as one point, not a line"() {
    assert.deepStrictEqual(app.chartData([]), { unit: "MH/s", div: 1, data: [] });
    assert.deepStrictEqual(app.chartData([0, 0, 0]), { unit: "MH/s", div: 1, data: [] });
    assert.deepStrictEqual(app.chartData([0, 0, 812000]), { unit: "GH/s", div: 1000, data: [812] });          // just booted: one sample
    assert.deepStrictEqual(app.chartData([0, 735000, 0, 812000]), { unit: "GH/s", div: 1000, data: [735, 0, 812] });
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log("ok   " + name); }
  catch (e) { failed++; console.log("FAIL " + name + "\n     " + String(e.message).split("\n").join("\n     ")); }
}
console.log(failed ? failed + " failed" : Object.keys(tests).length + " passed");
process.exit(failed ? 1 : 0);
