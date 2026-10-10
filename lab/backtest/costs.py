"""Network, rent and execution costs per scenario (week2-defaults.yaml `costs`)."""

from dataclasses import dataclass

LAMPORTS = 1_000_000_000


@dataclass(frozen=True)
class Scenario:
    name: str
    fee_per_tx: int
    tip: int
    failed_tx: int
    land_prob: float
    ata_rent: int
    rent_mode: str  # refunded | refunded_on_full_exit | lost
    adverse_per_side: float

    @classmethod
    def from_cfg(cls, name, cfg):
        s = cfg["costs"]["scenarios"][name]
        sol = lambda x: int(round(float(x) * LAMPORTS))  # noqa: E731
        return cls(name, sol(s["fee_per_tx_sol"]), sol(s["tip_sol"]), sol(s["failed_tx_sol"]), float(s["land_prob"]),
                   int(cfg["costs"]["ata_rent_lamports"]), s["ata_rent"], float(s["adverse_exec_per_side"]))

    def per_tx(self):
        """Expected network cost of one order: landed fee + tip, plus failed attempts before it lands."""
        retries = 1 / self.land_prob - 1
        return self.fee_per_tx + self.tip + int(retries * self.failed_tx)

    def entry_total(self, venue_cost):
        rent = self.ata_rent
        return int(venue_cost * (1 + self.adverse_per_side)) + self.per_tx() + rent

    def exit_net(self, proceeds, full_exit=True):
        refund = self.ata_rent if (self.rent_mode == "refunded" or
                                   (self.rent_mode == "refunded_on_full_exit" and full_exit)) else 0
        return int(proceeds * (1 - self.adverse_per_side)) - self.per_tx() + refund


def settle(result, scn):
    """(pnl_lamports, return, total_cost) for a replay Result under a scenario."""
    if result.entry_failed:
        loss = scn.failed_tx  # the entry transaction landed and failed on its slippage limit
        return -loss, None, loss
    cost = scn.entry_total(result.cost)
    # Exit attempts that landed and failed on our own slippage limit pay the failed-tx cost.
    net = scn.exit_net(result.proceeds) - max(0, result.exit_attempts - 1) * scn.failed_tx
    return net - cost, net / cost - 1, cost


def settle_sql(scn):
    """(pnl_lamports, return) SQL expressions over results columns: the twin of settle().

    Skipped positions (no transaction) give NULL for both; a failed entry has no return."""
    a = float(scn.adverse_per_side)
    refund = scn.ata_rent if scn.rent_mode in ("refunded", "refunded_on_full_exit") else 0
    cost = f"(CAST(trunc(cost::DOUBLE * {1 + a!r}::DOUBLE) AS BIGINT) + {scn.per_tx() + scn.ata_rent})"
    net = (f"(CAST(trunc(proceeds::DOUBLE * {1 - a!r}::DOUBLE) AS BIGINT) - {scn.per_tx()} + {refund}"
           f" - GREATEST(0, COALESCE(exit_attempts, 1) - 1) * {scn.failed_tx})")
    pnl = f"CASE WHEN skipped THEN NULL WHEN entry_failed THEN {-scn.failed_tx} ELSE {net} - {cost} END"
    ret = f"CASE WHEN skipped OR entry_failed THEN NULL ELSE {net}::DOUBLE / {cost} - 1 END"
    return pnl, ret
