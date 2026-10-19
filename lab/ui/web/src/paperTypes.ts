// Shapes of /api/paper (lab/ui/paper.py).
export interface PaperStatus {
  running: boolean; last_heartbeat_unix: number | null; last_slot: number | null; open_positions: number | null;
  gaps_today: number; largest_gap_slots: number | null; last_gap: string | null;
  config?: string | null; config_sha256?: string | null; risk_sha256_at_start?: string | null;
}
export interface PaperGroup {
  trades: number; entry_failed: number; net_sol: number; net_sol_per_trade: number | null; win_rate: number | null; open: number;
}
export interface PaperRange {
  trades: number; entry_failed: number; net_sol: number; net_sol_per_trade: number | null; win_rate: number | null;
  ret_per_trade: number | null; vs_n2_pt: number | null; curve: number[];
}
export interface PaperPosition {
  family: string; variant: string; key: string; venue: string | null; orientation: string | null; farm?: string | null; entry_slot: number;
  cost_sol: number | null; status: string; pnl_sol: number | null; ret?: number | null; mark_sol?: number | null;
  failed?: boolean; slots?: number | null; entry_time?: number | null; exit_time?: number | null; mint?: string | null; symbol?: string | null; name?: string | null;
}
export interface PaperJournal {
  journal: string; test_run: boolean; day: string;
  // Where the journal comes from: a live feed is paper trading; a replay (the parity run) is a test that decides nothing.
  source: { feed: string; paper_trading: boolean; journal: string }; is_today: boolean; status: PaperStatus; totals: PaperGroup;
  variants: (PaperGroup & { family: string; variant: string })[]; positions: PaperPosition[]; shadow_positions: PaperPosition[];
  ranges: Record<"1d" | "7d" | "all", PaperRange>;
  ticker: { key: string; ret: number | null; failed: boolean; symbol?: string | null; mint?: string | null }[];
  // The daily report's stop rule (lab/paper/stoprule.py): the primary group only, filled trades, from the clock start.
  stop_rule: {
    trades: number; entry_failed: number; days: number; min_trades: number; min_days_ci: number; mean_sol_per_trade: number | null;
    ci_lo: number | null; ci_hi: number | null; verdict: PaperVerdict; primary: string[] | null; clock: PaperClock | null;
    clock_days: number; clock_max_days: number; assessment: PaperAssessment;
  };
  capital: { start_sol: number; max_positions: number; final_sol: number; max_drawdown: number; taken: number; skipped: number; curve: number[] };
  n2: { family: string; variant: string; has_shadow: boolean; own_sol_per_trade: number | null; n2_sol_per_trade: number | null; diff_all: number | null; diff_today: number | null; n2_trades: number }[];
  arms: { group: string; variants: (PaperGroup & { variant: string; today_sol: number })[] }[];
  holdout_rows_dropped: number;
}
// stoprule.decide: a verdict from MIN_TRADES filled trades and MIN_DAYS_CI trading days (owner, 2026-10-16).
export type PaperVerdict = "GO" | "ADJUST" | "STOP" | "too few trades" | "too few days";
// stoprule.clock_state: 'due' once there is a verdict or at the limit (CLOCK_MAX_DAYS, `limit`).
export interface PaperClock {
  date: string; reason: string; set_at: string; phase: "not_started" | "running" | "due"; clock_day: number | null; limit: boolean;
}
// stoprule.assessment (amounts also in SOL, lab/ui/paper.py _assessment_sol): what a verdict rests on and what stays
// uncertain. Items carry a key (worded in Dutch on the page) and the report's English text as a fallback.
export interface PaperAssessmentItem {
  key: string; text: string; land_prob?: number | null; d_ms?: number | null; q?: number | null; after_switch?: number;
  trades?: number; days?: number; feed?: string;
}
export interface PaperAssessment {
  verdict: PaperVerdict; applies: boolean; feed: string;
  basis: {
    filled_trades: number; failed_entries: number; trading_days: number; clock_day: number | null; mean_sol: number | null;
    ci95_sol: [number, number] | null; draws: number; seed: number; costs_sol: Record<string, number> | null;
  };
  assumptions: PaperAssessmentItem[]; uncertainty: PaperAssessmentItem[]; not_permission: string;
}
export interface PaperRecorder {
  sample: boolean; day: string; is_today: boolean; streams: { stream: string; gb: number; messages: number }[];
  spent_gb: number | null; cap_gb: number; usd_per_gb: number; budget_usd: number; stopped: boolean; budget_day: string | null;
  unknown?: string | null; // why the recorder stopped the day: today's spend could not be known (meter.rs, exit 75)
  level: number | null; dropped: string[]; last_segment_unix: number | null; segments_today: number; reconnects: number;
  notes: { unix: number; note: string }[];
}
export interface PaperData { journal: PaperJournal | null; recorder: PaperRecorder | null; now_unix: number }
