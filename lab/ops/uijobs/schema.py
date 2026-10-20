"""UI research jobs: the one schema that the web app, the VPS dispatcher, the NAS's forced command and the NAS
runner all check (interactive-UI spec v0.3 §5, §5.1). A job is parameters only, never code: the NAS renders it
from a template pinned there (SHA256SUMS). Stdlib only, so every one of them can load this file by its path.

A job (at most MAX_BYTES of JSON):
  {"id": <ULID>, "template": "backtest-f7", "template_sha256": <hex>, "params": {...}}

Hold-out (epochs 1047-1051) and the clean test period (1055-1063) are never selectable: no store holds them here
and validate() refuses any period that touches them, a second time on the NAS before a store is opened.
"""

import json
import re

MAX_BYTES = 4096
ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")  # a ULID (Crockford base32): time-ordered, no free text
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EPOCH_SLOTS = 432_000
HOLDOUT = range(1047, 1052)
CLEAN = range(1055, 1064)
# The stores a UI job may use (file under the NAS's store directory) and the epochs each holds.
STORES = {"dev2": ("dev2.duckdb", 1033, 1046), "fwd1052": ("fwd1052.duckdb", 1052, 1052),
          "fwd1053-1054": ("fwd1053-1054.duckdb", 1053, 1054)}
MAX_EPOCHS = 7  # about 14 days per job
# F7's variants per tournament --f7-grid (backtest.strategies at the pin; tests/test_uijobs.py checks they agree).
GRIDS = {
    "v1": ["tp4_T150", "tp4_T600", "tp4_T2400", "tp6_T150", "tp6_T600", "tp6_T2400", "tp10_T150", "tp10_T600", "tp10_T2400"],
    "exits": ["trail3_T600", "trail3_T600_sl8", "trail5_T600", "trail5_T600_sl8", "trail10_T600", "trail10_T600_sl8",
              "hold60", "hold60_sl8", "hold180", "hold180_sl8", "hold300", "hold300_sl8",
              "flow10_T600", "flow10_T600_sl8", "flow30_T600", "flow30_T600_sl8", "flow60_T600", "flow60_T600_sl8"],
    "long": ["trail20_H6", "trail30_H6", "trail50_H6", "trail30_H1", "flow300_H6", "flow300_H6_sl8"],
    "big": ["tp50_H6", "tp100_H6", "flow300_tp100_H6"],
}
GRID_OF = {v: g for g, vs in GRIDS.items() for v in vs}
K3 = "tp4_T600"  # the paper run's main candidate: every job's comparison on the same period and settings
SEGMENTS = ("pool_reversed", "pool_normal", "curve")
FARMS = ("all", "organic")
SIZES = (0.5, 2.0, 5.0, 10.0, 25.0)
DELAYS_MS = (300, 600, 900)
TEMPLATES = {"backtest-f7": ("variant", "segment", "farm", "size_sol", "d_ms", "store", "epoch_from", "epoch_to")}


def _int(v, name):
    if type(v) is not int:  # no bool, no float, no string
        raise ValueError(f"{name}: an integer")
    return v


def validate(raw):
    """The job, normalised, or ValueError naming the first problem. raw: bytes, str or a dict already parsed."""
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_BYTES:
            raise ValueError(f"job larger than {MAX_BYTES} bytes")
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_BYTES:
            raise ValueError(f"job larger than {MAX_BYTES} bytes")
        try:
            raw = json.loads(raw)
        except RecursionError:
            raise ValueError("job nested too deeply") from None
    if not isinstance(raw, dict) or set(raw) != {"id", "template", "template_sha256", "params"}:
        raise ValueError("job: exactly id, template, template_sha256, params")
    jid, tpl, sha, p = raw["id"], raw["template"], raw["template_sha256"], raw["params"]
    if not (isinstance(jid, str) and ID_RE.match(jid)):
        raise ValueError("id: a ULID")
    if tpl not in TEMPLATES:
        raise ValueError(f"template: one of {sorted(TEMPLATES)}")
    if not (isinstance(sha, str) and SHA_RE.match(sha)):
        raise ValueError("template_sha256: 64 hex digits")
    if not isinstance(p, dict) or set(p) != set(TEMPLATES[tpl]):
        raise ValueError(f"params: exactly {', '.join(TEMPLATES[tpl])}")
    if p["variant"] not in GRID_OF:
        raise ValueError("variant: an F7 variant of the engine's grids")
    for name, allowed in (("segment", SEGMENTS), ("farm", FARMS), ("store", tuple(STORES))):
        if p[name] not in allowed:
            raise ValueError(f"{name}: one of {', '.join(allowed)}")
    if type(p["size_sol"]) not in (int, float) or float(p["size_sol"]) not in SIZES:
        raise ValueError(f"size_sol: one of {SIZES}")
    if _int(p["d_ms"], "d_ms") not in DELAYS_MS:
        raise ValueError(f"d_ms: one of {DELAYS_MS}")
    lo, hi = _int(p["epoch_from"], "epoch_from"), _int(p["epoch_to"], "epoch_to")
    _, s_lo, s_hi = STORES[p["store"]]
    if not s_lo <= lo <= hi <= s_hi:
        raise ValueError(f"epochs: within the store's {s_lo}-{s_hi}, from <= to")
    if any(e in HOLDOUT or e in CLEAN for e in range(lo, hi + 1)):
        raise ValueError("epochs: the hold-out (1047-1051) and the clean test (1055-1063) are not selectable")
    if hi - lo + 1 > MAX_EPOCHS:
        raise ValueError(f"epochs: at most {MAX_EPOCHS}")
    params = dict(p, size_sol=float(p["size_sol"]))
    return {"id": jid, "template": tpl, "template_sha256": sha, "params": params}


def slots(params):
    """The job's decision window [lo, hi) in slots: its whole epochs."""
    return params["epoch_from"] * EPOCH_SLOTS, (params["epoch_to"] + 1) * EPOCH_SLOTS


def env(job):
    """The template's environment: UI_* values only, each from the validated job (never shell text)."""
    p = job["params"]
    lo, hi = slots(p)
    return {"UI_ID": job["id"], "UI_VARIANT": p["variant"], "UI_GRID": GRID_OF[p["variant"]], "UI_K3": K3,
            "UI_K3_GRID": GRID_OF[K3], "UI_SEGMENT": p["segment"], "UI_FARM": p["farm"], "UI_SIZE": f"{p['size_sol']:g}",
            "UI_DMS": str(p["d_ms"]), "UI_STORE_FILE": STORES[p["store"]][0], "UI_FROM_SLOT": str(lo), "UI_TO_SLOT": str(hi)}
