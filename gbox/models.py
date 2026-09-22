"""The per-model table: rated figures for the "% of rated" axes, and the capability profile behind the model seam.

Keyed by the `model` string `/mcb/status` returns. The lookup ignores case,
spaces and hyphens, because only the SC-BOX, SC Lite and SC5 Pro II strings
have been read from a real unit; the others are a guess at the pattern (SC BOX
II, SC5 Pro) and are marked as such in `source`.

Percentages are never logged: they are a rendering of a logged number against
one of these constants, so a corrected constant re-renders history. The JS
data layer carries the same table (`MODELS` in gbox/web/app.js); a test keeps
the two identical.

The capability fields (0.7.0, docs/plan.md "The model seam") say what the
sampler, the plan parser and the page may assume about a unit:

- `plan_dialect`: how the firmware writes the power plan string
  (docs/firmware-api.md, "Power plan dialects"). Parsing is driven by the
  string itself; this field says what to expect and what to write.
- `board_source`: where per-board data comes from, `icinfo` (the BOX's
  `/dbg/icinfo`, one board) or `http_devs` (`/mcb/cgminer?cgminercmd=devs`,
  the HTTP wrapper around cgminer's `devs` command; 500 on the BOX).
- `dbg_expected`: whether `/dbg/` answers without unlocking the stock UI's
  debug page. MaVeTh's early SC Lite notes reported a 401 "Debug
  access is locked"; his 2026-09-22 capture answered 200, so no model on record
  is locked.
- `fan_target`: whether the firmware exposes an adjustable fan target
  (`temp_targets` in `/mcb/setting`).
- `temp_target_basis`: `board_sensor` (the BOX: the fans hold a chosen
  board-sensor temperature) or `fixed` (the SC Lite and the SC5 Pro/Pro II:
  a fixed target, read-only).
- `plan_names`: `{level: name}` for the stock UI's names of the power-plan
  levels on this model (the SC5 Pro II: "Hashrate Mode", "Low-power Mode",
  "Idle Mode"), or None when no such names have been read from a unit. The
  page shows `plan_names[level]` when present, else the plan string itself.
- `absent_signature` (0.9.0): whether "clock 0 and the board sensor at its
  no-sensor value, while HTTP answers" has been verified on this model to mean
  the controller has lost its hashboard. True only for the SC-BOX (six
  episodes, 2026-09-13 to 09-20, none of which ended without a restart). Where
  it is False the watchdog's board-absent rule never fires; a model earns it
  from a log, not from resemblance.

An unknown model gets the BOX's sampling path with every optional
capability off and `known: False`, and the page says so.
"""

PLAN_DIALECTS = ("box", "mv_pv", "float_pv")

MODELS = {
    "Goldshell-SCBox": {
        "name": "SC-BOX",
        "rated_mhs": 900000.0,          # 900 GH/s ±5%
        "rated_watts": 200.0,           # 200 W ±5%
        "fans": 2,
        "fan_max_rpm": 4900.0,          # observed: 4,440 RPM at 90% duty on 2026-09-09; not on the spec sheet
        "boards": 1,
        "source": "Goldshell spec via retailer listings (900 GH/s, 200 W); fan max observed on one unit",
        "verified_string": True,
        "plan_dialect": "box",
        "board_source": "icinfo",
        "dbg_expected": True,
        "fan_target": True,
        "temp_target_basis": "board_sensor",
        "plan_names": None,
        "absent_signature": True,
    },
    "Goldshell-SCBox II": {
        "name": "SC-BOX II",
        "rated_mhs": 1900000.0,         # 1.9 TH/s; low-power mode 1.45 TH/s
        "rated_watts": 400.0,           # 400 W; low-power mode 260 W
        "fans": 2,
        "fan_max_rpm": None,
        "boards": 1,
        "source": "retailer listings (kryptex, d-central, miningnow); model string not read from a unit; capabilities assumed as the SC-BOX's",
        "verified_string": False,
        "plan_dialect": "box",
        "board_source": "icinfo",
        "dbg_expected": True,
        "fan_target": True,
        "temp_target_basis": "board_sensor",
        "plan_names": None,
        "absent_signature": False,      # assumed like the SC-BOX in every other way; this one is not assumed
    },
    "Goldshell-SCLITE": {
        "name": "SC Lite",
        "rated_mhs": 4400000.0,         # 4.4 TH/s ±5%
        "rated_watts": 950.0,           # 950 W ±5%
        "fans": 4,                      # fan0..fan3 on every board, captured 2026-09-22
        "fan_max_rpm": 2200.0,          # goldshell.company/sclite "Fan Specifications: 2200rpm"
        "boards": 4,                    # four [PGAn] blocks on 4028 devs and /dbg/minerinfo; icinfo 4 x 46 chips
        "source": "goldshell.company/sclite spec table; model string, plan dialect, 4 PGA boards, 4 fans, open /dbg/ and no fan-target range from MaVeTh's unit (Maveth/goldshell-config, MCB_V4_3, fw 2.2.0, hw 30.40.SA, captured 2026-09-22)",
        "verified_string": True,
        "plan_dialect": "mv_pv",
        "board_source": "icinfo",       # like the SC5 Pro II: the service reads 4028 first, the page /dbg/minerinfo
        "dbg_expected": True,           # /dbg/minerinfo answered 200 with the token; the "debug lock" did not appear
        "fan_target": False,
        "temp_target_basis": "fixed",
        "plan_names": None,
        "absent_signature": False,
    },
    "Goldshell-SC5ProⅡ": {            # exact bytes from /mcb/status on a friend's unit (Unicode Ⅱ, U+2161)
        "name": "SC5 Pro II", "rated_mhs": 14000000.0, "rated_watts": 3300.0, "fans": 4, "fan_max_rpm": None, "boards": 4,
        "source": "Goldshell spec sheet 2026-09-15 (14 TH/s ±5%, 3300 W ±5%; low-power 10 TH/s at 2050 W); model string, "
                  "plan dialect, PGA blocks, 4028 devs and plan names from a friend's unit (MCB_V3_3, fw 2.2.0, hw 30.50.SA)",
        "verified_string": True, "plan_dialect": "mv_pv", "board_source": "icinfo", "dbg_expected": True,
        "fan_target": False, "temp_target_basis": "fixed",
        "plan_names": {0: "Hashrate Mode", 2: "Low-power Mode", 3: "Idle Mode"}, "absent_signature": False,
    },
    "Goldshell-SC5Pro": {              # string not read from a unit
        "name": "SC5 Pro", "rated_mhs": 11000000.0, "rated_watts": 2820.0, "fans": None, "fan_max_rpm": None, "boards": None,
        "source": "Goldshell spec sheet 2026-09-15 (11 TH/s ±5%, 2820 W ±5%; low-power 8.8 TH/s at 2020 W); capabilities "
                  "assumed as the SC5 Pro II's", "verified_string": False, "plan_dialect": "mv_pv", "board_source": "icinfo",
        "dbg_expected": True, "fan_target": False, "temp_target_basis": "fixed", "plan_names": None,
        "absent_signature": False,
    },
}

# The profile for a model not in the table: the SC-BOX's sampling path, no rated figures, nothing optional.
UNKNOWN = {
    "name": None, "rated_mhs": None, "rated_watts": None, "fans": None, "fan_max_rpm": None, "boards": None,
    "source": "not in the table; the SC-BOX's sampling path with every optional capability off",
    "verified_string": False,
    "plan_dialect": "box", "board_source": "icinfo", "dbg_expected": True, "fan_target": False,
    "temp_target_basis": "board_sensor", "plan_names": None, "absent_signature": False,
}


def _key(model):
    text = model if isinstance(model, str) else ("" if model is None else str(model))   # the miner's JSON is untrusted
    return "".join(ch for ch in text.casefold() if ch not in " -_")


_BY_KEY = {_key(k): v for k, v in MODELS.items()}


def rated_for(model):
    """The table row for a model string, or None when the model is not in the table."""
    return _BY_KEY.get(_key(model))


def profile_for(model):
    """The capability profile for a model string: the table row plus `known: True` and `model` (the string as
    the firmware gave it), or the UNKNOWN profile named after the string with `known: False`. Always a fresh
    dict with the same keys either way, JSON-safe, so /api/health can carry it."""
    text = model if isinstance(model, str) and model else None
    row = _BY_KEY.get(_key(model))
    if row is not None:
        p = dict(row)
        p.update(known=True, model=text)
    else:
        p = dict(UNKNOWN)
        p.update(known=False, model=text, name=text)
    return p


def pct_of(value, rated):
    """`value` as a percentage of `rated`, or None when either is missing or rated is not positive."""
    if value is None or not rated or rated <= 0:
        return None
    return 100.0 * value / rated
