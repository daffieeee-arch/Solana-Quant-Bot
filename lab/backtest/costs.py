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
    net = scn.exit_net(result.proceeds)
    return net - cost, net / cost - 1, cost
