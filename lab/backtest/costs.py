"""Network, rent and execution costs per scenario (week2-defaults.yaml `costs`).

The transactions a position pays for (Codex review #6, LAB review 24 Oct; settle and settle_sql):
- skipped: none (pnl NULL);
- a failed entry: one landed buy that failed on its limit: failed_tx (the ATA creation reverts with it: no rent);
- a filled entry: fee, tip and the expected dropped resends (per_tx), the ATA rent, the cost after adverse execution;
- every exit attempt that landed and failed pays failed_tx: a sale below its limit, and an attempt into a pool that can
  pay nothing (sell() None) too. The model assumes no preflight: a bot that simulates first would not land that one;
- exactly one final transaction at per_tx: the filling sale (its proceeds; a vault_capped remainder burned and the
  account closed in it), or, when no attempt filled (an unsold position at the data's end, a paper write-off), a burn
  and close of its own (proceeds 0, or the end-of-data valuation the replay records for a position whose last attempt
  missed its limit while the pool could still pay: a modelled sale without a limit);
- the rent back as the scenario says (exit_net).
So the failed exit fees are exit_attempts - filled, filled = exit_filled (the last counted attempt filled). Rule 1,
before 24 Oct, charged exit_attempts - 1 always: one failed fee short on every row whose last attempt failed. Frozen
results (E63/E65/E70, the clean tests, the hold-out) were settled under rule 1; a comparison with them passes rule=1.
"""

from dataclasses import dataclass

LAMPORTS = 1_000_000_000
SETTLE_RULE = 2  # every landed failed exit attempt pays (Codex review #6); 1: exit_attempts - 1 (before 24 Oct)
# New paper exits carry their own rule; replayed journal exits retain their stored P&L and historical rule.
ENGINE_SETTLE_RULE = 2


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
        """The exit's net: proceeds after adverse execution and the network cost, plus the rent where refunded.

        The model's exit closes the token account (Codex review of c29c545, decided 2026-10-19): it burns any remainder
        and closes the account in the exit transaction, so the rent comes back and the remainder is worth 0. A remainder
        is mostly fee dust (venues.rev_sell_token leaves 1-3 raw units in about sum(bps)/10,000 of reversed-pool exits,
        flagged vault_capped). On-chain a non-native account closes only at a zero token amount, and the owner may burn
        the rest first (SPL Token interface/src/instruction.rs CloseAccount, Burn); Token-2022 withheld transfer fees
        need the permissionless HarvestWithheldTokensToMint first (token-2022 interface/src/instruction.rs). So settle
        and settle_sql treat every exit as full, vault_capped or not; full_exit=False is for tests only."""
        refund = self.ata_rent if (self.rent_mode == "refunded" or
                                   (self.rent_mode == "refunded_on_full_exit" and full_exit)) else 0
        return int(proceeds * (1 - self.adverse_per_side)) - self.per_tx() + refund


def exit_filled(r):
    """The position's last counted exit attempt filled: it has an exit slot (a sale, a graduation's sale) and is no paper
    write-off. An unsold position (exit_slot None) and a write-off ended with a failed attempt. A graduation counts as
    filled: on the dev runs every graduation sold something (graduations with an exit slot and nothing sold: 0 of 92,342
    in families-v2 and of 194,942 in v1.1-dev, counted 24 Oct for LAB review's Q2)."""
    return r.exit_slot is not None and r.exit_reason != "written_off"


# exit_filled over results or journal columns (exit_slot, exit_reason).
FILLED_SQL = "(exit_slot IS NOT NULL AND exit_reason IS DISTINCT FROM 'written_off')"


def failed_exits_sql(rule=SETTLE_RULE):
    """SQL: the failed exit attempts that pay failed_tx (settle's rule)."""
    if rule == 1:
        return "GREATEST(0, COALESCE(exit_attempts, 1) - 1)"
    return f"GREATEST(0, COALESCE(exit_attempts, 1) - CASE WHEN {FILLED_SQL} THEN 1 ELSE 0 END)"


def settle(result, scn, rule=SETTLE_RULE):
    """(pnl_lamports, return, total_cost) for a replay Result under a scenario (the transaction set in the module's
    docstring; rule 1: the failed exit fees before 24 Oct). Every exit is full (exit_net): a vault_capped remainder is
    burned and the account closed, so its rent comes back too."""
    if result.entry_failed:
        loss = scn.failed_tx  # the entry transaction landed and failed on its slippage limit
        return -loss, None, loss
    cost = scn.entry_total(result.cost)
    filled = 1 if rule == 1 else int(exit_filled(result))
    net = scn.exit_net(result.proceeds) - max(0, result.exit_attempts - filled) * scn.failed_tx
    return net - cost, net / cost - 1, cost


def settle_sql(scn, rule=SETTLE_RULE):
    """(pnl_lamports, return) SQL expressions over results columns: the twin of settle() (rule 2 reads exit_slot and
    exit_reason).

    Skipped positions (no transaction) give NULL for both; a failed entry has no return. Every exit is full, as in
    settle: a vault_capped row gets the rent back too (exit_net's doc)."""
    a = float(scn.adverse_per_side)
    refund = scn.ata_rent if scn.rent_mode in ("refunded", "refunded_on_full_exit") else 0
    cost = f"(CAST(trunc(cost::DOUBLE * {1 + a!r}::DOUBLE) AS BIGINT) + {scn.per_tx() + scn.ata_rent})"
    net = (f"(CAST(trunc(proceeds::DOUBLE * {1 - a!r}::DOUBLE) AS BIGINT) - {scn.per_tx()} + {refund}"
           f" - {failed_exits_sql(rule)} * {scn.failed_tx})")
    pnl = f"CASE WHEN skipped THEN NULL WHEN entry_failed THEN {-scn.failed_tx} ELSE {net} - {cost} END"
    ret = f"CASE WHEN skipped OR entry_failed THEN NULL ELSE {net}::DOUBLE / {cost} - 1 END"
    return pnl, ret
