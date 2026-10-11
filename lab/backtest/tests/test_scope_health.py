"""Tournament scope restrictions (--only) and the disk health gate."""

from types import SimpleNamespace

from backtest import health
from backtest.tournament import _allowed, parse_only


def sig(family, venue="pool", orientation="reversed"):
    return SimpleNamespace(family=family, venue=venue, orientation=orientation)


def test_only_restricts_a_value_to_families_and_segments():
    only = parse_only(["size=0.25:M1,N2_M1", "d=3:F7/pool_reversed,N2_F7/pool_reversed"])
    assert _allowed(only, sig("M1", orientation="normal"), size=0.25, d=1)
    assert not _allowed(only, sig("F6"), size=0.25, d=1)
    assert _allowed(only, sig("F6"), size=0.5, d=2)  # values without a rule apply to every family
    assert _allowed(only, sig("F7"), size=0.5, d=3) and _allowed(only, sig("N2_F7"), size=1.0, d=3)
    assert not _allowed(only, sig("F7", orientation="normal"), size=0.5, d=3)
    assert not _allowed(only, sig("F7", venue="curve", orientation=None), size=0.5, d=3)
    assert parse_only(None) == {}


def test_health_gate(monkeypatch, tmp_path):
    flag = tmp_path / "io-stall"
    monkeypatch.setattr(health, "FLAGS", (str(flag),))
    monkeypatch.setattr(health, "DD_STAMP", str(tmp_path / "stamp"))
    monkeypatch.setattr(health, "io_pressure", lambda: 1.0)
    monkeypatch.setattr(health, "write_speed", lambda d: 200.0)
    assert health.problems(str(tmp_path)) == []
    assert (tmp_path / "stamp").exists()  # the write test passed: not repeated within DD_EVERY_S
    monkeypatch.setattr(health, "write_speed", lambda d: 2.0)
    assert health.problems(str(tmp_path)) == []
    (tmp_path / "stamp").unlink()
    assert health.problems(str(tmp_path)) == ["8 MB direct write 2 MB/s < 50"]
    flag.write_text("")
    monkeypatch.setattr(health, "io_pressure", lambda: 12.5)
    assert health.problems(str(tmp_path)) == [f"{flag} exists", "io pressure avg60 12.5 >= 10"]
    waits = []
    monkeypatch.setattr(health.time, "sleep", lambda s: (waits.append(s), flag.unlink()))
    monkeypatch.setattr(health, "io_pressure", lambda: 1.0)
    monkeypatch.setattr(health, "write_speed", lambda d: 200.0)
    logs = []
    health.wait_ok(logs.append, "chunk 1", directory=str(tmp_path))
    assert waits == [health.WAIT_S] and len(logs) == 1 and "before chunk 1" in logs[0]
