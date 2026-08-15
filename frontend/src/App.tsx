import { useCallback, useEffect, useMemo, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

export type DashboardData = {
  mode: 'paper';
  updatedAt: string;
  summary: { availableSol: number; realizedPnlSol: number; openPositions: number; totalEquitySol: number; winRate: number };
  scanner: { strategy: 'quant-momentum'; status: 'online'; lastScanAt: string; scans: number; candidatesFound: number; duplicateSuppressed: number; meaningfulEvents: number; consecutiveLosses: number; whaleInterestMints: string[] };
  positions: Position[];
  equity: Array<{ at: string; equitySol: number }>;
  feed: FeedItem[];
  candidates: Candidate[];
  rejections: Rejection[];
  closedTrades: ClosedTrade[];
  marketContext?: { updatedAt: string; ticker: Array<{ symbol: string; name: string; priceUsd: number; change24hPercent: number }>; solEur?: number; news: Array<{ source: string; title: string; url: string; publishedAt: string }> };
  providerHealth?: Array<{ provider: string; status: 'ok' | 'degraded' | 'down' | 'DISABLED_OFFLINE_ZERO_COST'; count: number; lastError?: string }>;
  /** Offline zero-cost mode (TRITON_LIVE_ENABLED=false) — géén live Triton. */
  offline?: 'OFFLINE_ZERO_COST';
};

type ControlsData = {
  engine: { scannerRunning: boolean; scannerState: string; providers: Record<string, boolean>; lastScanAt?: string; cycles: number };
  providerLatency: Record<string, number>;
};

type Position = { mint: string; symbol: string; openedAt: string; entryPriceUsd: number; markPriceUsd: number; highPriceUsd: number; allocatedSol: number; entryCostSol: number; unrealizedPnlSol: number; unrealizedPnlPercent: number };
type FeedItem = { id: string; pairId: string; at: string; type: 'scan_complete' | 'paper_entry' | 'paper_exit' | 'rejected' | 'duplicate_suppressed'; symbol: string; detail: string };
type Candidate = { pairId: string; mint: string; symbol: string; score: number; source: string; at: string; whaleInterest?: boolean };
type Rejection = {
  pairId: string; mint: string; symbol: string; reason: string; rejectionClass: 'market' | 'risk';
  score: number; source: string; at: string; occurrences: number; firstEvaluatedAt: string; lastEvaluatedAt: string;
  pairCreatedAt?: string; firstSeenAt?: string; observedAt?: string; evaluatedAt: string; detectionDelayMs?: number;
};
type ClosedTrade = { pairId: string; mint: string; symbol: string; reason: string; source: string; at: string; pnlSol: number; heldMinutes?: number };

type AppProps = { pollMs?: number };

export function App({ pollMs = 5_000 }: AppProps) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedMint, setSelectedMint] = useState<string | null>(null);
  const [chartRange, setChartRange] = useState<'1H' | '6H' | '24H' | '7D' | '30D' | 'ALL'>('ALL');
  const [currency, setCurrency] = useState<'SOL' | 'USD' | 'EUR'>('USD');
  const [selectedTrade, setSelectedTrade] = useState<ClosedTrade | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('theme') as 'dark' | 'light') || 'dark');
  const [refreshing, setRefreshing] = useState(false);
  const [apiLatencyMs, setApiLatencyMs] = useState<number | null>(null);

  useEffect(() => { document.body.className = theme; localStorage.setItem('theme', theme); }, [theme]);

  const refresh = useCallback(async () => {
    const startedAt = Date.now();
    setRefreshing(true);
    try {
      const response = await fetch('/api/dashboard-data', { headers: { accept: 'application/json' } });
      if (!response.ok) throw new Error(`Data feed unavailable (${response.status})`);
      setData(await response.json() as DashboardData);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Data feed unavailable');
    } finally {
      setApiLatencyMs(Math.max(0, Date.now() - startedAt));
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), pollMs);
    return () => window.clearInterval(interval);
  }, [pollMs, refresh]);

  const selected = useMemo(() => data?.positions.find((position) => position.mint === selectedMint) ?? data?.positions[0] ?? null, [data, selectedMint]);
  const chartData = useMemo(() => filterEquity(data?.equity ?? [], chartRange, now), [data?.equity, chartRange, now]);
  const CustomTooltip = useCallback(({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) => {
    if (!active || !payload?.length || !label) return null;
    const val = payload[0].value;
    const start = chartData[0]?.equitySol ?? val;
    const change = val - start;
    const pct = start > 0 ? (change / start) * 100 : 0;
    return <div style={{ background: '#0f172a', border: '1px solid #334155', padding: '8px 12px', fontSize: 12 }}><p style={{ margin: 0, color: '#94a3b8' }}>{formatTime(label)}</p><p style={{ margin: '4px 0 0', color: '#f8fafc', fontWeight: 700 }}>{val.toFixed(4)} SOL</p><p style={{ margin: 0, color: change >= 0 ? '#22c55e' : '#ef4444' }}>{change >= 0 ? '+' : ''}{change.toFixed(4)} SOL ({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)</p></div>;
  }, [chartData]);
  if (!data) return <main className="boot"><span className="pulse" /> <span>{error ?? 'Synchronizing paper ledger…'}</span></main>;
  const solUsd = data.marketContext?.ticker.find((coin) => coin.symbol === 'SOL')?.priceUsd;
  const activeCurrency = currency === 'USD' && solUsd ? 'USD' : currency === 'EUR' && data.marketContext?.solEur ? 'EUR' : 'SOL';
  const solRate = activeCurrency === 'USD' ? solUsd : activeCurrency === 'EUR' ? data.marketContext?.solEur : undefined;
  const formatValue = (solValue: number) => formatPortfolioValue(solValue, activeCurrency, solRate);
  const formatPnl = (solValue: number) => formatPortfolioValue(solValue, activeCurrency, solRate, true);
  const unrealizedPnlSol = data.positions.reduce((total, position) => total + position.unrealizedPnlSol, 0);

  return (
    <main className="monitor-shell">
      <header className="masthead">
        <div className="brand"><span className="brand-mark">◢</span><div><strong>PAPER // MONITOR</strong><small>SOLANA SCANNER · READ ONLY</small></div></div>
        <div className="system-state"><span className="live-dot" /> SCANNER {data.scanner.status.toUpperCase()} <span className="separator">/</span> <time dateTime={now.toISOString()} aria-label="Current Netherlands time">{formatNlTime(now)}</time> <span className="separator">/</span> <time dateTime={data.updatedAt}>SCAN AGE {formatAge(now, data.updatedAt)}</time></div>
        <div className="monitor-controls" aria-label="Dashboard controls"><button type="button" className={refreshing ? 'refreshing' : ''} onClick={() => void refresh()}>{refreshing ? '↻ LOADING…' : '↻ REFRESH'}</button><button type="button" className="theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀ LIGHT' : '☾ DARK'}</button><span className="currency-controls" role="group" aria-label="Portfolio currency">{(['SOL', 'USD', 'EUR'] as const).map((unit) => <button type="button" className={activeCurrency === unit ? 'active' : ''} aria-pressed={activeCurrency === unit} disabled={unit === 'USD' && !solUsd || unit === 'EUR' && !data.marketContext?.solEur} onClick={() => setCurrency(unit)} key={unit}>{unit}</button>)}</span></div>
        <div className="paper-flag">SIMULATION · NO WALLET · NO ORDERS · LAN http://192.168.1.234:3000 · 5G/TK http://100.79.221.55:3000</div>
      </header>

      {error && <div className="data-warning" role="status"><strong>DATA FEED WARNING</strong><span>{error}</span><small>Last valid snapshot remains on screen.</small></div>}

      <section className="metrics" aria-label="Portfolio summary">
        <Metric label={`TOTAL EQUITY · ${activeCurrency}`} value={formatValue(data.summary.totalEquitySol)} tone="neutral" />
        <Metric label={`AVAILABLE · ${activeCurrency}`} value={formatValue(data.summary.availableSol)} tone="neutral" />
        <Metric label={`REALIZED P&L · ${activeCurrency}`} value={formatPnl(data.summary.realizedPnlSol)} tone={tone(data.summary.realizedPnlSol)} />
        <Metric label={`UNREALIZED P&L · ${activeCurrency}`} value={formatPnl(unrealizedPnlSol)} tone={tone(unrealizedPnlSol)} />
        <Metric label="OPEN POSITIONS" value={String(data.summary.openPositions).padStart(2, '0')} tone="neutral" />
        <Metric label="WIN RATE" value={`${data.summary.winRate.toFixed(1)}%`} tone={data.summary.winRate > 0 ? 'positive' : 'neutral'} />
        <Metric label="SCANS / QUALIFIED" value={`${data.scanner.scans} / ${data.scanner.candidatesFound}`} tone="neutral" />
      </section>

      <MarketTicker context={data.marketContext} />

      <section className="grid-primary">
        <section className="surface equity-surface" aria-labelledby="equity-title">
          <SurfaceTitle id="equity-title" eyebrow="PORTFOLIO · SOL-DENOMINATED LEDGER" title="EQUITY / P&L" right={<div className="chart-controls" role="group" aria-label="Equity chart range"><span className="chart-key">● EQUITY · SOL</span>{(['1H', '6H', '24H', '7D', '30D', 'ALL'] as const).map((range) => <button type="button" className={chartRange === range ? 'active' : ''} aria-pressed={chartRange === range} onClick={() => setChartRange(range)} key={range}>{range === 'ALL' ? 'ALL RETAINED' : range}</button>)}</div>} />
          {chartData.length ? <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
                <defs>
                  <linearGradient id="equityGreen" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} /><stop offset="100%" stopColor="#22c55e" stopOpacity={0} /></linearGradient>
                  <linearGradient id="equityRed" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#ef4444" stopOpacity={0.3} /><stop offset="100%" stopColor="#ef4444" stopOpacity={0} /></linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="#1e293b" strokeDasharray="3 3" />
                <XAxis dataKey="at" tickFormatter={formatTime} axisLine={{ stroke: '#1e293b' }} tickLine={false} tick={{ fill: '#64748b', fontSize: 10 }} minTickGap={50} />
                <YAxis dataKey="equitySol" domain={['dataMin - 0.2', 'dataMax + 0.2']} tickFormatter={(value) => `${value.toFixed(2)}`} axisLine={{ stroke: '#1e293b' }} tickLine={false} tick={{ fill: '#64748b', fontSize: 10 }} width={56} />
                <ReferenceLine y={chartData[0]?.equitySol ?? 10} stroke="#334155" strokeDasharray="4 4" strokeWidth={1} />
                <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#475569', strokeWidth: 1 }} />
                <Area type="monotone" dataKey="equitySol" stroke={chartTone(chartData)} strokeWidth={2} fill={`url(#${chartTone(chartData) === '#22c55e' ? 'equityGreen' : 'equityRed'})`} activeDot={{ r: 4, fill: '#f8fafc' }} />
              </AreaChart>
            </ResponsiveContainer>
          </div> : <div className="chart-empty"><span className="chart-empty-mark">∿</span><strong>No retained equity observations</strong><small>The API returned no points for this ledger snapshot.</small></div>}
          <div className="chart-footer"><span>{`${chartData.length} RETAINED OBSERVATIONS · ${chartRange === 'ALL' ? 'ALL RETAINED' : chartRange}`}</span><span>MARK-TO-MODEL · LAST LEDGER UPDATE {formatTime(data.updatedAt)}</span></div>
        </section>

        <section className="surface feed-surface" aria-labelledby="feed-title">
          <SurfaceTitle id="feed-title" eyebrow="LIVE LOG" title="SCANNER ACTIVITY" right={<span>{data.feed.length} EVENTS</span>} />
          <div className="scanner-strip" aria-label="Retained scanner counters">
            <span>{`${data.scanner.scans} RETAINED SCANS`}</span>
            <span>{`${data.scanner.candidatesFound} QUALIFIED · RETAINED`}</span>
            <span>{`${data.scanner.meaningfulEvents} SIGNAL EVENTS`}</span>
            <span>{`${data.scanner.duplicateSuppressed} DUPLICATES`}</span>
          </div>
          <div className="feed-summary"><span>QUANT MOMENTUM · {data.scanner.meaningfulEvents} RETAINED SIGNALS · {data.scanner.duplicateSuppressed} DUPLICATES FILTERED</span><span>LAST SCAN {formatTime(data.scanner.lastScanAt)}</span></div>
          <div className="feed-list">{data.feed.map((item) => <FeedRow key={item.id} item={item} />)}</div>
        </section>
      </section>

      <section className={`grid-secondary${selected ? '' : ' no-detail'}`}>
        <section className={`surface positions-surface${data.positions.length ? '' : ' is-empty'}`} aria-labelledby="positions-title">
          <SurfaceTitle id="positions-title" eyebrow="LEDGER" title="OPEN POSITIONS" right={<span>{data.positions.length} ACTIVE</span>} />
          {data.positions.length > 0 && <div className="position-head"><span>ASSET / MINT</span><span>ENTRY USD / MARK USD</span><span>HIGH USD</span><span>ALLOCATION · {activeCurrency}</span><span>LIVE P&L · {activeCurrency}</span></div>}
          <div className="position-list">{data.positions.length ? data.positions.map((position) => <button type="button" aria-label={`Inspect ${position.symbol} paper position`} aria-pressed={selected?.mint === position.mint} className={`position-row ${selected?.mint === position.mint ? 'selected' : ''}`} key={position.mint} onClick={() => setSelectedMint(position.mint)}><span><strong>{position.symbol}</strong><small>{truncateMint(position.mint)}</small></span><span>${formatPrice(position.entryPriceUsd)}<small>MARK ${formatPrice(position.markPriceUsd)}</small></span><span>${formatPrice(position.highPriceUsd)}</span><span>{formatValue(position.allocatedSol)}</span><span className={tone(position.unrealizedPnlSol)}>{formatPnl(position.unrealizedPnlSol)}<small>{signedPercent(position.unrealizedPnlPercent)}</small></span></button>) : <Empty label="No paper positions open" />}</div>
        </section>

        {selected && <aside className="surface detail-surface" aria-live="polite"><CoinDetail position={selected} formatValue={formatValue} formatPnl={formatPnl} /></aside>}
      </section>

      <section className="grid-tertiary">
        <CandidatesPanel items={data.candidates} />
        <RejectionAnalytics items={data.rejections} />
        <ClosedTrades trades={data.closedTrades} onSelect={setSelectedTrade} selected={selectedTrade} />
      </section>

      {selectedTrade && <aside className="surface detail-surface" aria-live="polite">
        <SurfaceTitle id="trade-detail" eyebrow="CLOSED TRADE" title={`${selectedTrade.symbol} / DETAIL`} />
        <div className="detail-grid">
          <Detail label="EXIT REASON" value={humanize(selectedTrade.reason)} />
          <Detail label="P&L" value={`${signedSol(selectedTrade.pnlSol)}`} />
          <Detail label="HELD" value={selectedTrade.heldMinutes !== undefined ? formatDuration(selectedTrade.heldMinutes) : '—'} />
          <Detail label="CLOSED AT" value={formatTime(selectedTrade.at)} />
          <Detail label="SOURCE" value={humanize(selectedTrade.source).toUpperCase()} />
        </div>
        <div className={`pnl-block ${tone(selectedTrade.pnlSol)}`}>
          <span>REALIZED P&L</span>
          <strong>{signedSol(selectedTrade.pnlSol)}</strong>
        </div>
        <p className="detail-note">Paper-only trade. Simulated fills with 1% slippage + 1% fee.</p>
      </aside>}

      <section className="secondary-context" aria-label="Secondary monitoring context">
        <SystemStatus apiLatencyMs={apiLatencyMs} scanner={data.scanner} updatedAt={data.updatedAt} now={now} providerHealth={data.providerHealth} />
        <EngineControlSection />
        <NewsPanel context={data.marketContext} />
      </section>
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: 'positive' | 'negative' | 'neutral' }) { return <div className="metric"><span>{label}</span><strong className={tone}>{value}</strong></div>; }
function SurfaceTitle({ id, eyebrow, title, right }: { id: string; eyebrow: string; title: string; right?: React.ReactNode }) { return <header className="surface-title"><div><small>{eyebrow}</small><h2 id={id}>{title}</h2></div>{right && <span className="surface-meta">{right}</span>}</header>; }
function MarketTicker({ context }: { context: DashboardData['marketContext'] }) { return <section className="market-ticker" aria-label="Major crypto market ticker"><span className="ticker-label">MARKET TICKER</span><div className="ticker-window"><div className="ticker-track">{context?.ticker.length ? context.ticker.map((coin) => <span className="ticker-item" key={coin.symbol} title={coin.name}><strong>{coin.symbol}</strong><b>${formatUsd(coin.priceUsd)}</b><em className={tone(coin.change24hPercent)}>{signedPercent(coin.change24hPercent)}</em></span>) : <span className="ticker-item muted">Major-coin data is temporarily unavailable</span>}</div></div><time className="ticker-source">COINGECKO · {context ? formatTime(context.updatedAt) : '—'}</time></section>; }
function NewsPanel({ context }: { context: DashboardData['marketContext'] }) { return <aside className="surface news-surface" aria-labelledby="news-title"><SurfaceTitle id="news-title" eyebrow="SECONDARY CONTEXT · NEVER A TRADE SIGNAL" title="MARKET NEWS" right={<span>{context?.news.length ?? 0} ITEMS</span>} /><div className="news-list">{context?.news.length ? context.news.map((item) => <article className="news-row" key={`${item.url}-${item.publishedAt}`}><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a><div><span>{item.source}</span><time>{formatTime(item.publishedAt)}</time></div></article>) : <Empty label="News feed is temporarily unavailable" />}</div><p className="news-disclaimer">Headlines are context only — never an automatic trade signal.</p></aside>; }
function statusLabel(status: 'ok' | 'degraded' | 'down' | 'DISABLED_OFFLINE_ZERO_COST'): string {
  if (status === 'ok') return 'OBSERVED';
  if (status === 'down') return 'DOWN';
  if (status === 'DISABLED_OFFLINE_ZERO_COST') return 'OFFLINE_ZERO_COST';
  return 'DEGRADED';
}
function SystemStatus({ apiLatencyMs, scanner, updatedAt, now, providerHealth }: { apiLatencyMs: number | null; scanner: DashboardData['scanner']; updatedAt: string; now: Date; providerHealth?: DashboardData['providerHealth'] }) {
  const unavailableMetrics = ['PROVIDER LATENCY', 'RECONNECTS', 'ERRORS', 'CPU', 'MEMORY'];
  return <section className="surface system-status-surface" aria-labelledby="system-status-title">
    <SurfaceTitle id="system-status-title" eyebrow="READ ONLY · TELEMETRY BOUNDARY" title="SYSTEM STATUS" right={<span>NO CONTROL ACTIONS</span>} />
    <div className="observed-status">
      <div><span className="status-pip observed" /><strong>DASHBOARD API</strong><b>OBSERVED</b><small>DASHBOARD FETCH</small><time>{apiLatencyMs ?? 0} MS</time></div>
      <div><span className="status-pip reported" /><strong>SCANNER HEARTBEAT</strong><b>REPORTED {scanner.status.toUpperCase()}</b><small>SNAPSHOT AGE</small><time>{formatAge(now, updatedAt)}</time></div>
    </div>
    <div className="dependency-grid">
      {(providerHealth?.length ? providerHealth : [
        // TRITON-ONLY fallback: de verboden providers (BIRDEYE/GECKO/SOLANA WS)
        // zijn uit de code gesloopt en mogen niet meer als status verschijnen.
        { provider: 'TRITON', status: 'ok' as const },
      ]).map((dep) => {
        return (
          <article key={dep.provider}>
            <span className={`status-pip ${dep.status}`} />
            <strong>{dep.provider}</strong>
            <b className={dep.status === 'ok' ? 'exposed' : dep.status === 'DISABLED_OFFLINE_ZERO_COST' ? 'offline' : ''}>{statusLabel(dep.status)}</b>
            <small>{dep.status === 'DISABLED_OFFLINE_ZERO_COST' ? 'OFFLINE_ZERO_COST — triton live uit (prépaid $0)' : 'From latest scan telemetry'}</small>
          </article>
        );
      })}
    </div>
    <div className="telemetry-grid">{unavailableMetrics.map((metric) => <div key={metric}><span>{metric}</span><strong className="not-exposed">NOT EXPOSED</strong></div>)}</div>
    <p className="telemetry-note">Provider labels reflect the latest scan cycle's diagnostics only — degraded/down means recent per-provider errors were observed in this window.</p>
  </section>;
}
function FeedRow({ item }: { item: FeedItem }) { return <article className="feed-row"><time>{formatTime(item.at)}</time><span className={`event-pip ${item.type}`}>●</span><DexLink pairId={item.pairId} symbol={item.symbol} /><span className="event-type">{item.type.replace('_', ' ')}</span><span>{humanize(item.detail)}</span></article>; }
function CoinDetail({ position, formatValue, formatPnl }: { position: Position; formatValue: (solValue: number) => string; formatPnl: (solValue: number) => string }) { return <><SurfaceTitle id="coin-detail" eyebrow="SELECTED ASSET" title={`${position.symbol} / DETAIL`} /><div className="coin-mint"><span>{truncateMint(position.mint)}</span><small>PAIR LINK NOT EXPOSED</small></div><div className="detail-grid"><Detail label="ENTRY · USD" value={`$${formatPrice(position.entryPriceUsd)}`} /><Detail label="LIVE MARK · USD" value={`$${formatPrice(position.markPriceUsd)}`} /><Detail label="SESSION HIGH · USD" value={`$${formatPrice(position.highPriceUsd)}`} /><Detail label="ALLOCATION" value={formatValue(position.allocatedSol)} /><Detail label="OPENED · AMSTERDAM" value={formatTime(position.openedAt)} /></div><div className={`pnl-block ${tone(position.unrealizedPnlSol)}`}><span>UNREALIZED P&L</span><strong>{formatPnl(position.unrealizedPnlSol)}</strong><small>{signedPercent(position.unrealizedPnlPercent)} FROM ENTRY</small></div><p className="detail-note">Modelled paper position. Values are observational and cannot submit transactions.</p></>; }
function Detail({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{value}</strong></div>; }
function ClosedTrades({ trades, onSelect, selected }: { trades: ClosedTrade[]; onSelect: (trade: ClosedTrade) => void; selected: ClosedTrade | null }) {
  return <section className="surface compact-surface closed-trades">
    <SurfaceTitle id="closed-trades" eyebrow="REALIZED P&L" title="CLOSED TRADES" right={<span>{trades.length} TRADES</span>} />
    <div className="compact-list" style={{ maxHeight: 400, overflowY: 'auto' }}>
      {trades.length ? trades.map((trade) => (
        <article key={`${trade.pairId}-${trade.at}`} className={`trade-row ${selected?.at === trade.at && selected?.pairId === trade.pairId ? 'selected' : ''}`}>
          <span className={`status-bar ${tone(trade.pnlSol)}`} />
          <DexLink pairId={trade.pairId} symbol={trade.symbol} />
          <button type="button" aria-label={`Inspect ${trade.symbol} closed trade`} aria-pressed={selected?.at === trade.at && selected?.pairId === trade.pairId} onClick={() => onSelect(trade)}>
            <span className="trade-reason">{humanize(trade.reason)}</span>
            {trade.heldMinutes !== undefined && <span className="trade-duration">HELD {formatDuration(trade.heldMinutes)}</span>}
            <time className={tone(trade.pnlSol)}>{signedSol(trade.pnlSol)} · {formatTime(trade.at)}</time>
          </button>
        </article>
      )) : <Empty label="No closed paper trades yet" />}
    </div>
  </section>;
}
function EngineControlSection() {
  const [controls, setControls] = useState<ControlsData | null>(null);
  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/controls', { headers: { accept: 'application/json' } });
      if (response.ok) setControls(await response.json() as ControlsData);
    } catch { /* dashboard-poll baséret op de hoofdloop */ }
  }, []);
  useEffect(() => { refresh(); const id = setInterval(refresh, 5_000); return () => clearInterval(id); }, [refresh]);
  const act = useCallback(async (body: Record<string, unknown>) => {
    try {
      const response = await fetch('/api/control', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      if (response.ok) setControls(await response.json() as ControlsData);
    } catch { /* read-only fallback; toggles blijven lokaal */ }
  }, []);
  if (!controls) return <div />; // controls nog niet geladen of endpoint onbeschikbaar
  const providers = Object.entries(controls.engine?.providers ?? {});
  return <section className="surface engine-control-surface" aria-labelledby="engine-control-title">
    <SurfaceTitle id="engine-control-title" eyebrow="DASHBOARD CONTROL" title="ENGINE CONTROL" right={<span>{controls.engine?.cycles ?? 0} CYCLES</span>} />
    <div className="engine-control-row">
      <span className="control-label">SCANNER</span>
      <button
        type="button"
        className={controls.engine?.scannerRunning ? 'control-toggle is-on' : 'control-toggle'}
        aria-pressed={!!controls.engine?.scannerRunning}
        disabled={!controls.engine}
        onClick={() => act({ scannerRunning: !controls.engine?.scannerRunning })}
      >{controls.engine?.scannerRunning ? 'STOP' : 'START'}</button>
      <span className="control-state">{controls.engine ? (controls.engine.scannerRunning ? 'RUNNING' : 'PAUSED') : '…'}</span>
      {controls.engine?.lastScanAt ? <time>· LAST SCAN {formatTime(controls.engine.lastScanAt)}</time> : null}
    </div>
    {providers.map(([name, enabled]) => (
      <div className="engine-provider-row" key={name}>
        <span className="control-label">{name}</span>
        <button
          type="button"
          className={enabled ? 'control-toggle is-on' : 'control-toggle'}
          aria-pressed={enabled}
          disabled={!controls.engine}
          onClick={() => act({ provider: name, enabled: !enabled })}
        >{enabled ? 'ON' : 'OFF'}</button>
        <span className="latency-value">{controls.providerLatency?.[name] != null ? `${controls.providerLatency[name]} ms` : '—'}</span>
      </div>
    ))}
    <p className="control-note">Controls pause the in-process paper scanner and toggle providers. No live orders or wallet actions are exposed.</p>
  </section>;
}
function CandidatesPanel({ items }: { items: Candidate[] }) {
  return <section className={`surface compact-surface candidates-surface${items.length ? '' : ' is-empty'}`}>
    <SurfaceTitle id="candidates" eyebrow="QUALIFIED · RETAINED WINDOW" title="CANDIDATES" right={<span>{items.length} ITEMS</span>} />
    {items.length ? <><div className="candidate-head"><span>ASSET</span><span>ENTRY SCORE</span><span>SOURCE</span><span>OBSERVED · AMS</span></div><div className="candidate-list">{items.map((item) => <article key={item.pairId} className="candidate-row">
      <span className="status-bar candidate" />
      <DexLink pairId={item.pairId} symbol={item.symbol} />
      {item.whaleInterest && <span className="whale-badge" title="Whale activity detected">🐋</span>}
      <strong>{item.score.toFixed(1)}</strong>
      <span>{humanize(item.source).toUpperCase()}</span>
      <time>{formatTime(item.at)}</time>
    </article>)}</div></> : <Empty label="No candidates in the retained feed" />}
  </section>;
}
function RejectionAnalytics({ items }: { items: Rejection[] }) {
  const total = items.reduce((sum, item) => sum + item.occurrences, 0);
  const byReason = Array.from(items.reduce((groups, item) => groups.set(item.reason, (groups.get(item.reason) ?? 0) + item.occurrences), new Map<string, number>()))
    .sort((left, right) => right[1] - left[1]);
  return <section className="surface compact-surface rejection-surface">
    <SurfaceTitle id="rejection-analytics" eyebrow="FILTERED · RETAINED WINDOW" title="REJECTION ANALYTICS" right={<span>{total} RETAINED REJECTION EVENTS</span>} />
    {items.length ? <>
      <div className="rejection-bars">
        {byReason.map(([reason, count]) => {
          const percent = total ? (count / total) * 100 : 0;
          return <article className="rejection-bar-row" key={reason}>
            <div><strong>{humanize(reason).toUpperCase()}</strong><span>{count} EVENTS</span><b>{percent.toFixed(1)}%</b></div>
            <span className="rejection-track"><i style={{ width: `${percent}%` }} /></span>
          </article>;
        })}
      </div>
      <div className="rejection-detail-list">{items.map((item) => <article key={`${item.pairId}-${item.rejectionClass}-${item.reason}`}>
        <DexLink pairId={item.pairId} symbol={item.symbol} />
        <span className="rejection-details">
          <b>{`${item.rejectionClass === 'risk' ? 'RISK BLOCK' : 'MARKET REJECT'} · ${humanize(item.reason)} · SCORE ${item.score.toFixed(1)} · ×${item.occurrences} RETAINED`}</b>
          <small>{`PAIR CREATED ${formatTime(item.pairCreatedAt ?? '')} · FIRST SEEN ${formatTime(item.firstSeenAt ?? '')}`}</small>
          <small>{`QUOTE ${formatTime(item.observedAt ?? '')} · EVALUATED ${formatTime(item.lastEvaluatedAt ?? item.evaluatedAt ?? item.at)}`}</small>
          <small>{`DISCOVERY DELAY ${formatDelay(item.detectionDelayMs)}`}</small>
          <small>{humanize(item.source).toUpperCase()}</small>
        </span>
        <time>{formatTime(item.lastEvaluatedAt ?? item.evaluatedAt ?? item.at)}</time>
      </article>)}</div>
    </> : <Empty label="No rejections in the retained feed" />}
  </section>;
}
function DexLink({ pairId, symbol }: { pairId: string; symbol: string }) {
  // DexScreener wil het Solana MINT-adres als token-key, niet het interne
  // pairId (bv. "gx:<mint>" of pool-adres). Extraheer de 32-byte base58 mint:
  // - pairId "gx:<mint>" → mint
  // - pairId is al een mint (44 chars base58) → direct
  // - anders (echt pool-adres) → fallback op pairId zoals-het-is
  const segment = pairId?.split(':').pop() ?? '';
  const isMint = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(segment);
  const target = isMint ? segment : pairId;
  return pairId && symbol !== 'MARKET' && symbol !== 'SCANNER' ? (
    <a className="coin-link" href={`https://dexscreener.com/solana/${encodeURIComponent(target)}`} target="_blank" rel="noreferrer" title={symbol}>{symbol}</a>
  ) : <strong title={symbol}>{symbol}</strong>;
}
function humanize(value: string) { return value.replaceAll('_', ' '); }
function Empty({ label }: { label: string }) { return <div className="empty">— {label}</div>; }
function formatPortfolioValue(solValue: number, currency: 'SOL' | 'USD' | 'EUR', solRate?: number, includeSign = false) { if (currency === 'SOL' || !solRate) return includeSign ? signedSol(solValue) : `${formatSol(solValue)} SOL`; const value = solValue * solRate; return new Intl.NumberFormat('nl-NL', { style: 'currency', currency, maximumFractionDigits: 2, signDisplay: includeSign ? 'always' : 'auto' }).format(value); }
function filterEquity(points: DashboardData['equity'], range: '1H' | '6H' | '24H' | '7D' | '30D' | 'ALL', now: Date) { const durationMs = { '1H': 3_600_000, '6H': 21_600_000, '24H': 86_400_000, '7D': 604_800_000, '30D': 2_592_000_000, ALL: Infinity }[range]; const cutoff = now.valueOf() - durationMs; const filtered = points.filter((point) => new Date(point.at).valueOf() >= cutoff); return filtered.length ? filtered : points.slice(-1); }
function formatDuration(totalMinutes: number) { const days = Math.floor(totalMinutes / 1_440); const hours = Math.floor((totalMinutes % 1_440) / 60); const minutes = totalMinutes % 60; return [days ? `${days}d` : '', hours ? `${hours}h` : '', `${minutes}m`].filter(Boolean).join(' '); }
function formatDelay(milliseconds: number | undefined) { if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds) || milliseconds < 0) return '—'; const totalSeconds = Math.floor(milliseconds / 1_000); const minutes = Math.floor(totalSeconds / 60); return `${minutes}m ${totalSeconds % 60}s`; }
function chartTone(data: Array<{ equitySol: number }>): '#22c55e' | '#ef4444' { if (!data.length) return '#22c55e'; return data[data.length - 1].equitySol >= data[0].equitySol ? '#22c55e' : '#ef4444'; }
function truncateMint(mint: string): string { return mint.length > 13 ? `${mint.slice(0, 7)}…${mint.slice(-5)}` : mint; }
function formatUsd(value: number) { return value.toLocaleString(undefined, { maximumFractionDigits: value >= 1 ? 2 : 6 }); }
function formatSol(value: number) { return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }); }
function signedSol(value: number) { return `${value >= 0 ? '+' : '−'}${formatSol(Math.abs(value))} SOL`; }
function signedPercent(value: number) { return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)}%`; }
function formatPrice(value: number) { return value < 0.01 ? value.toPrecision(3) : value.toFixed(value < 1 ? 4 : 2); }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? '—' : formatNlTime(date); }
function formatNlTime(date: Date) { return new Intl.DateTimeFormat('nl-NL', { timeZone: 'Europe/Amsterdam', hour: '2-digit', minute: '2-digit', second: '2-digit', day: 'numeric', month: 'short', hour12: false }).format(date); }
function formatAge(now: Date, value: string) { const then = new Date(value); if (Number.isNaN(then.valueOf())) return '—'; const seconds = Math.max(0, Math.floor((now.valueOf() - then.valueOf()) / 1_000)); const hours = Math.floor(seconds / 3_600); const minutes = Math.floor((seconds % 3_600) / 60); return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')} AGO`; }
function tone(value: number): 'positive' | 'negative' | 'neutral' { return value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral'; }