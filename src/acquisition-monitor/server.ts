import { createServer, type ServerResponse } from 'node:http';
import { extname, resolve } from 'node:path';
import { createSnapshotReader, MonitorReadError, readBoundedFile } from './reader.js';

export type AcquisitionMonitorServer = Readonly<{ port: number; close(): Promise<void> }>;
export type AcquisitionMonitorOptions = Readonly<{
  snapshotsDirectory: string;
  staticDirectory: string;
  port: number;
  bindHost?: '127.0.0.1' | '::1';
}>;

function send(response: ServerResponse, status: number, bytes: Buffer, contentType: string): void {
  response.writeHead(status, {
    'content-type': contentType,
    'content-length': String(bytes.length),
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'content-security-policy': "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  });
  response.end(bytes);
}

function json(response: ServerResponse, status: number, value: unknown): void {
  send(response, status, Buffer.from(JSON.stringify(value)), 'application/json; charset=utf-8');
}

export async function createAcquisitionMonitor(options: AcquisitionMonitorOptions): Promise<AcquisitionMonitorServer> {
  const host = options.bindHost ?? '127.0.0.1';
  if (host !== '127.0.0.1' && host !== '::1') throw new Error('MONITOR_LOOPBACK_ONLY');
  if (!Number.isSafeInteger(options.port) || options.port < 0 || options.port > 65535) throw new Error('INVALID_MONITOR_PORT');
  const reader = createSnapshotReader(options.snapshotsDirectory);
  const server = createServer(async (request, response) => {
    if (request.method !== 'GET') {
      response.setHeader('allow', 'GET');
      json(response, 405, { status: 'READ_ONLY', reason: 'GET_ONLY' });
      return;
    }
    try {
      const url = new URL(request.url ?? '/', 'http://monitor.invalid');
      if (url.search || url.hash) { json(response, 400, { status: 'INVALID_REQUEST' }); return; }
      if (url.pathname === '/healthz') { json(response, 200, { status: 'READ_ONLY_MONITOR' }); return; }
      if (url.pathname === '/api/acquisition/runs') {
        json(response, 200, { schema_version: 'OF1_MONITOR_HTTP_1', read_at_unix_ms: Date.now(), runs: await reader.runs() });
        return;
      }
      const verification = /^\/api\/acquisition\/runs\/([a-f0-9]{64})\/verification$/.exec(url.pathname);
      if (verification) {
        json(response, 200, await reader.verification(verification[1]));
        return;
      }
      const artifact = /^\/api\/acquisition\/runs\/([a-f0-9]{64})\/artifacts\/([a-z0-9-]{1,80})$/.exec(url.pathname);
      if (artifact) {
        send(response, 200, await reader.artifact(artifact[1], artifact[2]), 'application/json; charset=utf-8');
        return;
      }
      if (url.pathname === '/' || /^\/assets\/[A-Za-z0-9_.-]+$/.test(url.pathname)) {
        const path = resolve(options.staticDirectory, url.pathname === '/' ? 'monitor.html' : url.pathname.slice(1));
        const type = ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' })[extname(path)];
        if (type) {
          try { send(response, 200, await readBoundedFile(path, 4 * 1024 * 1024), `${type}; charset=utf-8`); return; }
          catch { /* A missing built asset is a bounded 404, never a fallback runtime. */ }
        }
      }
      json(response, 404, { status: 'UNAVAILABLE', reason: 'NOT_FOUND' });
    } catch (error) {
      const reason = error instanceof MonitorReadError ? error.reason : 'MONITOR_READ_FAILED';
      json(response, reason === 'ARTIFACT_NOT_FOUND' ? 404 : 503, { status: 'UNAVAILABLE', reason });
    }
  });
  await new Promise<void>((resolvePromise, reject) => {
    server.once('error', reject);
    server.listen(options.port, host, resolvePromise);
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('MONITOR_LISTENER_UNAVAILABLE');
  let closing: Promise<void> | undefined;
  return Object.freeze({
    port: address.port,
    close() {
      closing ??= new Promise<void>((resolvePromise, reject) => {
        const timeout = setTimeout(() => server.closeAllConnections(), 2000);
        server.close((error) => { clearTimeout(timeout); error ? reject(error) : resolvePromise(); });
        server.closeIdleConnections();
      });
      return closing;
    },
  });
}
