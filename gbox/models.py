"""The per-model table: rated figures for the "% of rated" axes, and the capability profile behind the model seam.

Keyed by the `model` string `/mcb/status` returns. The lookup ignores case,
spaces and hyphens, because only the SC-BOX string has been read from a real
unit; the others come from the other developer's notes (SC Lite) or are a
guess at the pattern (SC BOX II) and are marked as such in `source`.

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
  `/dbg/icinfo`, one board) or `devs` (`/mcb/cgminer?cgminercmd=devs`, the
  multi-board units; 500 on the BOX).
- `dbg_expected`: whether `/dbg/` answers without unlocking the stock UI's
  debug page. The SC Lite answers 401 "Debug access is locked" until it is.
- `fan_target`: whether the firmware exposes an adjustable fan target
  (`temp_targets` in `/mcb/setting`).
- `temp_target_basis`: `board_sensor` (the BOX: the fans hold a chosen
  board-sensor temperature) or `fixed` (the SC Lite: about 85 C, read-only).

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
    },
    "Goldshell-SCLITE": {
        "name": "SC Lite",
        "rated_mhs": 4400000.0,         # 4.4 TH/s ±5%
        "rated_watts": 950.0,           # 950 W ±5%
        "fans": None,
        "fan_max_rpm": 2200.0,          # goldshell.company/sclite "Fan Specifications: 2200rpm"
        "boards": None,                 # several (CPB0, CPB1 ...) per the other developer's notes
        "source": "goldshell.company/sclite spec table; model string, plan dialect, devs endpoint, debug lock and fixed 85 C target from Maveth/goldshell-config (fw 2.2.0)",
        "verified_string": False,
        "plan_dialect": "mv_pv",
        "board_source": "devs",
        "dbg_expected": False,
        "fan_target": False,
        "temp_target_basis": "fixed",
    },
}

# The profile for a model not in the table: the SC-BOX's sampling path, no rated figures, nothing optional.
UNKNOWN = {
    "name": None, "rated_mhs": None, "rated_watts": None, "fans": None, "fan_max_rpm": None, "boards": None,
    "source": "not in the table; the SC-BOX's sampling path with every optional capability off",
    "verified_string": False,
    "plan_dialect": "box", "board_source": "icinfo", "dbg_expected": True, "fan_target": False,
    "temp_target_basis": "board_sensor",
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
