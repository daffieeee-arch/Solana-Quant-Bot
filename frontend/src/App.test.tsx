// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

const styles = readFileSync('frontend/src/styles.css', 'utf8');

const dashboardData = {
  mode: 'paper' as const,
  updatedAt: '2026-07-25T22:36:38.982Z',
  summary: { availableSol: 10.0822, realizedPnlSol: 0.0822, openPositions: 1, totalEquitySol: 10.3322, winRate: 66.7 },
  scanner: { strategy: 'quant-momentum' as const, status: 'online' as const, lastScanAt: '2026-07-25T22:36:38.982Z', scans: 14, candidatesFound: 2, duplicateSuppressed: 12, meaningfulEvents: 4, consecutiveLosses: 0, whaleInterestMints: [] },
  positions: [{
    mint: 'Mint111111111111111111111111111111111111111', symbol: 'ALPHA', openedAt: '2026-07-25T22:00:00.000Z',
    entryPriceUsd: 1, markPriceUsd: 1.12, highPriceUsd: 1.4, allocatedSol: 0.25, entryCostSol: 0.255, unrealizedPnlSol: 0.03, unrealizedPnlPercent: 11.8,
  }],
  equity: [{ at: '2026-07-25T22:00:00.000Z', equitySol: 10 }, { at: '2026-07-25T22:36:38.982Z', equitySol: 10.3322 }],
  feed: [{ id: 'entry-alpha', pairId: 'pair-alpha', at: '2026-07-25T22:00:00.000Z', type: 'paper_entry', symbol: 'ALPHA', detail: 'score 72.4' }],
  candidates: [{ pairId: 'pair-alpha', mint: 'Mint1111111111111111111111111111111111111', symbol: 'ALPHA', score: 72.4, source: 'triton_vixen_pumpfun', at: '2026-07-25T22:00:00.000Z' }],
  rejections: [{
    pairId: 'pair-beta', mint: 'Mint222222222222222222222222222222222222222', symbol: 'BETA',
    reason: 'daily_loss_limit', rejectionClass: 'risk' as const, score: 81.8, occurrences: 3,
    source: 'solana_rpc_ws_pumpswap', at: '2026-07-25T22:00:00.000Z',
    firstEvaluatedAt: '2026-07-25T21:59:58.000Z', lastEvaluatedAt: '2026-07-25T22:00:00.000Z',
    pairCreatedAt: '2026-07-25T21:42:43.000Z', firstSeenAt: '2026-07-25T22:00:00.000Z',
    observedAt: '2026-07-25T21:59:59.500Z', evaluatedAt: '2026-07-25T22:00:00.000Z', detectionDelayMs: 1_037_000,
  }],
  closedTrades: [{ pairId: 'pair-win', mint: 'Mint3333333333333333333333333333333333333', symbol: 'WIN', reason: 'trailing_stop', source: 'triton_vixen_pumpfun', at: '2026-07-25T22:30:00.000Z', pnlSol: 0.0822, heldMinutes: 90 }],
  marketContext: { updatedAt: '2026-07-25T22:00:00.000Z', ticker: [{ symbol: 'BTC', name: 'Bitcoin', priceUsd: 64_000, change24hPercent: 1.25 }, { symbol: 'SOL', name: 'Solana', priceUsd: 150, change24hPercent: -2.5 }], news: [{ source: 'CoinDesk', title: 'Bitcoin sees renewed interest', url: 'https://www.coindesk.com/example', publishedAt: '2026-07-25T22:00:00.000Z' }] },
};

describe('App', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it('polls the dashboard endpoint and renders the paper trading monitor', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    expect(await screen.findByText('PAPER // MONITOR')).toBeInTheDocument();
    expect(screen.getByText('EQUITY / P&L')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1H' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '24H' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '7D' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '30D' })).toBeInTheDocument();
    expect(screen.getByText('SCANNER ACTIVITY')).toBeInTheDocument();
    expect(screen.getByText('CANDIDATES')).toBeInTheDocument();
    expect(screen.getByText('REJECTION ANALYTICS')).toBeInTheDocument();
    expect(screen.getByText('CLOSED TRADES')).toBeInTheDocument();
    expect(screen.getByText('QUANT MOMENTUM · 4 RETAINED SIGNALS · 12 DUPLICATES FILTERED')).toBeInTheDocument();
    expect(screen.queryByText(/NL TIME/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Current Netherlands time')).toHaveTextContent(/^\d{1,2} [a-z]{3}, \d{2}:\d{2}:\d{2}$/);
    expect(screen.getByText('MARKET TICKER')).toBeInTheDocument();
    expect(screen.getByText('BTC')).toBeInTheDocument();
    expect(screen.getByText('Bitcoin sees renewed interest')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Bitcoin sees renewed interest' })).toHaveAttribute('href', 'https://www.coindesk.com/example');
    expect(screen.getByText('WIN')).toBeInTheDocument();
    expect(screen.getByText('HELD 1h 30m')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'ALPHA' })[0]).toHaveAttribute('href', 'https://dexscreener.com/solana/pair-alpha');
    expect(screen.getByRole('link', { name: 'BETA' })).toHaveAttribute('href', 'https://dexscreener.com/solana/pair-beta');
    expect(screen.getByText(/RISK BLOCK · daily loss limit/i)).toBeInTheDocument();
    expect(screen.getByText(/DISCOVERY DELAY 17m 17s/i)).toBeInTheDocument();
    expect(screen.getByText(/PAIR CREATED/i)).toBeInTheDocument();
    expect(screen.getByText(/FIRST SEEN/i)).toBeInTheDocument();
    expect(screen.getByText(/QUOTE/i)).toBeInTheDocument();
    expect(screen.getByText(/EVALUATED/i)).toBeInTheDocument();
    expect(screen.getByText(/×3 RETAINED/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'WIN' })).toHaveAttribute('href', 'https://dexscreener.com/solana/pair-win');
    expect(screen.getByRole('button', { name: /REFRESH/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /REFRESH/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('LIVE POLL · 5S')).not.toBeInTheDocument();
    expect(screen.getByText('TOTAL EQUITY · USD')).toBeInTheDocument();
    expect(screen.getByText('UNREALIZED P&L · USD')).toBeInTheDocument();
    expect(screen.getByText('ENTRY USD / MARK USD')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ALL RETAINED' })).toBeInTheDocument();
    expect(screen.getByText(/2 retained observations/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'USD' })).toHaveClass('active');
    expect(screen.queryByRole('button', { name: /light mode/i })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/dashboard-data', { headers: { accept: 'application/json' } });
  });

  it('exposes currency and chart-range selection to assistive technology', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    await screen.findByText('PAPER // MONITOR');

    expect(screen.getByRole('group', { name: 'Portfolio currency' })).toBeInTheDocument();
    const usd = screen.getByRole('button', { name: 'USD' });
    const sol = screen.getByRole('button', { name: 'SOL' });
    expect(usd).toHaveAttribute('aria-pressed', 'true');
    expect(sol).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(sol);
    expect(sol).toHaveAttribute('aria-pressed', 'true');
    expect(usd).toHaveAttribute('aria-pressed', 'false');

    expect(screen.getByRole('group', { name: 'Equity chart range' })).toBeInTheDocument();
    const all = screen.getByRole('button', { name: 'ALL RETAINED' });
    const oneHour = screen.getByRole('button', { name: '1H' });
    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(oneHour).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(oneHour);
    expect(oneHour).toHaveAttribute('aria-pressed', 'true');
    expect(all).toHaveAttribute('aria-pressed', 'false');
  });

  it('keeps the last valid dashboard visible without querying a mutation control plane', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true, json: async () => dashboardData }));
    vi.stubGlobal('fetch', fetchMock);
    render(<App pollMs={60_000} />);

    await screen.findByText('PAPER // MONITOR');
    fireEvent.click(screen.getByRole('button', { name: /REFRESH/ }));

    expect(screen.getByText('TOTAL EQUITY · USD')).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !String(input).startsWith('/api/control'))).toBe(true);
    expect(screen.queryByRole('heading', { name: 'ENGINE CONTROL' })).not.toBeInTheDocument();
  });

  it('does not render an inert asset-detail panel when no positions are open', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...dashboardData, summary: { ...dashboardData.summary, openPositions: 0 }, positions: [] }) }));
    render(<App pollMs={60_000} />);

    await screen.findByText(/No paper positions open/);
    expect(screen.getByRole('heading', { name: 'OPEN POSITIONS' }).closest('section')).toHaveClass('is-empty');
    expect(document.querySelector('.position-head')).not.toBeInTheDocument();
    expect(screen.queryByText('Select an asset to inspect')).not.toBeInTheDocument();
    expect(screen.queryByText('SELECTED ASSET')).not.toBeInTheDocument();
  });

  it('keeps populated open positions fully expanded and visible', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    const panel = (await screen.findByRole('heading', { name: 'OPEN POSITIONS' })).closest('section');
    expect(panel).not.toHaveClass('is-empty');
    expect(document.querySelector('.position-head')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Inspect ALPHA paper position' })).toBeVisible();
  });

  it('summarizes retained rejection reasons without claiming all-time coverage', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    expect(await screen.findByRole('heading', { name: 'REJECTION ANALYTICS' })).toBeInTheDocument();
    expect(screen.getByText('3 RETAINED REJECTION EVENTS')).toBeInTheDocument();
    expect(screen.getByText('DAILY LOSS LIMIT')).toBeInTheDocument();
    expect(screen.getByText('100.0%')).toBeInTheDocument();
    expect(screen.getByText('14 RETAINED SCANS')).toBeInTheDocument();
    expect(screen.getByText('2 QUALIFIED · RETAINED')).toBeInTheDocument();
  });

  it('renders a compact equity empty state when no observations are retained', async () => {
    const emptyData = { ...dashboardData, equity: [], positions: [], candidates: [], rejections: [], closedTrades: [], summary: { ...dashboardData.summary, openPositions: 0 } };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => emptyData }));
    render(<App pollMs={60_000} />);

    expect(await screen.findByText('No retained equity observations')).toBeInTheDocument();
    expect(screen.getByText('0 RETAINED OBSERVATIONS · ALL RETAINED')).toBeInTheDocument();
  });

  it('uses verified pair links and keeps mint-only positions as read-only inspection rows', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    await screen.findByRole('heading', { name: 'ALPHA / DETAIL' });
    const inspectPosition = screen.getByRole('button', { name: 'Inspect ALPHA paper position' });
    expect(inspectPosition).toHaveAttribute('aria-pressed', 'true');
    const alphaLinks = screen.getAllByRole('link', { name: 'ALPHA' });
    expect(alphaLinks.length).toBeGreaterThan(0);
    for (const link of alphaLinks) expect(link).not.toHaveAttribute('href', expect.stringContaining('Mint111'));
    expect(screen.getByText('PAIR LINK NOT EXPOSED')).toBeInTheDocument();
    expect(screen.getByText('TRITON VIXEN PUMPFUN')).toBeInTheDocument();
  });

  it('opens an accessible closed-trade inspection row with source attribution', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    const inspectTrade = await screen.findByRole('button', { name: 'Inspect WIN closed trade' });
    fireEvent.click(inspectTrade);
    expect(screen.getByRole('heading', { name: 'WIN / DETAIL' })).toBeInTheDocument();
    expect(screen.getByText('TRITON VIXEN PUMPFUN', { selector: '.detail-grid strong' })).toBeInTheDocument();
    expect(screen.getByText('1h 30m', { selector: '.detail-grid strong' })).toBeInTheDocument();
  });

  it('keeps every closed-trade field available in the desktop row', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    const inspectTrade = await screen.findByRole('button', { name: 'Inspect WIN closed trade' });
    const row = inspectTrade.closest('.trade-row');
    expect(row).toHaveTextContent('WIN');
    expect(row).toHaveTextContent('trailing stop');
    expect(row).toHaveTextContent('HELD 1h 30m');
    expect(row?.querySelector('time')).toHaveTextContent(/SOL/);
    expect(row?.querySelector('.trade-reason')).toBeInTheDocument();
  });

  it('defines a mobile closed-trade card layout without hiding essential fields', () => {
    expect(styles).toContain('@media (max-width: 640px)');
    expect(styles).toContain('.trade-row { grid-template-columns: 4px minmax(0, 1fr); grid-template-areas: "status asset" "status detail";');
    expect(styles).toContain('.trade-row > button { grid-area: detail; grid-template-columns: minmax(0, 1fr) auto;');
    expect(styles).toContain('.trade-row time { grid-column: 1 / -1;');
  });

  it('contains long closed-trade token names without text overlap', async () => {
    const longSymbol = 'EXTREMELY-LONG-TOKEN-NAME-WITHOUT-BREAKS';
    const longData = { ...dashboardData, closedTrades: [{ ...dashboardData.closedTrades[0], symbol: longSymbol }] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => longData }));
    render(<App pollMs={60_000} />);

    const tokenLink = await screen.findByRole('link', { name: longSymbol });
    expect(tokenLink).toHaveAttribute('title', longSymbol);
    expect(tokenLink.closest('.trade-row')).not.toBeNull();
    expect(styles).toContain('.trade-row > .coin-link { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }');
    expect(styles).toContain('.trade-reason { min-width: 0; overflow-wrap: anywhere; }');
  });

  it('keeps market ticker status visible in the narrow responsive layout', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    const ticker = (await screen.findByText('MARKET TICKER')).closest('.market-ticker');
    expect(ticker?.querySelector('.ticker-window')).toBeInTheDocument();
    expect(ticker?.querySelector('.ticker-source')).toHaveTextContent('COINGECKO');
    expect(styles).toContain('.ticker-window { min-width: 0; max-width: 100%; overflow: hidden; }');
    expect(styles).toContain('.ticker-source { min-width: 0; overflow: hidden; text-overflow: ellipsis; }');
    expect(styles).toContain('.ticker-window { grid-column: 1 / -1; grid-row: 2;');
    expect(styles).toContain('.ticker-source { max-width: none; width: 100%; overflow: visible; text-overflow: clip;');
    expect(styles).toContain('.ticker-track { width: 100%; min-width: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));');
    expect(styles).toContain('animation: none;');
  });

  it('applies horizontal-overflow containment to the corrected components', () => {
    expect(styles).toContain('.positions-surface.is-empty { min-height: 0; align-self: start; }');
    expect(styles).toContain('.closed-trades, .closed-trades .compact-list, .trade-row, .trade-row > button { min-width: 0; max-width: 100%; }');
    expect(styles).toContain('.market-ticker { max-width: 100%;');
  });

  it('contains long scanner symbols and dense telemetry without mobile overlap', () => {
    expect(styles).toContain('.feed-row { grid-template-columns: 53px 10px minmax(0, 64px) minmax(0, 1fr);');
    expect(styles).toContain('.feed-row .coin-link { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }');
    expect(styles).toContain('.metric:last-child { grid-column: 1 / -1; }');
    expect(styles).toContain('.rejection-detail-list article { grid-template-columns: 54px minmax(0, 1fr);');
  });

  it('renders live provider health instead of fabricating unmeasured dependencies', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    const statusPanel = await screen.findByRole('region', { name: 'SYSTEM STATUS' });
    // TRITON-ONLY: de verboden providers (BIRDEYE/GECKO/SOLANA WS) zijn verwijderd.
    expect(statusPanel).toHaveTextContent('TRITON');
    expect(statusPanel).not.toHaveTextContent('BIRDEYE');
    expect(statusPanel).not.toHaveTextContent('SOLANA WS');
    expect(statusPanel).toHaveTextContent('DASHBOARD FETCH');
    expect(statusPanel).toHaveTextContent(/\d+ MS/);
    // Zonder providerHealth valt de UI terug op 'ok' → OBSERVED, niet 'NOT EXPOSED'
    expect(statusPanel).toHaveTextContent('OBSERVED');
    expect(screen.queryByRole('button', { name: /^(restart|deploy|promote strategy|buy|sell|place trade)/i })).not.toBeInTheDocument();
  });

  it('keeps market news inside the secondary context tier', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => dashboardData }));
    render(<App pollMs={60_000} />);

    const newsHeading = await screen.findByRole('heading', { name: 'MARKET NEWS' });
    expect(newsHeading.closest('.secondary-context')).not.toBeNull();
    expect(screen.getByText('SECONDARY CONTEXT · NEVER A TRADE SIGNAL')).toBeInTheDocument();
  });

  it('lazy-loads an accessible Research workspace without breaking Paper Monitor navigation', async () => {
    const researchBodies: Record<string, unknown> = {
      summary: {
        schemaVersion: 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1', sourceClass: 'SYNTHETIC_FIXTURE_ONLY', runId: 'fixture-run', observedAt: '2026-08-20T20:00:00.000Z', evidenceClass: 'SYNTHETIC', realData: false, acceptedSilver: false, researchReady: false, activationVerdict: 'HOLD_UNPROVEN_ACTIVATION',
        eligibility: { pilotEligible: false, transportPilot: { contractReady: true, inputMode: 'SYNTHETIC_FIXTURE_ONLY', preflightStatus: 'NOT_RUN', eligible: false, executionAuthorized: false }, acceptedSilver: { eligible: false, activationVerdict: 'HOLD_UNPROVEN_ACTIVATION', provenRegistryEntries: 0, totalRegistryEntries: 10 }, research: { approved: false, researchReady: false, strategyInputEligible: false, profitabilityEvidence: false } },
        progress: { requestedSlots: 0, reconciledSlots: 0, skippedSlots: 0, provisionalSlots: 0, resolvedSlots: 0, currentSlot: 0, lastCompletedSlot: 0, coveragePercent: 0, deterministicRerun: 'MATCH' },
        dataflow: { callbacks: 0, blocks: 0, transactions: 0, topLevelInstructions: 0, innerInstructions: 0, pumpCandidates: 0, failedPumpTransactions: 0, unknownDiscriminators: 0, quarantines: 0, exactRetries: 0, duplicateConflicts: 0 },
      },
      events: { schemaVersion: 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1', rows: [], total: 0, cursor: 0, limit: 50, nextCursor: null },
      quarantines: { schemaVersion: 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1', rows: [], total: 0, cursor: 0, limit: 50, nextCursor: null },
      provenance: { schemaVersion: 'PHASE8A_RESEARCH_PROVENANCE_RESPONSE_1', provenance: { sourceManifestSha256: '1'.repeat(64), configSha256: '2'.repeat(64), schemaSha256: '3'.repeat(64), reducerGitSha: '4'.repeat(40), inputSha256: '5'.repeat(64), aggregateOutputSha256: '6'.repeat(64), rerunSha256: '6'.repeat(64), approvalStatus: 'CANDIDATE_UNAPPROVED', completeness: 'FIXTURE_COMPLETE', uncertainty: 'UNPROVEN_ACTIVATION' } },
      metrics: { schemaVersion: 'PHASE8A_RESEARCH_METRICS_RESPONSE_1', metrics: { bytesRead: 0, bytesWritten: 0, outputBytes: 0, queueDepth: 0, peakRssBytes: 0, stageDurationsMs: {}, walClean: 1, checkpointPublished: 1, quarantineByReason: {} } },
    };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/dashboard-data') return Promise.resolve({ ok: true, json: async () => dashboardData });
      const key = url.includes('/events') ? 'events' : url.includes('/quarantines') ? 'quarantines' : url.includes('/provenance') ? 'provenance' : url.includes('/metrics') ? 'metrics' : 'summary';
      return Promise.resolve({ ok: true, json: async () => researchBodies[key] });
    }));
    render(<App pollMs={60_000} />);
    const paper = await screen.findByRole('tab', { name: 'Paper Monitor' });
    const research = screen.getByRole('tab', { name: 'Research // Pilot A' });
    expect(paper).toHaveAttribute('aria-selected', 'true');
    expect(research).toHaveAttribute('aria-selected', 'false');
    fireEvent.click(research);
    await waitFor(() => expect(screen.getByRole('heading', { name: 'RESEARCH // PILOT A' })).toBeInTheDocument());
    expect(research).toHaveAttribute('aria-selected', 'true');
    research.focus();
    fireEvent.keyDown(research, { key: 'ArrowLeft' });
    await waitFor(() => expect(paper).toHaveFocus());
    expect(await screen.findByText('PAPER // MONITOR')).toBeInTheDocument();
    expect(paper).toHaveAttribute('aria-selected', 'true');
    expect(readFileSync('frontend/src/App.tsx', 'utf8')).toContain("lazy(() => import('./research/ResearchCockpit.js').then");
  });

  it('keeps the Research tab reachable when the paper endpoint is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) }));
    render(<App pollMs={60_000} />);
    const research = screen.getByRole('tab', { name: 'Research // Pilot A' });
    fireEvent.click(research);
    expect(await screen.findByText('RESEARCH OUTPUT UNAVAILABLE')).toBeInTheDocument();
  });
});
