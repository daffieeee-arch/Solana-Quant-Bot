"""paper.live_reference: M3-11's Python reference is paper.engine.main itself with LiveFeed wrapped (spec v0.3 §1)."""

import json

import pytest

from paper import engine, live, live_reference

ARGV = ["--live", "/rec", "--catch-up", "--no-watchlist", "--no-backup", "--journal", "/j"]


class FakeFeed:
    """Stands in for LiveFeed: keeps what it was given, has LiveFeed's stats and farm_seed."""

    def __init__(self, root, store=None, follow=True, **kw):
        self.args = dict(root=root, store=store, follow=follow, **kw)
        self.stats = {"segments": 3, "tokens_dropped": 1}
        self.farm_seed = {"store": "/abs/store.duckdb", "lo": 1, "hi": 2, "h24": 5, "first_live_slot": 2, "complete_from": 7}


@pytest.fixture
def fake(monkeypatch):
    """main builds its feed as engine.py:1461 does (LiveFeed imported at call time from paper.live)."""
    calls = []

    def main(argv):
        calls.append(argv)
        from paper.live import LiveFeed

        LiveFeed(argv[1], store="/store.duckdb", follow=False, seed_farmers=True, learned=[], from_seq=0)

    monkeypatch.setattr(live, "LiveFeed", FakeFeed)
    monkeypatch.setattr(engine, "main", main)
    return calls


def test_the_feed_gets_the_snapshot_before_and_h24_and_main_its_own_arguments(fake, tmp_path):
    feed = live_reference.run(454_896_000, 395_804, ARGV)
    assert fake == [ARGV]
    assert feed.args == {"root": "/rec", "store": "/store.duckdb", "follow": False, "seed_farmers": True, "learned": [], "from_seq": 0,
                         "snapshot_before": 454_896_000, "h24": 395_804}
    assert live.LiveFeed is FakeFeed, "the class is restored after the call"
    # without --h24 the feed measures it (production's path): no h24 given
    assert "h24" not in live_reference.run(454_896_000, None, ARGV).args
    out = tmp_path / "s.json"
    live_reference.main(["--snapshot-before", "9", "--summary", str(out), "--", *ARGV])
    s = json.loads(out.read_text())
    assert s == {"snapshot_before": 9, "h24": None, "stats": {"segments": 3, "tokens_dropped": 1},
                 "farm_seed": {"lo": 1, "hi": 2, "h24": 5, "first_live_slot": 2, "complete_from": 7}}


def test_a_reference_without_the_live_catch_up_arguments_is_refused(fake):
    for drop in ("--live", "--catch-up", "--no-watchlist", "--no-backup"):
        argv = [a for a in ARGV if a != drop]
        with pytest.raises(SystemExit, match=f"lack {drop}"):
            live_reference.run(1, None, argv)
    with pytest.raises(SystemExit, match="after --"):
        live_reference.main(["--snapshot-before", "9", *ARGV])
    with pytest.raises(SystemExit):
        live_reference.main(["--snapshot-before", "9", "--h24", "0", "--", *ARGV])
    assert fake == []
