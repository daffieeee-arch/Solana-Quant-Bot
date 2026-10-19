"""lab/core's exit table and its generated test data (M1 C2-2 of the Rust live route; research/m1-c2-positionsim-spec-
2026-10-19.md §1.6, Pinning): core/src/exits.rs's VARIANTS is exactly what paper.engine builds for the three M1 specs,
bit for bit and with the engine's hard-coded stop-loss; the paper start's and the references' groups name exactly these
variants; and core/src/exits/cases.rs and core/src/flow/cases.rs are the generator's output on the unmodified engine."""

import importlib.util
import os
import re
import struct

from paper import engine as E

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXITS = os.path.join(LAB, "core", "src", "exits.rs")
CASES = {w: os.path.join(LAB, "core", "src", w, "cases.rs") for w in ("exits", "flow")}
M1 = {"0.04:600": "tp4_T600", "flow300_H6": "flow300_H6", "flow300_tp100_H6": "flow300_tp100_H6"}


def _generator():
    spec = importlib.util.spec_from_file_location("exit_cases", os.path.join(LAB, "core", "tools", "exit_cases.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bits(x):
    return None if x is None else struct.unpack("<Q", struct.pack("<d", float(x)))[0]


def _rust_table():
    """VARIANTS' rows as written in exits.rs: (name, spec, kind, tp, sl, flow_s, (cap kind, cap))."""
    with open(EXITS) as f:
        src = f.read()
    block = src[src.index("pub static VARIANTS: [Variant; 3] = ["):]
    block = block[:block.index("\n];\n")]
    rows = []
    for v in re.findall(r"Variant \{(.*?)\}", block, re.S):
        field = lambda k, pat: re.search(rf"\b{k}: {pat}", v).groups()  # noqa: E731
        opt = lambda k, conv: (lambda m: None if m[0] == "None" else conv(m[1]))(field(k, r"(None|Some\(([^)]*)\))"))  # noqa: E731
        cap_kind, cap = field("cap", r"Cap::(\w+)\(([\d_]+)\)")
        rows.append((field("name", r'"([^"]*)"')[0], field("spec", r'"([^"]*)"')[0], field("kind", r"Kind::(\w+)")[0],
                     opt("tp", float), opt("sl", float), opt("flow_s", lambda s: int(s.replace("_", ""))),
                     (cap_kind, int(cap.replace("_", "")))))
    return rows


def test_the_rust_table_is_what_the_engine_builds():
    """Per M1 spec: paper.engine.variant's name and params, and the class, tp and sl of the rule Engine._enter builds
    (the tp:T rule's -0.08 is engine.py:890's literal), equal the Rust row, every f64 by its bits."""
    gen = _generator()
    rows = _rust_table()
    assert [r[0] for r in rows] == list(M1.values()) and [r[1] for r in rows] == list(M1)
    for (name, spec, kind, tp, sl, flow_s, cap), (spec_py, name_py) in zip(rows, M1.items()):
        g_name, g_spec, g_kind, g_tp, g_sl, g_flow_s, g_cap, _ = gen.table_row(spec_py)
        assert (name, spec, kind, flow_s, cap) == (g_name, g_spec, g_kind, g_flow_s, g_cap) and name == name_py
        assert (_bits(tp), _bits(sl)) == (_bits(g_tp), _bits(g_sl)), name
    # The params themselves, as the spec lists them (§1.6).
    assert E.variant("0.04:600") == ("tp4_T600", (0.04, 600))
    assert E.variant("flow300_H6") == ("flow300_H6", ("flow", 300, None, 21_600_000))
    assert E.variant("flow300_tp100_H6") == ("flow300_tp100_H6", ("flowtp", (300, 1.0), None, 21_600_000))
    assert [_bits(rows[0][3]), _bits(rows[0][4]), _bits(rows[2][3])] == [0x3FA47AE147AE147B, 0xBFB47AE147AE147B, 0x3FF0000000000000]
    # tp:T names are lossy (engine.py:88), which is why nas_m1c2.sh pins the literal spec.
    assert E.variant("0.045:600")[0] == "tp4_T600"


def test_the_paper_start_and_the_references_name_exactly_these_variants():
    """k3-paper-2026-10-12.yaml trades ["0.04:600"] in [pool_reversed]; h-arms-2026-10-16.yaml's group H trades
    flow300_H6 and flow300_tp100_H6 in exactly [pool_reversed], the only segments M1 runs a flow name in."""
    here = os.path.join(LAB, "paper")
    _, k3 = E.load_config(os.path.join(here, "k3-paper-2026-10-12.yaml"))
    _, h = E.load_groups(os.path.join(here, "h-arms-2026-10-16.yaml"))
    assert k3["variants"] == ["0.04:600"] and k3["segments"] == ["pool_reversed"]
    assert list(h["groups"]) == ["H"]
    assert h["groups"]["H"]["variants"] == ["flow300_H6", "flow300_tp100_H6"] and h["groups"]["H"]["segments"] == ["pool_reversed"]
    names = [E.variant(v)[0] for v in k3["variants"]] + [n for g in E.config_groups(h) for n, _ in g.variants]
    assert names == list(M1.values())


def test_the_generated_data_is_the_generators_output():
    gen = _generator()
    for which, path in CASES.items():
        with open(path) as f:
            assert f.read() == gen.generate(which), which


def test_a_tp_t_name_drops_digits_so_only_the_exact_spec_is_m1s():
    """The reason lab-live paper checks the literal --variants spec (core exits.rs variant_for_spec): the name of a tp:T
    spec keeps int(tp * 100) only, so 0.045:600 is named tp4_T600 with other parameters."""
    from paper.engine import variant
    assert variant("0.04:600") == ("tp4_T600", (0.04, 600)) == variant("0.040:600")
    name, params = variant("0.045:600")
    assert name == "tp4_T600" and params != (0.04, 600)
    assert variant("0.0400000000000001:600")[1] != (0.04, 600)
