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
  "event markers are parsed from the event log, service lines excluded"() {
    const log = "2026-09-06 10:00:00 service: started v0.1.0\n2026-09-06 10:05:30 dashboard: clock set to 625 MHz\n" +
      "2026-09-06 11:10:00 watchdog: restart #1 sent (miner unreachable for 2 min)\nnot a log line\n";
    const marks = app.eventMarkers(log);
    assert.strictEqual(marks.length, 2);
    assert.strictEqual(marks[0].t, new Date(2026, 8, 6, 10, 5, 30).getTime());
    assert.strictEqual(marks[0].label, "dashboard: clock set to 625 MHz");
    assert.match(marks[1].label, /^watchdog: restart #1/);
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
      hw_pct: 0.48, hw_approx: true, accepted_per_hour: 600, mhs: 755000, chip_temp: 70.4, fan_rpm: 2294, overheat: 1 };
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
    assert.strictEqual(text[10], "70.4 °C · 2294 RPM · 1 overheat");
    assert.strictEqual(cells[6].cls, "critical");            // resets above zero
    assert.strictEqual(cells[4].cls, "critical");            // bad share above 5 percent
  },
  "trial row cells: clean segment, unknown fan target, live marker"() {
    const row = { clock: 575, fan_target: null, start: "2026-09-08 10:26:00", end: "2026-09-08 21:03:00", minutes: 637.2, last: true,
      worst_chip: null, bad_pct: null, bad_partial: false, bad_per_hour: 0, resets: 0, hw_pct: 0.31, hw_approx: false,
      accepted_per_hour: 595.4, mhs: 735000, chip_temp: 70.4, fan_rpm: 1389, overheat: 0 };
    const text = app.trialCells(row).map(c => c.text);
    assert.strictEqual(text[1], "?");
    assert.strictEqual(text[3], "10h 37m · live");
    assert.strictEqual(text[4], "none flagged");
    assert.strictEqual(text[7], "0.31%");
    assert.strictEqual(text[8], "595");
    assert.strictEqual(text[10], "70.4 °C · 1389 RPM");
    assert.strictEqual(app.trialCells(row)[6].cls, "");
    const mild = app.trialCells(Object.assign({}, row, { worst_chip: 8, bad_pct: 0.13, bad_per_hour: 0.8 }));
    assert.strictEqual(mild[4].text, "chip 8 0.1%");
    assert.strictEqual(mild[4].cls, "");
    assert.strictEqual(app.trialCells(Object.assign({}, row, { worst_chip: 8, bad_pct: 2.5 }))[4].cls, "serious");
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log("ok   " + name); }
  catch (e) { failed++; console.log("FAIL " + name + "\n     " + String(e.message).split("\n").join("\n     ")); }
}
console.log(failed ? failed + " failed" : Object.keys(tests).length + " passed");
process.exit(failed ? 1 : 0);
