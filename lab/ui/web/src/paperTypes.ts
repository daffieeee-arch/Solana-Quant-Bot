// Shapes of /api/paper (lab/ui/paper.py).
export interface PaperStatus {
  running: boolean; last_heartbeat_unix: number | null; last_slot: number | null; open_positions: number | null;
  gaps_today: number; largest_gap_slots: number | null; last_gap: string | null;
}
export interface PaperGroup {
  trades: number; entry_failed: number; net_sol: number; net_sol_per_trade: number | null; win_rate: number | null; open: number;
}
export interface PaperRange {
  trades: number; entry_failed: number; net_sol: number; net_sol_per_trade: number | null; win_rate: number | null;
  ret_per_trade: number | null; vs_n2_pt: number | null; curve: number[];
}
export interface PaperPosition {
  family: string; variant: string; key: string; venue: string | null; orientation: string | null; entry_slot: number;
  cost_sol: number | null; status: string; pnl_sol: number | null; ret?: number | null; mark_sol?: number | null;
  failed?: boolean; slots?: number | null;
}
export interface PaperJournal {
  journal: string; test_run: boolean; day: string; is_today: boolean; status: PaperStatus; totals: PaperGroup;
  variants: (PaperGroup & { family: string; variant: string })[]; positions: PaperPosition[];
  ranges: Record<"1d" | "7d" | "all", PaperRange>;
  ticker: { key: string; ret: number | null; failed: boolean }[];
  stop_rule: { trades: number; days: number; min_trades: number; min_days_ci: number; mean_sol_per_trade: number | null; ci_lo: number | null; ci_hi: number | null; verdict: string };
  capital: { start_sol: number; max_positions: number; final_sol: number; max_drawdown: number; taken: number; skipped: number; curve: number[] };
  n2: { family: string; variant: string; has_shadow: boolean; own_sol_per_trade: number | null; n2_sol_per_trade: number | null; diff_all: number | null; diff_today: number | null; n2_trades: number }[];
  holdout_rows_dropped: number;
}
export interface PaperRecorder {
  sample: boolean; day: string; is_today: boolean; streams: { stream: string; gb: number; messages: number }[];
  spent_gb: number | null; cap_gb: number; usd_per_gb: number; budget_usd: number; stopped: boolean; budget_day: string | null;
  level: number | null; dropped: string[]; last_segment_unix: number | null; segments_today: number; reconnects: number;
  notes: { unix: number; note: string }[];
}
export interface PaperData { journal: PaperJournal | null; recorder: PaperRecorder | null; now_unix: number }
