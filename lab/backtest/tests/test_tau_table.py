"""lab/core's tolerance table (core/src/tau/table.rs, M1 C2 of the Rust live route) is the generator's output on
backtest/tau_empirical.json, and its f64 bits are the JSON's floats: the Rust `Tau` draws from exactly the quantiles
backtest.replay.Tau draws from."""

import hashlib
import importlib.util
import json
import os
import re
import struct

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TABLE = os.path.join(LAB, "core", "src", "tau", "table.rs")
JSON = os.path.join(LAB, "backtest", "tau_empirical.json")


def _generator():
    spec = importlib.util.spec_from_file_location("tau_table", os.path.join(LAB, "core", "tools", "tau_table.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rust_table_is_the_generators_output():
    with open(TABLE) as f:
        assert f.read() == _generator().generate()


def test_its_bits_are_the_jsons_quantiles():
    with open(JSON, "rb") as f:
        raw = f.read()
    t = json.loads(raw)
    with open(TABLE) as f:
        src = f.read()
    assert f'SHA256: &str = "{hashlib.sha256(raw).hexdigest()}"' in src
    edges = t["size_buckets_lamports"]
    assert f"SIZE_BUCKETS: [i128; {len(edges)}] = [{', '.join(f'{e:_}' for e in edges)}]" in src
    for name, key in (("CURVE", "table"), ("POOL", "pool_table")):
        block = src[src.index(f"pub const {name}"):]
        block = block[block.index(" = [\n"):block.index("];\n") + 3]  # the values, not the type
        cells = re.findall(r"&\[([^\]]*)\]", block)
        assert len(cells) == 2 * (len(edges) + 1)
        for i, cell in enumerate(cells):
            side, b = ("buy", "sell")[i // (len(edges) + 1)], i % (len(edges) + 1)
            got = [struct.unpack("<d", struct.pack("<Q", int(h, 16)))[0] for h in re.findall(r"0x([0-9a-f]{16})", cell)]
            assert got == (t[key].get(f"{side}:{b}") or []), (name, side, b)
