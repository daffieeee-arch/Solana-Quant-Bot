"""lab/core's position data (M1 C2-3 of the Rust live route; research/m1-c2-positionsim-spec-2026-10-19.md §3.5-3.7,
§6.1): core/src/positions/cases.rs is the generator's output on the unmodified backtest.replay, and positions.rs's
constants are replay's: the standard seed pool, the hold limit the engine leaves at its default, and the Mayhem agent
whose curve legs are refused."""

import importlib.util
import inspect
import os
import re

from backtest import replay

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSITIONS = os.path.join(LAB, "core", "src", "positions.rs")
CASES = os.path.join(LAB, "core", "src", "positions", "cases.rs")


def _generator():
    spec = importlib.util.spec_from_file_location("position_cases", os.path.join(LAB, "core", "tools", "position_cases.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_generated_data_is_the_generators_output():
    with open(CASES) as f:
        assert f.read() == _generator().generate()


def test_the_constants_are_replays():
    with open(POSITIONS) as f:
        src = f.read()
    hold = int(re.search(r"pub const MAX_HOLD: u64 = ([\d_]+);", src).group(1).replace("_", ""))
    for fn in (replay.open_pool_position, replay.open_curve_position):
        assert inspect.signature(fn).parameters["max_slots"].default == hold == 400_000
    body = re.search(r"pub const SEED_POOL: Pool = Pool \{(.*?)\};", src, re.S).group(1)
    got = {k: int(v.replace("_", "")) for k, v in re.findall(r"(\w+): ([\d_]+)", body)}
    p = replay.SEED_POOL
    assert got == {"b": p.b, "e": p.e, "lp_bps": p.lp_bps, "protocol_bps": p.protocol_bps, "creator_bps": p.creator_bps,
                   "cashback_bps": p.cashback_bps, "virt": p.virt}
    gen = _generator()
    assert gen.IDS[replay.MAYHEM_AGENT] == gen.MAYHEM_ID  # the cases' agent id is the one Cfg gets
