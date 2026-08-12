import { readFile } from 'node:fs/promises';
import { extname, relative, resolve, sep } from 'node:path';
import { createServer, type Server } from 'node:http';
import type { MarketContext } from './providers/market-context.js';

const LAMPORTS_PER_SOL = 1_000_000_000;

export type DashboardPosition = {
  mint: string;
  symbol: string;
  openedAt: string;
  entryPriceUsd: number;
  highPriceUsd: number;
  allocatedLamports: number;
  entryCostLamports: number;
};

export type DashboardDecision = {
  type: 'paper_entry' | 'paper_exit' | 'rejected' | 'duplicate_suppressed';
  pairId: string;
  mint?: string;
  symbol?: string;
  score?: number;
  reason?: string;
  pnlLamports?: number;
  source?: string;
  at?: string;
  openedAt?: string;
  rejectionClass?: 'market' | 'risk';
  pairCreatedAt?: string;
  firstSeenAt?: string;
  observedAt?: string;
  evaluatedAt?: string;
  detectionDelayMs?: number;
};

export type DashboardStatus = {
  mode: 'paper';
  updatedAt: string;
  availableLamports: number;
  openPositions: DashboardPosition[];
  realizedPnlLamports: number;
  markPricesByMint?: Record<string, number>;
  equityHistory?: Array<{ at: string; equityLamports: number }>;
  recentDecisions?: DashboardDecision[];
  duplicateSuppressed?: number;
  closedTrades?: DashboardDecision[];
  marketContext?: MarketContext;
  whaleInterestMints?: string[];
  /** Samengevatte health per data-provider, afgeleid van de laatste scan
   * providerErrors. Frontend toont een live-status i.p.v. "NOT EXPOSED". */
  providerHealth?: Array<{ provider: string; status: 'ok' | 'degraded' | 'down'; errorHint?: string }>;
};

export type DashboardData = {
  mode: 'paper';
  updatedAt: string;
  summary: { availableSol: number; realizedPnlSol: number; openPositions: number; totalEquitySol: number; winRate: number };
  scanner: { strategy: 'quant-momentum'; status: 'online'; lastScanAt: string; scans: number; candidatesFound: number; duplicateSuppressed: number; meaningfulEvents: number; consecutiveLosses: number; whaleInterestMints: string[] };
  positions: Array<DashboardPosition & { markPriceUsd: number; allocatedSol: number; entryCostSol: number; unrealizedPnlSol: number; unrealizedPnlPercent: number }>;
  equity: Array<{ at: string; equitySol: number }>;
  feed: Array<{ id: string; pairId: string; at: string; type: DashboardDecision['type'] | 'scan_complete'; symbol: string; detail: string }>;
  candidates: Array<{ pairId: string; mint: string; symbol: string; score: number; source: string; at: string }>;
  rejections: Array<{
    pairId: string; mint: string; symbol: string; reason: string; rejectionClass: 'market' | 'risk';
    score: number; source: string; at: string; occurrences: number; firstEvaluatedAt: string; lastEvaluatedAt: string;
    pairCreatedAt?: string; firstSeenAt?: string; observedAt?: string; evaluatedAt: string; detectionDelayMs?: number;
  }>;
  closedTrades: Array<{ pairId: string; mint: string; symbol: string; reason: string; source: string; at: string; pnlSol: number; heldMinutes?: number }>;
  marketContext?: MarketContext;
  providerHealth?: Array<{ provider: string; status: 'ok' | 'degraded' | 'down'; errorHint?: string }>;
};

export type DashboardServer = { port: number; close(): Promise<void> };

export type DashboardControls = {
  getEngineState(): { scannerRunning: boolean; scannerState: string; providers: Record<string, boolean>; lastScanAt?: string; cycles: number };
  setScannerRunning(running: boolean): void;
  setProviderEnabled(name: string, enabled: boolean): void;
  getProviderLatency(): Record<string, number>;
};

function readJsonBody(request: import('node:http').IncomingMessage): Promise<unknown> {
  return new Promise((resolve) => {
    let body = '';
    request.on('data', (chunk: Buffer) => { body += chunk.toString('utf8'); });
    request.on('end', () => {
      try { resolve(body ? JSON.parse(body) : {}); } catch { resolve({}); }
    });
  });
}

function corsHeaders(): Record<string, string> {
  return {
    'access-control-allow-origin': '*',
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'access-control-allow-headers': 'content-type',
  };
}

export async function createDashboardServer(options: { port: number; staticDir?: string; getStatus(): DashboardStatus; controls?: DashboardControls; getDebug?(): Record<string, unknown>; controlToken?: string }): Promise<DashboardServer> {
  const handleRequest = async (request: import('node:http').IncomingMessage, response: import('node:http').ServerResponse): Promise<void> => {
    let path: string;
    try {
      path = new URL(request.url ?? '/', 'http://localhost').pathname;
    } catch {
      path = '/';
    }
    if (request.method === 'OPTIONS') {
      response.writeHead(204, corsHeaders()); response.end(); return;
    }
    if (path === '/healthz') { response.end('ok'); return; }
    if (path === '/api/control' && request.method === 'POST' && options.controls) {
      // Auth voor mutaties: als een controlToken is geconfigureerd, eisen we
      // `Authorization: Bearer <token>` — anders kan élke netwerkclient de
      // scanner stoppen / providers uitschakelen (remote DoS op controle-vlak).
      if (options.controlToken) {
        const header = (request.headers.authorization ?? '').trim();
        const expected = `Bearer ${options.controlToken}`;
        if (header !== expected) {
          response.writeHead(401, corsHeaders());
          response.end(JSON.stringify({ ok: false, error: 'unauthorized' }));
          return;
        }
      }
      const body = await readJsonBody(request) as { scannerRunning?: boolean; provider?: string; enabled?: boolean };
      if (typeof body.scannerRunning === 'boolean') options.controls.setScannerRunning(body.scannerRunning);
      if (typeof body.provider === 'string' && typeof body.enabled === 'boolean') options.controls.setProviderEnabled(body.provider, body.enabled);
      json(response, { ok: true, engine: options.controls.getEngineState() }, corsHeaders()); return;
    }
    if (path === '/api/controls' && options.controls) {
      json(response, { engine: options.controls.getEngineState(), providerLatency: options.controls.getProviderLatency() }, corsHeaders()); return;
    }
    if (request.method !== 'GET') {
      response.writeHead(404); response.end('Not found'); return;
    }
    const status = options.getStatus();
    if (path === '/api/status') { json(response, status); return; }
    if (path === '/api/debug' && options.getDebug) { json(response, options.getDebug() ?? {}); return; }
    if (path === '/api/dashboard-data') { json(response, toDashboardData(status)); return; }
    if (options.staticDir && (path === '/' || path.startsWith('/assets/'))) {
      if (await serveStaticFile(response, options.staticDir, path)) return;
    }
    if (path === '/') {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      response.end(render(status)); return;
    }
    response.writeHead(404); response.end('Not found');
  };
  const server = createServer(async (request, response) => {
    // Remote-hardening: een malformed request-target (bv. `GET http://x:99999/`)
    // deed eerder `new URL` een TypeError gooien → unhandled rejection → proces-crash
    // (remote DoS). Elke fout wordt een 500, nooit een exit.
    try {
      await handleRequest(request, response);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      try {
        response.writeHead(500, corsHeaders());
        response.end(JSON.stringify({ ok: false, error: 'internal_error', message: message.slice(0, 120) }));
      } catch {
        /* response al verzonden */
      }
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(options.port, '0.0.0.0', () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Dashboard did not bind a TCP port');
  return { port: address.port, close: () => close(server) };
}

export function toDashboardData(status: DashboardStatus): DashboardData {
  const decisions = status.recentDecisions ?? [];
  const positions = status.openPositions.map((position) => {
    const markPriceUsd = status.markPricesByMint?.[position.mint] ?? position.entryPriceUsd;
    const markedLamports = Math.floor(position.allocatedLamports * (markPriceUsd / position.entryPriceUsd));
    const unrealizedPnlLamports = markedLamports - position.entryCostLamports;
    return {
      ...position,
      markPriceUsd,
      allocatedSol: sol(position.allocatedLamports),
      entryCostSol: sol(position.entryCostLamports),
      unrealizedPnlSol: sol(unrealizedPnlLamports),
      unrealizedPnlPercent: round((unrealizedPnlLamports / position.entryCostLamports) * 100, 1),
    };
  });
  const totalEquitySol = sol(status.availableLamports + status.openPositions.reduce((total, position) => {
    const markPriceUsd = status.markPricesByMint?.[position.mint] ?? position.entryPriceUsd;
    return total + Math.floor(position.allocatedLamports * (markPriceUsd / position.entryPriceUsd));
  }, 0));
  const exits = (status.closedTrades ?? decisions.filter((decision) => decision.type === 'paper_exit')).filter((decision) => decision.type === 'paper_exit');
  const wins = exits.filter((decision) => (decision.pnlLamports ?? 0) > 0).length;
  const duplicateSuppressed = status.duplicateSuppressed ?? decisions.filter((decision) => decision.type === 'duplicate_suppressed').length;
  const meaningful = decisions.filter((decision) => decision.type !== 'duplicate_suppressed');
  const whaleMints = status.whaleInterestMints ?? [];
  const feed = [
    { id: `scan-${status.updatedAt}`, pairId: 'scanner', at: status.updatedAt, type: 'scan_complete' as const, symbol: 'SCANNER', detail: `${meaningful.length} meaningful events · ${duplicateSuppressed} pairs already seen${whaleMints.length ? ` · ${whaleMints.length} whale interest mint(s)` : ''}` },
    ...meaningful.slice(-19).reverse().map((decision, index) => ({
      id: `${decision.pairId}-${index}`,
      pairId: decision.pairId,
      at: decision.at ?? status.updatedAt,
      type: decision.type,
      symbol: decision.symbol ?? 'MARKET',
      detail: decisionDetail(decision),
    })),
  ];
  const candidates = latestByPair(meaningful.filter((decision) => decision.type === 'paper_entry' && decision.mint && decision.symbol)).slice(-8).reverse().map((decision) => ({
    pairId: decision.pairId, mint: decision.mint!, symbol: decision.symbol!, score: decision.score ?? 0,
    source: decision.source ?? 'scanner', at: decision.at ?? status.updatedAt,
    whaleInterest: whaleMints.includes(decision.mint!),
  }));
  const rejections = summarizeRejections(meaningful, status.updatedAt);
  return {
    mode: 'paper', updatedAt: status.updatedAt,
    marketContext: status.marketContext,
    summary: { availableSol: sol(status.availableLamports), realizedPnlSol: sol(status.realizedPnlLamports), openPositions: positions.length, totalEquitySol, winRate: exits.length ? round((wins / exits.length) * 100, 1) : 0 },
    scanner: { strategy: 'quant-momentum', status: 'online', lastScanAt: status.updatedAt, scans: new Set(decisions.map((decision) => decision.at ?? status.updatedAt)).size, candidatesFound: candidates.length, duplicateSuppressed, meaningfulEvents: meaningful.length, consecutiveLosses: 0, whaleInterestMints: status.whaleInterestMints ?? [] },
    positions,
    equity: (status.equityHistory?.length ? status.equityHistory.slice(-500) : [{ at: status.updatedAt, equityLamports: status.availableLamports + status.openPositions.reduce((total, position) => total + position.entryCostLamports, 0) }]).map((point) => ({ at: point.at, equitySol: sol(point.equityLamports) })),
    feed,
    candidates,
    rejections,
    closedTrades: exits.slice(-500).reverse().map((decision) => ({
      pairId: decision.pairId, mint: decision.mint ?? 'unknown', symbol: decision.symbol ?? 'UNKNOWN',
      reason: decision.reason ?? 'exit', source: decision.source ?? 'scanner', at: decision.at ?? status.updatedAt, pnlSol: sol(decision.pnlLamports ?? 0), heldMinutes: tradeDurationMinutes(decision.openedAt, decision.at ?? status.updatedAt),
    })),
    providerHealth: status.providerHealth,
  };
}

/** Clusters raw providerErrors strings into per-provider health. Pure + testable.
 * Expected prefixes: `enrichment:` (age prefilter skip), `exact_pair_seed:`,
 * `discovery_enrichment:`, `exact_pool_quote:`, `discovery:`, `websocket:`.
 * Returns a stable list (Triton, SolanaWS) so the UI can render a fixed health grid. */
export function summarizeProviderHealth(providerErrors: string[]): NonNullable<DashboardData['providerHealth']> {
  // TRITON-ONLY: Solana WS lane is verwijderd (useSolanaWs=false). Enige provider
  // is TRITON (RPC/geyser/Vixen/Titan/DAS). Geen BIRDEYE/GECKO/SOLANA-WS meer.
  const grid: Array<{ provider: string; status: 'ok' | 'degraded' | 'down'; errorHint?: string }> = [
    { provider: 'TRITON', status: 'ok' },
  ];
  type Row = { status: 'ok' | 'degraded' | 'down'; count: number; lastError?: string };
  const map: Record<string, Row> = {
    'TRITON': { status: 'ok', count: 0 },
  };
  const classify = (error: string): { provider: string; severity: 'down' | 'degraded' } | null => {
    const e = error.toLowerCase();
    if (e.includes('triton') || e.includes('vixen') || e.includes('geyser') || e.includes('rpc') || e.includes('titan') || e.includes('das')) {
      return { provider: 'TRITON', severity: /stream error|subscribe failed|401|403/.test(e) ? 'down' : 'degraded' };
    }
    return null;
  };
  for (const error of providerErrors) {
    const hit = classify(error);
    if (!hit) continue;
    const row = map[hit.provider];
    row.count += 1;
    row.lastError = row.lastError ?? error.slice(0, 120);
    // degraded (429/errors) is milder than down (401/403/reconnect failure)
    if (hit.severity === 'down') row.status = 'down';
    else if (row.status !== 'down') row.status = 'degraded';
  }
  return grid.map((row) => {
    const m = map[row.provider];
    if (m.count === 0) return { ...row, status: 'ok' as const };
    return { ...row, status: m.status, errorHint: m.lastError ? `LAST ${m.lastError.slice(0, 40)}` : undefined };
  });
}

function tradeDurationMinutes(openedAt: string | undefined, closedAt: string): number | undefined {
  if (!openedAt) return undefined;
  const duration = Date.parse(closedAt) - Date.parse(openedAt);
  return Number.isFinite(duration) && duration >= 0 ? Math.floor(duration / 60_000) : undefined;
}
function latestByPair(decisions: DashboardDecision[]): DashboardDecision[] { const byPair = new Map<string, DashboardDecision>(); for (const decision of decisions) byPair.set(decision.pairId, decision); return Array.from(byPair.values()); }
function earliestIso(left: string | undefined, right: string | undefined): string | undefined {
  const leftMs = left ? Date.parse(left) : Number.NaN;
  const rightMs = right ? Date.parse(right) : Number.NaN;
  if (Number.isFinite(leftMs) && Number.isFinite(rightMs)) return leftMs <= rightMs ? left : right;
  if (Number.isFinite(leftMs)) return left;
  if (Number.isFinite(rightMs)) return right;
  return undefined;
}
function latestIso(left: string | undefined, right: string | undefined): string | undefined {
  const leftMs = left ? Date.parse(left) : Number.NaN;
  const rightMs = right ? Date.parse(right) : Number.NaN;
  if (Number.isFinite(leftMs) && Number.isFinite(rightMs)) return leftMs >= rightMs ? left : right;
  if (Number.isFinite(leftMs)) return left;
  if (Number.isFinite(rightMs)) return right;
  return undefined;
}
function summarizeRejections(decisions: DashboardDecision[], fallbackAt: string): DashboardData['rejections'] {
  const groups = new Map<string, DashboardData['rejections'][number]>();
  for (const decision of decisions) {
    if (decision.type !== 'rejected' || !decision.mint) continue;
    const reason = decision.reason ?? 'rejected';
    const rejectionClass = decision.rejectionClass ?? legacyRejectionClass(reason);
    const key = `${decision.pairId}:${rejectionClass}:${reason}`;
    const previous = groups.get(key);
    const candidateEvaluatedAt = decision.evaluatedAt ?? decision.at ?? fallbackAt;
    const firstEvaluatedAt = earliestIso(previous?.firstEvaluatedAt, candidateEvaluatedAt) ?? candidateEvaluatedAt;
    const lastEvaluatedAt = latestIso(previous?.lastEvaluatedAt, candidateEvaluatedAt) ?? candidateEvaluatedAt;
    const pairCreatedAt = previous?.pairCreatedAt ?? decision.pairCreatedAt;
    const firstSeenAt = earliestIso(previous?.firstSeenAt, decision.firstSeenAt);
    const observedAt = latestIso(previous?.observedAt, decision.observedAt);
    const createdMs = pairCreatedAt ? Date.parse(pairCreatedAt) : Number.NaN;
    const firstSeenMs = firstSeenAt ? Date.parse(firstSeenAt) : Number.NaN;
    const detectionDelayMs = Number.isFinite(createdMs) && Number.isFinite(firstSeenMs)
      ? Math.max(0, firstSeenMs - createdMs)
      : previous?.detectionDelayMs ?? decision.detectionDelayMs;
    groups.set(key, {
      pairId: decision.pairId,
      mint: decision.mint,
      symbol: decision.symbol ?? 'UNKNOWN',
      reason,
      rejectionClass,
      score: decision.score ?? 0,
      source: decision.source ?? 'scanner',
      at: lastEvaluatedAt,
      evaluatedAt: lastEvaluatedAt,
      lastEvaluatedAt,
      firstEvaluatedAt,
      occurrences: (previous?.occurrences ?? 0) + 1,
      ...(pairCreatedAt ? { pairCreatedAt } : {}),
      ...(firstSeenAt ? { firstSeenAt } : {}),
      ...(observedAt ? { observedAt } : {}),
      ...(typeof detectionDelayMs === 'number' ? { detectionDelayMs } : {}),
    });
  }
  return Array.from(groups.values()).sort((left, right) => Date.parse(right.at) - Date.parse(left.at)).slice(0, 8);
}

function legacyRejectionClass(reason: string): 'market' | 'risk' {
  return new Set([
    'risk_unknown_in_strict_mode', 'risk_flagged', 'duplicate_position', 'max_concurrent_positions',
    'daily_loss_limit', 'insufficient_balance', 'invalid_price', 'position_sized_to_zero', 'same_cycle_reentry',
  ]).has(reason) ? 'risk' : 'market';
}

async function serveStaticFile(response: import('node:http').ServerResponse, staticDir: string, pathname: string): Promise<boolean> {
  const root = resolve(staticDir);
  const requested = pathname === '/' ? 'index.html' : pathname.slice(1);
  const filePath = resolve(root, requested);
  const fileRelative = relative(root, filePath);
  if (!fileRelative || fileRelative.startsWith(`..${sep}`) || fileRelative === '..') return false;
  try {
    const contents = await readFile(filePath);
    response.writeHead(200, { 'content-type': contentType(filePath), 'cache-control': pathname === '/' ? 'no-store' : 'public, max-age=31536000, immutable' });
    response.end(contents);
    return true;
  } catch {
    return false;
  }
}
function contentType(filePath: string): string {
  return ({ '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' })[extname(filePath)] ?? 'application/octet-stream';
}

function json(response: import('node:http').ServerResponse, body: unknown, extraHeaders?: Record<string, string>): void {
  response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', ...extraHeaders });
  response.end(JSON.stringify(body));
}
function close(server: Server): Promise<void> { return new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())); }
function sol(value: number): number { return round(value / LAMPORTS_PER_SOL, 4); }
function round(value: number, decimals: number): number { return Number(value.toFixed(decimals)); }
function decisionDetail(decision: DashboardDecision): string {
  if (decision.type === 'paper_entry') return `entry score ${decision.score?.toFixed(1) ?? '—'}`;
  if (decision.type === 'paper_exit') return `${decision.reason ?? 'exit'} · ${formatSignedSol(decision.pnlLamports ?? 0)}`;
  if (decision.type === 'rejected') return decision.reason ?? 'rejected';
  return 'duplicate suppressed';
}
function formatSignedSol(lamports: number): string { return `${lamports >= 0 ? '+' : '−'}${Math.abs(sol(lamports)).toFixed(4)} SOL`; }
function render(status: DashboardStatus): string {
  return `<!doctype html><html><head><meta charset="utf-8"><title>Solana Paper Scanner</title></head><body><main><h1>Solana Paper Scanner</h1><p><strong>PAPER ONLY</strong> — simulated results, no wallet or real orders.</p><p>Dashboard UI assets are not built.</p><pre>${escapeHtml(JSON.stringify(toDashboardData(status), null, 2))}</pre></main></body></html>`;
}
function escapeHtml(value: string): string { return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] ?? char); }
