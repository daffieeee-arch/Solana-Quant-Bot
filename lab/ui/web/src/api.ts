// Thin client for lab/ui/server.py. All responses are JSON; strings from the chain (token names,
// symbols) are rendered as React text only, never as HTML.

export type Row = Record<string, unknown>;

export interface Meta {
  meta: { slot_start: number; slot_end_exclusive: number; built_at_unix: number; counts: Record<string, number> };
  tabs: string[];
}

export interface TokenRow {
  mint: string;
  name: string | null;
  symbol: string | null;
  create_time: string;
  sol_quote: boolean;
  mayhem: boolean;
  cashback: boolean;
  holder_reward: boolean;
  graduated: boolean;
  grad_in_create_slot: boolean;
  curve_trades: number;
  pool_trades: number;
  total_volume_quote: number;
  curve_max_mcap: number | null;
  pool_max_mcap: number | null;
}

export interface Candle {
  venue: "curve" | "pool";
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  trades: number;
  volume_quote: number;
  buy_quote: number;
}

export interface MarketEvent {
  slot: number;
  tx_index: number | null;
  time: number | null;
  kind: "graduation" | "large_buy" | "large_sell" | "protocol_marker";
  mint: string | null;
  symbol: string | null;
  name: string | null;
  venue: string | null;
  side: string | null;
  sol: number | null;
  wallet: string | null;
  note: string | null;
}

export interface TapeRow {
  venue: "curve" | "pool";
  slot: number;
  tx_index: number;
  time: number;
  side: "buy" | "sell";
  quote: number;
  tokens: number;
  price: number;
  trader: string;
}

export interface TokenDetail {
  token: TokenRow & Row & { supply_tokens: number; create_slot: number; complete_slot: number | null; creator: string; quote_mint: string; pool: string | null };
  candles: Candle[];
  events: MarketEvent[];
  tape: TapeRow[];
}

export async function get<T>(path: string, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
  const res = await fetch(`/api/${path}${qs.size ? "?" + qs : ""}`, { signal });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error ?? `Fout ${res.status}`);
  return body as T;
}
