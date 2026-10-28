//! The costs of a paper position (backtest/costs.py): the network cost per transaction, the entry's total and the exit's
//! net in lamports, and `settle`, a position's P&L and return. M1 C2; the porting spec is
//! research/m1-c2-positionsim-spec-2026-10-19.md §3.9.
//!
//! - Landing is not simulated: the expected retries (1 / land_prob - 1 failed attempts) are part of `per_tx`, and a
//!   transaction that lands and fails on its own slippage limit costs `failed_tx` (a failed entry, each failed exit).
//! - Every step is Python's: `int(x * f)` is `mul_trunc`, and the return `net / cost - 1` divides two ints exactly
//!   (`true_div`), which a plain f64 division does not.
//! - The scenario comes from the reference journal's state 'cost_scenario' (lab/live parses it); its integers are already
//!   lamports (Scenario.from_cfg).

use crate::exact::{mul_trunc, true_div};

/// What happens to the token account's rent at the exit (Scenario.rent_mode).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RentMode {
    Refunded,
    RefundedOnFullExit,
    Lost,
}

impl RentMode {
    pub fn parse(s: &str) -> Option<RentMode> {
        match s {
            "refunded" => Some(RentMode::Refunded),
            "refunded_on_full_exit" => Some(RentMode::RefundedOnFullExit),
            "lost" => Some(RentMode::Lost),
            _ => None,
        }
    }
}

/// A cost scenario (costs.py `Scenario`), lamports as integers.
#[derive(Clone, Debug, PartialEq)]
pub struct Scenario {
    pub name: String,
    pub fee_per_tx: i128,
    pub tip: i128,
    pub failed_tx: i128,
    pub land_prob: f64,
    pub ata_rent: i128,
    pub rent_mode: RentMode,
    pub adverse_per_side: f64,
}

/// A position's settlement (costs.py `settle`): P&L in lamports, the return (None for a failed entry) and the total
/// cost it is measured against.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Settled {
    pub pnl: i128,
    pub ret: Option<f64>,
    pub total_cost: i128,
}

impl Scenario {
    /// The expected network cost of one order: the landed fee and tip, plus the failed attempts before it lands.
    pub fn per_tx(&self) -> i128 {
        let retries = 1.0 / self.land_prob - 1.0;
        self.fee_per_tx + self.tip + mul_trunc(self.failed_tx, retries)
    }

    /// An entry's total cost: the venue cost with the adverse execution, the network cost and the account's rent.
    pub fn entry_total(&self, venue_cost: i128) -> i128 {
        mul_trunc(venue_cost, 1.0 + self.adverse_per_side) + self.per_tx() + self.ata_rent
    }

    /// An exit's net: the proceeds after the adverse execution and the network cost, with the rent back where the
    /// scenario refunds it (the engine always exits fully).
    ///
    /// **The model's exit closes the token account** (Codex review of c29c545, decided 2026-10-19): every exit ends with a
    /// Burn of any remainder and a CloseAccount, in the exit transaction, so the rent comes back and the remainder is
    /// worth 0. A remainder is mostly fee dust: a reversed pool's sale (`venues::rev_sell_token`) leaves 1-3 raw units
    /// in about sum(bps)/10,000 of the exits, flagged `vault_capped`; a real vault cap is rare. On-chain a non-native
    /// account closes only at a zero token amount and its owner may burn the rest first (SPL Token
    /// interface/src/instruction.rs `CloseAccount`, `Burn`: "Non-native accounts may only be closed if its token amount
    /// is zero"); a Token-2022 account with withheld transfer fees also needs the permissionless
    /// `HarvestWithheldTokensToMint` first (token-2022 interface/src/instruction.rs `CloseAccount`; the Token-2022
    /// extension guide). So `settle` passes `full_exit = true` for every exit, `vault_capped` or not.
    pub fn exit_net(&self, proceeds: i128, full_exit: bool) -> i128 {
        let refund = match self.rent_mode {
            RentMode::Refunded => self.ata_rent,
            RentMode::RefundedOnFullExit if full_exit => self.ata_rent,
            _ => 0,
        };
        mul_trunc(proceeds, 1.0 - self.adverse_per_side) - self.per_tx() + refund
    }

    /// costs.py `settle`: a failed entry costs one failed transaction and has no return; else the exit's net (each
    /// exit attempt after the first a failed transaction) against the entry's total. Every exit is a full exit: the
    /// remainder, if any, is burned and the account closed (`exit_net`).
    pub fn settle(&self, entry_failed: bool, cost: i128, proceeds: i128, exit_attempts: i128) -> Settled {
        if entry_failed {
            return Settled { pnl: -self.failed_tx, ret: None, total_cost: self.failed_tx };
        }
        let total = self.entry_total(cost);
        let net = self.exit_net(proceeds, true) - (exit_attempts - 1).max(0) * self.failed_tx;
        Settled { pnl: net - total, ret: Some(true_div(net, total) - 1.0), total_cost: total }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// week2-defaults.yaml:86-89 through Scenario.from_cfg (lamports).
    pub(crate) fn scenario(name: &str) -> Scenario {
        let (fee, tip, failed, land, rent, adverse) = match name {
            "optimistic" => (11_000, 0, 11_000, 0.99, RentMode::Refunded, 0.0),
            "base" => (605_000, 100_000, 505_000, 0.97, RentMode::RefundedOnFullExit, 0.0),
            "pessimistic" => (3_005_000, 1_000_000, 2_005_000, 0.93, RentMode::Lost, 0.005),
            _ => unreachable!(),
        };
        Scenario { name: name.into(), fee_per_tx: fee, tip, failed_tx: failed, land_prob: land, ata_rent: 1_513_840, rent_mode: rent,
                   adverse_per_side: adverse }
    }

    #[test]
    fn the_scenarios_cost_what_python_says() {
        // lab-py costs.py on the real week2-defaults.yaml: per_tx, entry_total(5e8), exit_net(0), exit_net(510000123)
        // and exit_net(510000123, full_exit=False).
        for (name, want) in [("optimistic", (11_111, 501_524_951, 1_502_729, 511_502_852, 511_502_852)),
                             ("base", (720_618, 502_234_458, 793_222, 510_793_345, 509_279_505)),
                             ("pessimistic", (4_155_913, 508_169_752, -4_155_913, 503_294_209, 503_294_209))] {
            let s = scenario(name);
            let got = (s.per_tx(), s.entry_total(500_000_000), s.exit_net(0, true), s.exit_net(510_000_123, true), s.exit_net(510_000_123, false));
            assert_eq!(got, want, "{name}");
        }
        assert_eq!(["refunded", "refunded_on_full_exit", "lost", "x"].map(RentMode::parse),
                   [Some(RentMode::Refunded), Some(RentMode::RefundedOnFullExit), Some(RentMode::Lost), None]);
    }

    /// (scenario, entry_failed, cost, proceeds, exit_attempts, pnl, the ret's bits, total cost)
    type SettleCase = (&'static str, bool, i128, i128, i128, i128, Option<u64>, i128);

    #[test]
    fn settle_is_pythons_to_the_bit() {
        // lab-py settle(Result(entry_failed, cost, proceeds, exit_attempts), scenario) -> (pnl, ret bits, total cost)
        let cases: [SettleCase; 12] = [
            ("optimistic", false, 500_000_000, 612_345_678, 1, 112_323_456, Some(0x3fccaad8e5369dd8), 501_524_951),
            ("optimistic", false, 499_999_999, 401_000_001, 3, -99_044_220, Some(0xbfc94739b4920d10), 501_524_950),
            ("optimistic", true, 0, 0, 0, -11_000, None, 11_000),
            ("optimistic", false, 500_000_000, 0, 2, -500_033_222, Some(0xbfefe7a242742f7d), 501_524_951),
            ("base", false, 500_000_000, 612_345_678, 1, 110_904_442, Some(0x3fcc43e59af18e30), 502_234_458),
            ("base", false, 499_999_999, 401_000_001, 3, -101_451_234, Some(0xbfc9db20b4cb8ff0), 502_234_457),
            ("base", true, 0, 0, 0, -505_000, None, 505_000),
            ("base", false, 500_000_000, 0, 2, -501_946_236, Some(0xbfeffb4c7cda6bf4), 502_234_458),
            ("pessimistic", false, 500_000_000, 612_345_678, 1, 96_958_284, Some(0x3fc86c1a134b9f10), 508_169_752),
            ("pessimistic", false, 499_999_999, 401_000_001, 3, -117_340_664, Some(0xbfcd8e680ca0fb1c), 508_169_751),
            ("pessimistic", true, 0, 0, 0, -2_005_000, None, 2_005_000),
            ("pessimistic", false, 500_000_000, 0, 2, -514_330_665, Some(0xbff031a8a70f9e31), 508_169_752),
        ];
        for (name, failed, cost, proceeds, attempts, pnl, ret, total) in cases {
            let got = scenario(name).settle(failed, cost, proceeds, attempts);
            assert_eq!((got.pnl, got.ret.map(f64::to_bits), got.total_cost), (pnl, ret, total), "{name} {cost} {proceeds} {attempts}");
        }
    }

    #[test]
    fn a_vault_capped_exit_with_dust_gets_the_rent_back() {
        // Codex on c29c545, decided 2026-10-19: the exit burns the remainder and closes the account (exit_net's doc), so a
        // vault_capped exit settles as any other. The dust case: a reversed pool (80 SOL, 3e14 tokens, fees 25+5 bps)
        // sells 1,849,999,999,800 of 1,849,999,999,801 held tokens (1 raw unit of fee dust) for 488,852,188 lamports.
        // Exact-in rule 2: initial net 1,844,466,600,000 has integer fees 4,611,166,500 + 922,233,300;
        // its debit is one less than held. The old held=1,850,000,000,203 now spends everything.
        // backtest/tests/test_settle_sql.py pins the same fee-dust case.
        use crate::venues::{rev_sell_token, Pool};
        let p = Pool { b: 80_000_000_000, e: 300_000_000_000_000, lp_bps: 25, protocol_bps: 5, creator_bps: 0, cashback_bps: 0, virt: 0 };
        let held = 1_849_999_999_801;
        let f = rev_sell_token(&p, held).unwrap();
        assert_eq!((held - f.tokens, f.trader_quote), (1, 488_852_188)); // vault_capped: f.tokens < held
        let base = scenario("base");
        let s = base.settle(false, 500_000_000, f.trader_quote, 1);
        assert_eq!((s.pnl, s.ret.map(f64::to_bits), s.total_cost), (-12_589_048, Some(0xbf99aaec01a04340), 502_234_458));
        // the rent is in it: without the close the net would be 1,513,840 lower
        assert_eq!(base.exit_net(f.trader_quote, true) - base.exit_net(f.trader_quote, false), base.ata_rent);
        assert_eq!(s.pnl, base.exit_net(f.trader_quote, true) - base.entry_total(500_000_000));
    }
}
