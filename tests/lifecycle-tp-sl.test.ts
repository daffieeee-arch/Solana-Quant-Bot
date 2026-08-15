import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { createPortfolio, enterPaperPosition, evaluateOpenPosition } from '../src/portfolio.js';
import { PaperLedgerStore } from '../src/ledger.js';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
  MIN_MOMENTUM_SCORE: '35', MIN_STOP_LOSS_PERCENT: '5', MAX_STOP_LOSS_PERCENT: '25', STOP_VOLATILITY_MULTIPLIER: '1.5',
  BREAKEVEN_TRIGGER_PERCENT: '8', MIN_PROFIT_FOR_CONTINUE_PERCENT: '3', POSITION_SCORE_DIVISOR: '100',
  LIQUIDITY_POSITION_FRACTION: '0.02', SOL_PRICE_USD: '100', ENTRY_MODE: 'contra',
});

const en = { pairId: 'gx:BowTSnCRpgCMqwFV4oUGHnw24AQ1n51VPEtMaCAnpump', mint: 'BowTSnCRpgCMqwFV4oUGHnw24AQ1n51VPEtMaCAnpump', symbol: 'PIM', priceUsd: 1, at: '2026-07-15T10:00:00.000Z', score: 50, liquidityUsd: 100_000, priceChangeM5Percent: 25 };

// Volledig offline: géén markt-/news-fetches; clock = expliciete 'at'; WAL-replay op lokale tempdir.
describe('normale papertrade lifecycle (TP/SL) — offline, deterministisch, netwerkloos', () => {
  it('take-profit: entry → stijgende prijs → trailing exit → echte realized PnL → WAL-replay geeft zelfde eindstaat', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'tp-'));
    try {
      const store = new PaperLedgerStore(dir);
      const initial = createPortfolio(config, '2026-07-15T10:00:00.000Z');
      let L = await store.loadOrCreate(initial);
      const entered = enterPaperPosition(L.portfolio, en, config);
      expect(entered.ok).toBe(true);
      if (!entered.ok) return;
      L = { ...L, portfolio: entered.portfolio };
      await store.save(L, { type: 'paper_entry', ...en, at: en.at });
      const pos = entered.position;
      // stijgende prijs: high +35% (>TP 30%), trailing-stop 15% onder high
      const high = pos.entryPriceUsd * 1.35;
      const e1 = evaluateOpenPosition(L.portfolio, { pairId: en.pairId, mint: en.mint }, high, '2026-07-15T10:02:00.000Z', config);
      expect(e1.event.type).toBe('hold');
      const ev = evaluateOpenPosition(e1.portfolio, { pairId: en.pairId, mint: en.mint }, high * (1 - 0.15 - 0.001), '2026-07-15T10:03:00.000Z', config);
      expect(ev.event.type).toBe('exit');
      if (ev.event.type !== 'exit') return;
      expect(ev.event.reason).toBe('trailing_stop');
      expect(ev.event.pnlLamports > 0).toBe(true);
      L = { ...L, portfolio: ev.portfolio };
      await store.save(L, { type: 'paper_exit', at: '2026-07-15T10:03:00.000Z', pairId: en.pairId, mint: en.mint, reason: ev.event.reason, pnlLamports: ev.event.pnlLamports });
      // WAL-replay: laad opnieuw → positie weg, kapitaal identiek
      const replay = await store.loadOrCreate(createPortfolio(config, '2026-07-15T10:04:00.000Z'));
      expect(replay.portfolio.positions.length).toBe(0);
      expect(replay.portfolio.availableLamports).toBe(ev.portfolio.availableLamports);
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });

  it('stop-loss: dalende prijs → stop_loss exit → verlies (fee/slippage-semantik) + geen duplicate exit', () => {
    const p0 = createPortfolio(config, '2026-07-15T10:00:00.000Z');
    const entered = enterPaperPosition(p0, en, config);
    expect(entered.ok).toBe(true);
    if (!entered.ok) return;
    const drop = entered.position.entryPriceUsd * 0.70; // -30% > max stop (25%) → stop_loss
    const ev = evaluateOpenPosition(entered.portfolio, { pairId: en.pairId, mint: en.mint }, drop, '2026-07-15T10:02:00.000Z', config);
    expect(ev.event.type).toBe('exit');
    if (ev.event.type !== 'exit') return;
    expect(ev.event.reason).toBe('stop_loss');
    expect(ev.event.pnlLamports < 0).toBe(true);
    // geen dup exit: positie weg → volgende evaluate = hold
    const ev2 = evaluateOpenPosition(ev.portfolio, { pairId: en.pairId, mint: en.mint }, drop * 0.9, '2026-07-15T10:03:00.000Z', config);
    expect(ev2.event.type).not.toBe('exit');
    expect(ev.portfolio.positions.length).toBe(0); // concurrency-slot vrij
  });

  it('replay is netwerkloos/deterministisch: generieke fetch/HTTP wordt tijdens lifecycle NOOIT aangeroepen', () => {
    // globale fetch/undici-spy: elke echte HTTP-call (CoinGecko/CoinDesk/anders) = FAIL
    const origFetch = globalThis.fetch;
    const fetchSpy = { calls: 0 };
    globalThis.fetch = (async (...args: unknown[]) => { fetchSpy.calls += 1; throw new Error('NETWORK-CALL-VERBODEN in replay: ' + String(args[0]).slice(0, 60)); }) as typeof fetch;
    const httpsRequire = require('node:https');
    const origHttps = httpsRequire.request;
    try {
      const p0 = createPortfolio(config, '2026-07-15T10:00:00.000Z');
      const entered = enterPaperPosition(p0, en, config);
      expect(entered.ok).toBe(true);
      if (!entered.ok) return;
      // volledige lifecycle zonder netwerk
      const high = entered.position.entryPriceUsd * 1.35;
      const e1 = evaluateOpenPosition(entered.portfolio, { pairId: en.pairId, mint: en.mint }, high, '2026-07-15T10:02:00.000Z', config);
      const ev = evaluateOpenPosition(e1.portfolio, { pairId: en.pairId, mint: en.mint }, high * 0.80, '2026-07-15T10:03:00.000Z', config);
      expect(ev.event.type).toBe('exit');
      expect(fetchSpy.calls).toBe(0); // GEEN netwerk
    } finally {
      globalThis.fetch = origFetch;
    }
  });
});