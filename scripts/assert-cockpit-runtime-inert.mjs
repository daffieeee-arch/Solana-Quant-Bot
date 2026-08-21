#!/usr/bin/env node
import { lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, readlinkSync, rmSync } from 'node:fs';
import { get as httpGet } from 'node:http';
import { connect as netConnect, createServer as createNetServer } from 'node:net';
import { extname, join, posix, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import ts from 'typescript';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeCockpitOutboundDenyFilter } from './write-cockpit-seccomp-filter.mjs';

const ALLOWED_MODULES = new Set(['node:fs', 'node:fs/promises', 'node:path', 'node:url']);
const ALLOWED_IMPORT_NAMES = new Map([
  ['node:fs', new Set(['accessSync', 'closeSync', 'constants', 'fstatSync', 'lstatSync', 'openSync', 'readSync', 'readdirSync'])],
  ['node:fs/promises', new Set(['readFile'])],
  ['node:path', new Set(['dirname', 'extname', 'isAbsolute', 'join', 'normalize', 'parse', 'relative', 'resolve', 'sep'])],
  ['node:url', new Set(['fileURLToPath'])],
]);
const FORBIDDEN_LOCAL = /(?:^|\/)(?:main|scanner|ledger|portfolio|engine-control|learn-controller|learning-observations|strategy[^/]*|runtime-lifecycle|zero-cost)\.js$|(?:^|\/)providers\//u;
const FORBIDDEN_IDENTIFIERS = new Set(['fetch', 'WebSocket', 'EventSource', 'XMLHttpRequest', 'require', 'eval', 'Function']);
const FORBIDDEN_PROPERTIES = new Set(['getBuiltinModule', 'binding', '_linkedBinding', 'dlopen', 'constructor', 'require', 'fetch', 'WebSocket']);
const ALLOWED_PROCESS_PROPERTIES = new Set(['argv', 'env', 'exit', 'exitCode', 'once', 'stderr', 'stdout']);

function constantString(node) {
  if (!node) return undefined;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = constantString(node.left);
    const right = constantString(node.right);
    return left === undefined || right === undefined ? undefined : left + right;
  }
  if (ts.isParenthesizedExpression(node)) return constantString(node.expression);
  return undefined;
}

function localTarget(from, specifier) {
  const base = posix.normalize(posix.join(posix.dirname(from), specifier));
  return extname(base) ? base : `${base}.js`;
}

function validNamedImport(node, allowed) {
  const clause = node.importClause;
  if (!clause || clause.isTypeOnly || clause.name || !clause.namedBindings || !ts.isNamedImports(clause.namedBindings)) return false;
  return clause.namedBindings.elements.every((element) => !element.isTypeOnly
    && !element.propertyName && allowed.has(element.name.text));
}

function validHttpImport(node) {
  const clause = node.importClause;
  if (!clause || clause.isTypeOnly || clause.name || !clause.namedBindings || !ts.isNamedImports(clause.namedBindings)) return false;
  return clause.namedBindings.elements.length === 1
    && !clause.namedBindings.elements[0].isTypeOnly
    && !clause.namedBindings.elements[0].propertyName
    && clause.namedBindings.elements[0].name.text === 'createServer';
}

export function validateCockpitGraphSources(sources, entry = 'dist/cockpit-main.js') {
  const errors = [];
  const pending = [entry];
  const seen = new Set();
  while (pending.length) {
    const path = pending.pop();
    if (seen.has(path)) continue;
    seen.add(path);
    const source = sources[path];
    if (typeof source !== 'string') { errors.push(`unapproved unresolved cockpit module:${path}`); continue; }
    if (FORBIDDEN_LOCAL.test(path) && path !== 'dist/research/phase8a-research-provider.js') errors.push(`forbidden cockpit module:${path}`);
    const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    if (file.parseDiagnostics.length) { errors.push(`unapproved unparsable cockpit module:${path}`); continue; }
    const imports = [];
    const visit = (node) => {
      if (ts.isImportDeclaration(node) || (ts.isExportDeclaration(node) && node.moduleSpecifier)) {
        if (!node.moduleSpecifier || !ts.isStringLiteral(node.moduleSpecifier)) errors.push(`dynamic cockpit module specifier:${path}`);
        else {
          const specifier = node.moduleSpecifier.text;
          imports.push({ specifier, node });
        }
      }
      if (ts.isCallExpression(node)) {
        if (node.expression.kind === ts.SyntaxKind.ImportKeyword) errors.push(`dynamic import forbidden:${path}`);
        if (ts.isIdentifier(node.expression) && node.expression.text === 'require') errors.push(`loader require forbidden:${path}`);
      }
      if (ts.isIdentifier(node) && FORBIDDEN_IDENTIFIERS.has(node.text)) errors.push(`forbidden cockpit identifier:${node.text}:${path}`);
      if (ts.isIdentifier(node) && ['process', 'globalThis', 'global'].includes(node.text)) {
        const parent = node.parent;
        const directReceiver = (ts.isPropertyAccessExpression(parent) || ts.isElementAccessExpression(parent))
          && parent.expression === node;
        if (!directReceiver) errors.push(`forbidden global capability alias:${node.text}:${path}`);
      }
      if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
        const property = ts.isPropertyAccessExpression(node) ? node.name.text : constantString(node.argumentExpression);
        if (property === undefined
          && ts.isIdentifier(node.expression)
          && ['globalThis', 'global', 'process'].includes(node.expression.text)) errors.push(`dynamic computed property forbidden:${path}`);
        else if (property !== undefined && FORBIDDEN_PROPERTIES.has(property)) errors.push(`forbidden cockpit property:${property}:${path}`);
        if (ts.isIdentifier(node.expression) && node.expression.text === 'process'
          && (property === undefined || !ALLOWED_PROCESS_PROPERTIES.has(property))) errors.push(`forbidden process property:${String(property)}:${path}`);
        if ((ts.isIdentifier(node.expression) && (node.expression.text === 'globalThis' || node.expression.text === 'global'))
          && property !== undefined) errors.push(`forbidden global capability:${property}:${path}`);
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    for (const { specifier, node } of imports) {
      if (specifier.startsWith('.')) {
        const target = localTarget(path, specifier);
        if (FORBIDDEN_LOCAL.test(target) && target !== 'dist/research/phase8a-research-provider.js') errors.push(`forbidden cockpit reachability:${target}`);
        pending.push(target);
      } else if (specifier === 'node:http') {
        if (path !== 'dist/cockpit-server.js' || !ts.isImportDeclaration(node) || !validHttpImport(node)) errors.push(`unapproved node:http capability:${path}`);
      } else if (ALLOWED_MODULES.has(specifier)) {
        if (!ts.isImportDeclaration(node) || !validNamedImport(node, ALLOWED_IMPORT_NAMES.get(specifier))) errors.push(`unapproved read-only builtin import:${specifier}:${path}`);
      } else {
        errors.push(`unapproved cockpit dependency:${specifier}:${path}`);
      }
    }
  }
  return [...new Set(errors)].sort();
}

function collectJavascript(root) {
  const sources = {};
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolute = resolve(directory, entry.name);
      if (entry.isSymbolicLink() || lstatSync(absolute).isSymbolicLink()) throw new Error(`forbidden cockpit source symlink:${absolute}`);
      if (entry.isDirectory()) walk(absolute);
      else if (entry.isFile() && entry.name.endsWith('.js')) sources[posix.relative(process.cwd().replaceAll('\\', '/'), absolute.replaceAll('\\', '/'))] = readFileSync(absolute, 'utf8');
    }
  };
  walk(root);
  return sources;
}

function reserveLoopbackPort() {
  return new Promise((resolvePromise, reject) => {
    const server = createNetServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') { server.close(); reject(new Error('cockpit port reservation failed')); return; }
      server.close((error) => error ? reject(error) : resolvePromise(address.port));
    });
  });
}

function request(port, path) {
  return new Promise((resolvePromise, reject) => {
    const request_ = httpGet({ host: '127.0.0.1', port, path, timeout: 2_000, agent: false, headers: { connection: 'close' } }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolvePromise({ status: response.statusCode, body: Buffer.concat(chunks).toString('utf8') }));
    });
    request_.once('error', reject);
    request_.once('timeout', () => request_.destroy(new Error('cockpit request timeout')));
  });
}

function waitForStarted(child, output) {
  return new Promise((resolvePromise, reject) => {
    const timer = setTimeout(() => reject(new Error('cockpit start timeout')), 10_000);
    child.stdout.on('data', (chunk) => {
      output.stdout += chunk.toString('utf8');
      if (output.stdout.includes('"event":"cockpit_started"')) { clearTimeout(timer); resolvePromise(); }
    });
    child.stderr.on('data', (chunk) => { output.stderr += chunk.toString('utf8'); });
    child.once('exit', (code, signal) => { clearTimeout(timer); reject(new Error(`cockpit exited before ready:${String(code)}:${String(signal)}`)); });
  });
}

function waitForExit(child) {
  return new Promise((resolvePromise, reject) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('cockpit graceful shutdown timeout')); }, 5_000);
    child.once('exit', (code, signal) => { clearTimeout(timer); code === 0 && signal === null ? resolvePromise() : reject(new Error(`cockpit shutdown failed:${String(code)}:${String(signal)}`)); });
  });
}

function assertPortClosed(port) {
  return new Promise((resolvePromise, reject) => {
    const socket = netConnect({ host: '127.0.0.1', port });
    socket.once('connect', () => { socket.destroy(); reject(new Error('cockpit listener survived shutdown')); });
    socket.once('error', (error) => error.code === 'ECONNREFUSED' ? resolvePromise() : reject(error));
  });
}

async function assertRuntime(root) {
  const scratch = mkdtempSync(join(tmpdir(), 'phase8c-cockpit-runtime-'));
  const filter = join(scratch, 'outbound-deny.bpf');
  const launcher = join(scratch, 'launcher');
  const runtimeCwd = join(scratch, 'cwd');
  mkdirSync(runtimeCwd);
  let child;
  try {
    await writeCockpitOutboundDenyFilter(filter);
    await buildResearchSeccompLauncher(launcher);
    const outboundProbe = spawnSync(launcher, [filter, process.execPath, '--eval', `
      const net = require('node:net');
      const socket = net.connect({host:'127.0.0.1',port:9});
      socket.on('error', (error) => process.exit(error.code === 'EPERM' ? 0 : 2));
      setTimeout(() => process.exit(3), 1000);
    `], { encoding: 'utf8' });
    if (outboundProbe.status !== 0 || outboundProbe.stderr !== '') throw new Error(outboundProbe.stderr || 'cockpit outbound seccomp probe failed');
    const port = await reserveLoopbackPort();
    const env = { ...process.env, COCKPIT_BIND_HOST: '127.0.0.1', COCKPIT_PORT: String(port), NODE_NO_WARNINGS: '1' };
    delete env.PHASE8A_RESEARCH_OUTPUT_DIR;
    const output = { stdout: '', stderr: '' };
    child = spawn(launcher, [filter, process.execPath, resolve(root, 'dist/cockpit-main.js')], {
      cwd: runtimeCwd,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    await waitForStarted(child, output);
    const health = await request(port, '/healthz');
    const readiness = await request(port, '/readyz');
    if (health.status !== 200 || health.body !== 'ok') throw new Error('cockpit health probe failed');
    if (readiness.status !== 503 || JSON.parse(readiness.body).status !== 'UNAVAILABLE') throw new Error('cockpit unavailable readiness drift');
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50));
    const socketInodes = new Set(readdirSync(`/proc/${child.pid}/fd`).map((name) => {
      try { return readlinkSync(`/proc/${child.pid}/fd/${name}`).match(/^socket:\[(\d+)\]$/)?.[1] ?? ''; } catch { return ''; }
    }).filter(Boolean));
    const inet = [];
    for (const table of ['tcp', 'tcp6', 'udp', 'udp6']) {
      const lines = readFileSync(`/proc/${child.pid}/net/${table}`, 'utf8').trim().split('\n').slice(1);
      for (const line of lines) {
        const fields = line.trim().split(/\s+/);
        if (socketInodes.has(fields[9])) inet.push({ table, local: fields[1], state: fields[3], inode: fields[9] });
      }
    }
    const expectedPort = port.toString(16).toUpperCase().padStart(4, '0');
    if (inet.length !== 1 || inet[0].table !== 'tcp' || inet[0].state !== '0A' || !inet[0].local.endsWith(`:${expectedPort}`)) {
      throw new Error(`cockpit INET socket drift:${JSON.stringify(inet)}`);
    }
    if (readdirSync(runtimeCwd).length !== 0) throw new Error('cockpit wrote runtime cwd');
    child.kill('SIGTERM');
    await waitForExit(child);
    child = undefined;
    await assertPortClosed(port);
    if (output.stderr !== '') throw new Error(`cockpit stderr drift:${output.stderr}`);
  } finally {
    if (child && child.exitCode === null) child.kill('SIGKILL');
    rmSync(scratch, { recursive: true, force: true });
  }
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === fileURLToPath(import.meta.url)) {
  try {
    const root = resolve(process.cwd(), 'dist');
    const errors = validateCockpitGraphSources(collectJavascript(root));
    if (errors.length) throw new Error(errors.join('\n'));
    await assertRuntime(process.cwd());
    process.stdout.write('Cockpit-only compiled import graph/runtime/seccomp PASS\n');
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
