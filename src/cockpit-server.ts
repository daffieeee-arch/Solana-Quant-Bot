import { readFile } from 'node:fs/promises';
import { createServer, type ServerResponse } from 'node:http';
import { extname, relative, resolve, sep } from 'node:path';
import type { ResearchDashboardProvider, ResearchPageQuery } from './research/phase8a-research-contract.js';

export type CockpitServer = Readonly<{ port: number; close(): Promise<void> }>;
export type CockpitServerOptions = Readonly<{
  bindHost: string;
  port: number;
  staticDir: string;
  indexFile?: 'index.html' | 'cockpit.html';
  researchProvider?: ResearchDashboardProvider;
}>;

const RESEARCH_PREFIX = '/api/research/pilot-a/';
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_CURSOR = 1_000_000;
const MAX_LIMIT = 100;

function headers(contentType: string, length: number, cacheControl = 'no-store'): Record<string, string> {
  return {
    'content-type': contentType,
    'content-length': String(length),
    'cache-control': cacheControl,
    'x-content-type-options': 'nosniff',
    'content-security-policy': "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  };
}

function send(response: ServerResponse, method: string, status: number, bytes: Buffer, contentType: string, cacheControl?: string): void {
  response.writeHead(status, headers(contentType, bytes.length, cacheControl));
  response.end(method === 'HEAD' ? undefined : bytes);
}

function json(response: ServerResponse, method: string, status: number, value: unknown): void {
  const bytes = Buffer.from(JSON.stringify(value));
  if (bytes.length > MAX_RESPONSE_BYTES) throw new Error('cockpit_response_too_large');
  send(response, method, status, bytes, 'application/json; charset=utf-8');
}

function error(response: ServerResponse, method: string, status: number, value: 'UNAVAILABLE' | 'INVALID_REQUEST' | 'INTERNAL_ERROR'): void {
  json(response, method, status, { schemaVersion: 'PHASE8A_RESEARCH_API_ERROR_1', status: value });
}

function page(url: URL): ResearchPageQuery | undefined {
  const allowed = new Set(['cursor', 'limit']);
  for (const key of url.searchParams.keys()) if (!allowed.has(key)) return undefined;
  if (url.searchParams.getAll('cursor').length > 1 || url.searchParams.getAll('limit').length > 1) return undefined;
  const cursorText = url.searchParams.get('cursor') ?? '0';
  const limitText = url.searchParams.get('limit') ?? '50';
  if (!/^(?:0|[1-9]\d*)$/.test(cursorText) || !/^[1-9]\d*$/.test(limitText)) return undefined;
  const cursor = Number(cursorText);
  const limit = Number(limitText);
  if (!Number.isSafeInteger(cursor) || cursor > MAX_CURSOR || !Number.isSafeInteger(limit) || limit > MAX_LIMIT) return undefined;
  return { cursor, limit };
}

async function research(response: ServerResponse, method: string, url: URL, provider: ResearchDashboardProvider | undefined): Promise<void> {
  if (!provider) { error(response, method, 404, 'UNAVAILABLE'); return; }
  try {
    const route = url.pathname.slice(RESEARCH_PREFIX.length);
    let value: unknown;
    if (route === 'events' || route === 'quarantines') {
      const query = page(url);
      if (!query) { error(response, method, 400, 'INVALID_REQUEST'); return; }
      value = route === 'events' ? await provider.getEvents(query) : await provider.getQuarantines(query);
    } else {
      if ([...url.searchParams.keys()].length) { error(response, method, 400, 'INVALID_REQUEST'); return; }
      if (route === 'summary') value = await provider.getSummary();
      else if (route === 'provenance') value = await provider.getProvenance();
      else if (route === 'metrics') value = await provider.getMetrics();
      else if (route === 'metrics/prometheus') {
        const bytes = Buffer.from(await provider.getPrometheus());
        if (bytes.length > MAX_RESPONSE_BYTES) throw new Error('cockpit_response_too_large');
        send(response, method, 200, bytes, 'text/plain; version=0.0.4; charset=utf-8');
        return;
      } else { error(response, method, 404, 'UNAVAILABLE'); return; }
    }
    if (typeof value !== 'object' || value === null || Array.isArray(value)
      || typeof (value as Record<string, unknown>).schemaVersion !== 'string') throw new Error('invalid_research_response');
    json(response, method, 200, value);
  } catch {
    error(response, method, 500, 'INTERNAL_ERROR');
  }
}

async function staticFile(response: ServerResponse, method: string, staticDir: string, indexFile: 'index.html' | 'cockpit.html', pathname: string): Promise<boolean> {
  if (pathname !== '/' && !pathname.startsWith('/assets/')) return false;
  const root = resolve(staticDir);
  const file = resolve(root, pathname === '/' ? indexFile : pathname.slice(1));
  const child = relative(root, file);
  if (!child || child === '..' || child.startsWith(`..${sep}`)) return false;
  try {
    const bytes = await readFile(file);
    const contentType = ({ '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' })[extname(file)] ?? 'application/octet-stream';
    send(response, method, 200, bytes, contentType, pathname === '/' ? 'no-store' : 'public, max-age=31536000, immutable');
    return true;
  } catch { return false; }
}

function createBoundedClose(server: import('node:http').Server): () => Promise<void> {
  let closePromise: Promise<void> | undefined;
  return () => {
    if (closePromise) return closePromise;
    closePromise = new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => server.closeAllConnections(), 2_000);
      server.close((failure) => {
        clearTimeout(timer);
        if (failure) reject(failure);
        else resolvePromise();
      });
      server.closeIdleConnections();
    });
    return closePromise;
  };
}

export async function createCockpitServer(options: CockpitServerOptions): Promise<CockpitServer> {
  if (options.bindHost !== '127.0.0.1' && options.bindHost !== '::1') throw new Error('COCKPIT_LOOPBACK_ONLY');
  const server = createServer(async (request, response) => {
    const method = request.method ?? '';
    if (method !== 'GET' && method !== 'HEAD') {
      response.setHeader('allow', 'GET, HEAD');
      send(response, method, 405, Buffer.from('Method not allowed'), 'text/plain; charset=utf-8');
      return;
    }
    let url: URL;
    try { url = new URL(request.url ?? '/', 'http://cockpit.invalid'); }
    catch { send(response, method, 400, Buffer.from('Bad request'), 'text/plain; charset=utf-8'); return; }
    if (url.pathname === '/healthz') { send(response, method, 200, Buffer.from('ok'), 'text/plain; charset=utf-8'); return; }
    if (url.pathname === '/readyz') {
      json(response, method, options.researchProvider ? 200 : 503, {
        schemaVersion: 'PHASE8C_COCKPIT_READINESS_1',
        status: options.researchProvider ? 'READY' : 'UNAVAILABLE',
      });
      return;
    }
    if (url.pathname.startsWith(RESEARCH_PREFIX)) { await research(response, method, url, options.researchProvider); return; }
    if (await staticFile(response, method, options.staticDir, options.indexFile ?? 'index.html', url.pathname)) return;
    send(response, method, 404, Buffer.from('Not found'), 'text/plain; charset=utf-8');
  });
  await new Promise<void>((resolvePromise, reject) => {
    server.once('error', reject);
    server.listen(options.port, options.bindHost, () => resolvePromise());
  });
  const address = server.address();
  if (!address || typeof address === 'string') { await createBoundedClose(server)(); throw new Error('cockpit_listener_unavailable'); }
  return Object.freeze({ port: address.port, close: createBoundedClose(server) });
}
