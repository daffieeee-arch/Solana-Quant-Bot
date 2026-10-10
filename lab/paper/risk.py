"""Risk configuration (lab/paper/risk.yaml): written by the owner, only read by the engine.

The engine re-reads it every minute of event time. An invalid file means: open nothing, and say
why in the journal.
"""

from dataclasses import dataclass

import yaml

FAMILIES = ("F7",)


@dataclass(frozen=True)
class Risk:
    size_sol: float
    max_open_positions: int
    max_exposure_sol: float
    daily_loss_limit_sol: float
    kill_switch: bool
    families: tuple

    @property
    def size_lamports(self):
        return int(round(self.size_sol * 1e9))


def load(path):
    """(Risk, None) or (None, reason)."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        return None, f"risk config unreadable: {e.__class__.__name__}"
    try:
        r = Risk(float(raw["size_sol"]), int(raw["max_open_positions"]), float(raw["max_exposure_sol"]),
                 float(raw["daily_loss_limit_sol"]), bool(raw["kill_switch"]), tuple(raw["families"]))
    except (KeyError, TypeError, ValueError) as e:
        return None, f"risk config invalid: {e.__class__.__name__} {e}"
    if not (0 < r.size_sol <= r.max_exposure_sol) or r.max_open_positions < 1 or r.daily_loss_limit_sol <= 0:
        return None, "risk config invalid: sizes and limits must be positive and size <= max exposure"
    if not set(r.families) <= set(FAMILIES):
        return None, f"risk config invalid: unknown families {sorted(set(r.families) - set(FAMILIES))}"
    if not isinstance(raw["kill_switch"], bool):
        return None, "risk config invalid: kill_switch must be true or false"
    return r, None
