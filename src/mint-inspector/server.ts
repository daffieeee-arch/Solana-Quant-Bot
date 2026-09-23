import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readdir, realpath } from 'node:fs/promises';
import { isAbsolute, join, resolve, extname } from 'node:path';
import { readBoundedFile } from '../acquisition-monitor/reader.js';
import { INPUT_NAMES, MAX_RESPONSE_BYTES, hash, object, parseInspection, type InputHashes, type InputName } from './contract.js';

const sha256 = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
const fail = (): never => { throw new Error('INSPECTOR_INPUT_UNAVAILABLE'); };
async function directory(path: string): Promise<string> {
  if (!isAbsolute(path) || resolve(path) !== path || await realpath(path) !== path) fail();
  return path;
}
/** Pin bytes before publishing. No path supplied by an HTTP request is opened. */
async function registeredFile(root: string, relative: unknown, limit: number): Promise<Buffer> {
  if (typeof relative !== 'string' || relative.length > 1024 || !relative.split('/').every(part => /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(part))) fail();
  const file = join(root, relative as string);
  if (await realpath(file) !== file) fail(); // includes ancestor symlinks
  return readBoundedFile(file, limit);
}
export async function loadInspection(root: string, registryPath: string) {
  await directory(root);
  const registry = object(JSON.parse((await registeredFile(root, registryPath, 16384)).toString('utf8')));
  if (registry.schema !== 'OF1_MINT_INSPECTOR_REGISTRY_1') fail();
  const inputs = object(registry.inputs), hashes = {} as InputHashes;
  if (Object.keys(inputs).sort().join(',') !== [...INPUT_NAMES].sort().join(',')) fail();
  const bytes = {} as Record<InputName, Buffer>;
  for (const name of INPUT_NAMES) {
    const registered = object(inputs[name]); hash(registered.sha256);
    bytes[name] = await registeredFile(root, registered.path, MAX_RESPONSE_BYTES);
    if (sha256(bytes[name]) !== registered.sha256) fail();
    hashes[name] = registered.sha256;
  }
  const timeline = JSON.parse(bytes.timeline.toString('utf8'));
  const lifecycle = JSON.parse(bytes.lifecycle.toString('utf8'));
  const collection = object(JSON.parse(bytes.collection.toString('utf8')));
  if (collection.schema !== 'OF1_BATCH_COLLECTION_1' || collection.plan_sha256 !== hashes.plan
    || collection.research_ready !== false || collection.state !== 'COMPLETE') fail();
  const inspection = parseInspection({ schema: 'OF1_MINT_INSPECTOR_1', state: 'READY', inputs: hashes, timeline, lifecycle });
  const response = Buffer.from(JSON.stringify(inspection));
  if (response.length > MAX_RESPONSE_BYTES) fail();
  return { inspection, response, bytes };
}

export async function createMintInspector(options: { dataRoot: string; registryPath: string; staticDirectory: string; port: number }) {
  if (!Number.isInteger(options.port) || options.port < 0 || options.port > 65535) fail();
  const input = await loadInspection(options.dataRoot, options.registryPath);
  const staticRoot = await directory(options.staticDirectory);
  const routes = new Map<string, { bytes: Buffer; type: string }>();
  routes.set('/api/inspection', { bytes: input.response, type: 'application/json' });
  for (const name of INPUT_NAMES) routes.set(`/evidence/${name}.json`, { bytes: input.bytes[name], type: 'application/json' });
  routes.set('/', { bytes: await registeredFile(staticRoot, 'inspector.html', 65536), type: 'text/html' });
  const assets = await readdir(join(staticRoot, 'assets'));
  if (assets.length > 16) fail();
  let total = 0;
  for (const name of assets) {
    if (!/^[A-Za-z0-9_-]+\.(js|css)$/.test(name)) fail();
    const bytes = await registeredFile(staticRoot, `assets/${name}`, 4 * 1024 * 1024);
    total += bytes.length; if (total > 8 * 1024 * 1024) fail();
    routes.set(`/assets/${name}`, { bytes, type: extname(name) === '.js' ? 'text/javascript' : 'text/css' });
  }
  let port = options.port;
  const allowedHost = (host: string | undefined) => host === `127.0.0.1:${port}` || host === `localhost:${port}`;
  const server = createServer({ maxHeaderSize: 8192, headersTimeout: 5000, requestTimeout: 5000, keepAliveTimeout: 1000 }, (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
    res.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    const deny = (status: number, reason: string) => { res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end(reason); };
    if (!allowedHost(req.headers.host) || (req.headers.origin !== undefined && req.headers.origin !== `http://${req.headers.host}`)
      || req.headers['sec-fetch-site'] === 'cross-site') { deny(403, 'LOCAL_ORIGIN_REQUIRED'); return; }
    if (req.method !== 'GET') { res.setHeader('Allow', 'GET'); deny(405, 'READ_ONLY'); return; }
    // Exact allowlist: no URL normalization, query, fragment, percent-decoding or filesystem mapping.
    const asset = routes.get(req.url ?? '');
    if (!asset) { deny(404, 'UNAVAILABLE'); return; }
    res.writeHead(200, { 'Content-Type': `${asset.type}; charset=utf-8`, 'Content-Length': String(asset.bytes.length) });
    res.end(asset.bytes);
  });
  server.maxConnections = 16; server.maxRequestsPerSocket = 32; server.setTimeout(5000, socket => socket.destroy());
  await new Promise<void>((done, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', done); });
  const address = server.address(); if (!address || typeof address === 'string') fail();
  port = (address as { port: number }).port;
  let closing: Promise<void> | undefined;
  return { port, close(): Promise<void> {
    closing ??= new Promise((done, reject) => {
      const timer = setTimeout(() => server.closeAllConnections(), 2000);
      server.close(error => { clearTimeout(timer); error ? reject(error) : done(); }); server.closeIdleConnections();
    });
    return closing;
  } };
}
