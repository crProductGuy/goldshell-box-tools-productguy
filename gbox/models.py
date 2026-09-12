"""Rated figures per Goldshell model, for the "% of rated" axes and captions.

Keyed by the `model` string `/mcb/status` returns. The lookup ignores case,
spaces and hyphens, because only the SC-BOX string has been read from a real
unit; the others come from the other developer's notes (SC Lite) or are a
guess at the pattern (SC BOX II) and are marked as such in `source`.

Percentages are never logged: they are a rendering of a logged number against
one of these constants, so a corrected constant re-renders history. The JS
data layer carries the same table (`MODELS` in gbox/web/app.js); a test keeps
the two identical.
"""

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
    },
    "Goldshell-SCBox II": {
        "name": "SC-BOX II",
        "rated_mhs": 1900000.0,         # 1.9 TH/s; low-power mode 1.45 TH/s
        "rated_watts": 400.0,           # 400 W; low-power mode 260 W
        "fans": 2,
        "fan_max_rpm": None,
        "boards": 1,
        "source": "retailer listings (kryptex, d-central, miningnow); model string not read from a unit",
        "verified_string": False,
    },
    "Goldshell-SCLITE": {
        "name": "SC Lite",
        "rated_mhs": 4400000.0,         # 4.4 TH/s ±5%
        "rated_watts": 950.0,           # 950 W ±5%
        "fans": None,
        "fan_max_rpm": 2200.0,          # goldshell.company/sclite "Fan Specifications: 2200rpm"
        "boards": None,                 # several (CPB0, CPB1 ...) per the other developer's notes
        "source": "goldshell.company/sclite spec table; model string from Maveth/goldshell-config (fw 2.2.0)",
        "verified_string": False,
    },
}


def _key(model):
    text = model if isinstance(model, str) else ("" if model is None else str(model))   # the miner's JSON is untrusted
    return "".join(ch for ch in text.casefold() if ch not in " -_")


_BY_KEY = {_key(k): v for k, v in MODELS.items()}


def rated_for(model):
    """The rated figures for a model string, or None when the model is not in the table."""
    return _BY_KEY.get(_key(model))


def pct_of(value, rated):
    """`value` as a percentage of `rated`, or None when either is missing or rated is not positive."""
    if value is None or not rated or rated <= 0:
        return None
    return 100.0 * value / rated
