//! A variant group's risk limits (paper/risk.py `Risk`), M1 C3 (research/m1-c3-risk-spec-2026-10-19.md §2). lab-live
//! reads the owner's risk files (lab/live/src/risk.rs, risk.py `load`); lab-core only holds the values.

/// The families a risk file may enable (risk.py `FAMILIES`).
pub const FAMILIES: [&str; 1] = ["F7"];

/// risk.py's `Risk`, field for field: floats where Python has floats, the families in the file's order (duplicates
/// kept, as `tuple()` keeps them).
#[derive(Clone, Debug, PartialEq)]
pub struct Risk {
    pub size_sol: f64,
    pub max_open_positions: i64,
    pub max_exposure_sol: f64,
    pub daily_loss_limit_sol: f64,
    pub kill_switch: bool,
    pub families: Vec<String>,
    pub pool_cooldown_h: f64,
}

impl Risk {
    /// `int(round(self.size_sol * 1e9))`: the same float product, and Python's round() of a float goes half to even.
    pub fn size_lamports(&self) -> i128 {
        (self.size_sol * 1e9).round_ties_even() as i128
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn risk(size_sol: f64) -> Risk {
        Risk { size_sol, max_open_positions: 5, max_exposure_sol: 2.5, daily_loss_limit_sol: 1.0, kill_switch: false,
               families: vec!["F7".into()], pool_cooldown_h: 24.0 }
    }

    #[test]
    fn the_size_rounds_half_to_even_as_python() {
        // size_sol * 1e9 is exactly 0.5, 1.5, 2.5 and 3.5 for these (risk.py: int(round(...)) gives 0, 2, 2, 4).
        let got: Vec<i128> = [0.0000000005, 0.0000000015, 0.0000000025, 0.0000000035].iter().map(|&s| risk(s).size_lamports()).collect();
        assert_eq!(got, [0, 2, 2, 4]);
        assert_eq!(risk(0.5).size_lamports(), 500_000_000);
    }
}
