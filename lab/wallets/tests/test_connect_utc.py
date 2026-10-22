"""The shared DuckDB connections pin TimeZone to UTC: to_timestamp() renders in the session zone, which is the host zone by
default (Europe/Amsterdam on the VPS, UTC on the NAS), so day and hour labels differed between the two machines (E58 lesson,
2026-10-22). The test presets every new connection to CEST and checks that the lab's connect functions override it."""
import duckdb
import pytest

from backtest import data as bd
from wallets import common

T = 1789945200  # 2026-09-20 23:00 UTC = 2026-09-21 01:00 CEST
LABEL = "SELECT current_setting('TimeZone'), strftime(to_timestamp(%d), '%%Y-%%m-%%d %%H')" % T


@pytest.fixture
def cest_host(monkeypatch):
    real = duckdb.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.execute("SET TimeZone = 'Europe/Amsterdam'")
        return con
    monkeypatch.setattr(duckdb, "connect", connect)
    assert duckdb.connect().execute(LABEL).fetchone() == ("Europe/Amsterdam", "2026-09-21 01")


def test_backtest_data_connect_is_utc(cest_host):
    assert bd.connect(threads=1, memory="100MB").execute(LABEL).fetchone() == ("UTC", "2026-09-20 23")


def test_wallets_common_connect_is_utc(cest_host, tmp_path, monkeypatch):
    store = str(tmp_path / "s.duckdb")
    duckdb.connect(store).close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    assert common.connect(store, memory="100MB", threads=1).execute(LABEL).fetchone() == ("UTC", "2026-09-20 23")
