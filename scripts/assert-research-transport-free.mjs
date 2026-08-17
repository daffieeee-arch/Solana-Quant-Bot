import { spawnSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import { lstat, mkdtemp, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';

const root = resolve(import.meta.dirname, '..');
const allowedModules = new Set([
  'bs58',
  'fs-ext',
  'node:crypto',
  'node:fs',
  'node:fs/promises',
  'node:path',
  'node:url',
]);
const forbiddenIdentifiers = new Set([
  '_linkedBinding', 'binding', 'constructor', 'dlopen', 'eval', 'fetch', 'Function',
  'getBuiltinModule', 'global', 'globalThis', 'mainModule', 'Reflect', 'Proxy',
  'WebSocket', 'EventSource', 'XMLHttpRequest', 'navigator',
]);
const forbiddenProperties = new Set([
  '_linkedBinding', 'binding', 'constructor', 'dlopen', 'getBuiltinModule',
  'getOwnPropertyDescriptor', 'getOwnPropertyDescriptors', 'mainModule', 'require',
  'sendBeacon', '__proto__', 'fetch', 'WebSocket', 'EventSource', 'XMLHttpRequest',
]);
const allowedDirectProcessProperties = new Set([
  'argv', 'env', 'execPath', 'exit', 'exitCode', 'stderr', 'stdout',
]);

async function javascriptFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`forbidden research capability: symlink ${path}`);
    if (entry.isDirectory()) files.push(...await javascriptFiles(path));
    else if (entry.isFile() && entry.name.endsWith('.js')) files.push(path);
  }
  return files.sort();
}

async function resolveLocalModule(fromFile, specifier) {
  const base = resolve(dirname(fromFile), specifier);
  const candidates = extname(base) ? [base] : [`${base}.js`, join(base, 'index.js')];
  for (const candidate of candidates) {
    try {
      const observed = await lstat(candidate);
      if (observed.isSymbolicLink() || !observed.isFile()) {
        throw new Error(`forbidden research capability: non-regular local module ${candidate}`);
      }
      return candidate;
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
  throw new Error(`forbidden research capability: unresolved local module ${specifier} from ${fromFile}`);
}

function literalModuleSpecifier(node, file) {
  if (node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))) return node.text;
  throw new Error(`forbidden research capability: non-literal module load in ${file}`);
}

function constantString(node, checker, seen = new Set()) {
  if (!node) return undefined;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isIdentifier(node)) {
    const symbol = checker.getSymbolAtLocation(node);
    if (!symbol || seen.has(symbol)) return undefined;
    const declaration = symbol.valueDeclaration;
    if (!declaration
      || !ts.isVariableDeclaration(declaration)
      || !declaration.initializer
      || !ts.isVariableDeclarationList(declaration.parent)
      || (declaration.parent.flags & ts.NodeFlags.Const) === 0) return undefined;
    const nextSeen = new Set(seen);
    nextSeen.add(symbol);
    return constantString(declaration.initializer, checker, nextSeen);
  }
  if (ts.isParenthesizedExpression(node)) return constantString(node.expression, checker, seen);
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = constantString(node.left, checker, seen);
    const right = constantString(node.right, checker, seen);
    return left === undefined || right === undefined ? undefined : left + right;
  }
  if (ts.isTemplateExpression(node)) {
    let result = node.head.text;
    for (const span of node.templateSpans) {
      const expression = constantString(span.expression, checker, seen);
      if (expression === undefined) return undefined;
      result += expression + span.literal.text;
    }
    return result;
  }
  return undefined;
}

function propertyName(node, checker) {
  if (ts.isPropertyAccessExpression(node)) return node.name.text;
  if (ts.isElementAccessExpression(node)) return constantString(node.argumentExpression, checker);
  return undefined;
}

function directProcessProperty(node, checker) {
  if ((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node))
    && ts.isIdentifier(node.expression)
    && node.expression.text === 'process') return propertyName(node, checker);
  return null;
}

async function assertStaticGraph(directory) {
  const pending = await javascriptFiles(directory);
  const seen = new Set();
  while (pending.length > 0) {
    const file = pending.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const program = ts.createProgram([file], {
      allowJs: true,
      checkJs: false,
      module: ts.ModuleKind.ESNext,
      noResolve: true,
      skipLibCheck: true,
      target: ts.ScriptTarget.Latest,
    });
    const sourceFile = program.getSourceFile(file);
    if (!sourceFile) throw new Error(`forbidden research capability: unreadable built module ${file}`);
    const checker = program.getTypeChecker();
    if (sourceFile.parseDiagnostics.length > 0) {
      throw new Error(`forbidden research capability: unparsable built module ${file}`);
    }
    const specifiers = [];
    function visit(node) {
      if (ts.isIdentifier(node) && forbiddenIdentifiers.has(node.text)) {
        throw new Error(`forbidden research capability: identifier ${node.text} in ${file}`);
      }
      if (ts.isIdentifier(node) && node.text === 'process') {
        const parent = node.parent;
        const isDirectPropertyBase = (ts.isPropertyAccessExpression(parent) || ts.isElementAccessExpression(parent))
          && parent.expression === node;
        if (!isDirectPropertyBase) {
          throw new Error(`forbidden research capability: aliased or destructured process in ${file}`);
        }
      }
      const processProperty = directProcessProperty(node, checker);
      if (processProperty === undefined || (processProperty !== null && !allowedDirectProcessProperties.has(processProperty))) {
        throw new Error(`forbidden research capability: process property ${String(processProperty)} in ${file}`);
      }
      const accessedProperty = propertyName(node, checker);
      if (accessedProperty && forbiddenProperties.has(accessedProperty)) {
        throw new Error(`forbidden research capability: property ${accessedProperty} in ${file}`);
      }
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
        specifiers.push(literalModuleSpecifier(node.moduleSpecifier, file));
      }
      if (ts.isCallExpression(node)) {
        if (node.expression.kind === ts.SyntaxKind.ImportKeyword) {
          specifiers.push(literalModuleSpecifier(node.arguments[0], file));
        } else if (ts.isIdentifier(node.expression) && node.expression.text === 'require') {
          specifiers.push(literalModuleSpecifier(node.arguments[0], file));
        }
      } else if (ts.isIdentifier(node) && node.text === 'require') {
        throw new Error(`forbidden research capability: aliased require in ${file}`);
      }
      ts.forEachChild(node, visit);
    }
    visit(sourceFile);
    for (const specifier of specifiers) {
      if (specifier.startsWith('.')) pending.push(await resolveLocalModule(file, specifier));
      else if (!allowedModules.has(specifier)) {
        throw new Error(`forbidden research capability: unapproved module ${specifier} in ${file}`);
      }
    }
  }
}

const staticOnly = process.argv[2] === '--static-only';
const staticRoot = staticOnly ? resolve(process.argv[3] ?? '') : resolve(root, 'dist/research');
try {
  await assertStaticGraph(staticRoot);
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
}
if (staticOnly) {
  process.stdout.write('Research transport-free static graph PASS\n');
  process.exit(0);
}

const isolationRoot = await mkdtemp(join(tmpdir(), 'research-network-isolation-'));
const seccompFilter = join(isolationRoot, 'network-deny.bpf');
process.on('exit', () => rmSync(isolationRoot, { recursive: true, force: true }));
await writeResearchNetworkDenyFilter(seccompFilter);

function isolatedNode(arguments_, options) {
  return spawnSync('setpriv', [
    '--nnp',
    '--seccomp-filter', seccompFilter,
    process.execPath,
    ...arguments_,
  ], options);
}

const networkProbe = isolatedNode([
  '--eval', `
    const net = require('node:net');
    const socket = net.connect({ host: '127.0.0.1', port: 9 });
    socket.on('error', (error) => {
      if (error.code === 'EPERM') process.stdout.write('EPERM');
      process.exit(error.code === 'EPERM' ? 0 : 2);
    });
    setTimeout(() => process.exit(3), 1_000);
  `,
], { cwd: root, encoding: 'utf8', env: { ...process.env, NODE_NO_WARNINGS: '1' } });
if (networkProbe.status !== 0 || networkProbe.stdout !== 'EPERM' || networkProbe.stderr !== '') {
  process.stderr.write(networkProbe.stderr || `research seccomp network probe failed: ${String(networkProbe.status)}\n`);
  process.exit(1);
}

const result = isolatedNode([
  '--require', resolve(root, 'scripts/research-transport-preload.cjs'),
  '--import', resolve(root, 'scripts/research-transport-register.mjs'),
  resolve(root, 'dist/research/pump-historical-cli.js'),
  '--manifest', resolve(root, 'tests/fixtures/pump-research/v1-manifest.json'),
  '--records', resolve(root, 'tests/fixtures/pump-research/v1-records.json'),
  '--config', resolve(root, 'tests/fixtures/pump-research/paper-config.json'),
], {
  cwd: root,
  encoding: 'utf8',
  env: { ...process.env, NODE_NO_WARNINGS: '1' },
});

if (result.status !== 2 || result.stderr !== '') {
  process.stderr.write(result.stderr || `research transport check exited ${String(result.status)}\n`);
  process.exit(1);
}

let report;
try {
  report = JSON.parse(result.stdout);
} catch {
  process.stderr.write('research transport check emitted invalid JSON\n');
  process.exit(1);
}
if (report?.status !== 'BLOCKED') {
  process.stderr.write('research transport check did not preserve v1 BLOCKED status\n');
  process.exit(1);
}

const bronzeModule = pathToFileURL(resolve(root, 'dist/research/pump-v2-bronze.js')).href;
const bronzeImport = isolatedNode([
  '--require', resolve(root, 'scripts/research-transport-preload.cjs'),
  '--import', resolve(root, 'scripts/research-transport-register.mjs'),
  '--input-type=module',
  '--eval', `await import(${JSON.stringify(bronzeModule)})`,
], {
  cwd: root,
  encoding: 'utf8',
  env: { ...process.env, NODE_NO_WARNINGS: '1' },
});
if (bronzeImport.status !== 0 || bronzeImport.stdout !== '' || bronzeImport.stderr !== '') {
  process.stderr.write(bronzeImport.stderr || bronzeImport.stdout || `Bronze transport check exited ${String(bronzeImport.status)}\n`);
  process.exit(1);
}
rmSync(isolationRoot, { recursive: true, force: true });
process.stdout.write('Research transport-free built graph PASS\n');
