import { spawnSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import { lstat, mkdtemp, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
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
const allowedPumpSilverRegistryDescriptorKeys = new Set([
  'startInclusive',
  'endExclusive',
  'officialDocsCommit',
  'pumpIdlSha256',
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

function enclosingFunctionLike(node) {
  let current = node.parent;
  while (current) {
    if (ts.isFunctionDeclaration(current) || ts.isFunctionExpression(current)
      || ts.isArrowFunction(current) || ts.isMethodDeclaration(current)
      || ts.isGetAccessorDeclaration(current) || ts.isSetAccessorDeclaration(current)) return current;
    current = current.parent;
  }
  return undefined;
}

function isAllowedPumpSilverUtilImport(file, graphRoot, sourceFile, specifier) {
  if (specifier !== 'node:util' || file !== resolve(graphRoot, 'pump-silver-state-contract.js')) return false;
  const imports = sourceFile.statements.filter((statement) => ts.isImportDeclaration(statement)
    && ts.isStringLiteral(statement.moduleSpecifier) && statement.moduleSpecifier.text === 'node:util');
  if (imports.length !== 1) return false;
  const clause = imports[0].importClause;
  if (!clause || clause.isTypeOnly || clause.name || !clause.namedBindings
    || !ts.isNamedImports(clause.namedBindings) || clause.namedBindings.elements.length !== 1) return false;
  const element = clause.namedBindings.elements[0];
  return !element.isTypeOnly && element.propertyName?.text === 'types' && element.name.text === 'utilTypes';
}

function isAllowedPumpSilverRegistryDescriptorRead(node, checker, file, graphRoot) {
  if (file !== resolve(graphRoot, 'pump-silver-contract.js')
    || !ts.isPropertyAccessExpression(node)
    || !ts.isIdentifier(node.expression)
    || node.expression.text !== 'Object'
    || node.name.text !== 'getOwnPropertyDescriptor') return false;
  const objectSymbol = checker.getSymbolAtLocation(node.expression);
  if (objectSymbol?.declarations?.some((declaration) => declaration.getSourceFile() === node.getSourceFile())) return false;
  const call = node.parent;
  if (!ts.isCallExpression(call) || call.expression !== node || call.arguments.length !== 2
    || !ts.isIdentifier(call.arguments[0])
    || !ts.isStringLiteral(call.arguments[1])
    || !allowedPumpSilverRegistryDescriptorKeys.has(call.arguments[1].text)) return false;
  const declaration = enclosingFunctionLike(node);
  if (!declaration || !ts.isFunctionDeclaration(declaration)
    || declaration.parent !== declaration.getSourceFile()
    || declaration.name?.text !== 'normalizeFixtureRegistryEntry'
    || declaration.parameters.length !== 1
    || !ts.isIdentifier(declaration.parameters[0].name)
    || declaration.parameters[0].name.text !== 'entry') return false;
  const argumentSymbol = checker.getSymbolAtLocation(call.arguments[0]);
  const parameterSymbol = checker.getSymbolAtLocation(declaration.parameters[0].name);
  return argumentSymbol !== undefined && argumentSymbol === parameterSymbol;
}

function isAllowedPumpSilverStateDescriptorRead(node, checker, file, graphRoot) {
  if (file !== resolve(graphRoot, 'pump-silver-state-contract.js')
    || !ts.isPropertyAccessExpression(node)
    || !ts.isIdentifier(node.expression)
    || node.expression.text !== 'Object'
    || node.name.text !== 'getOwnPropertyDescriptor') return false;
  const objectSymbol = checker.getSymbolAtLocation(node.expression);
  if (objectSymbol?.declarations?.some((declaration) => declaration.getSourceFile() === node.getSourceFile())) return false;
  const call = node.parent;
  if (!ts.isCallExpression(call) || call.expression !== node || call.arguments.length !== 2
    || !ts.isIdentifier(call.arguments[0]) || !ts.isIdentifier(call.arguments[1])
    || call.arguments[1].text !== 'name') return false;
  const declaration = enclosingFunctionLike(node);
  if (!declaration || !ts.isFunctionDeclaration(declaration)
    || declaration.parent !== declaration.getSourceFile()
    || declaration.name?.text !== 'hasExactOwnKeys'
    || declaration.parameters.length !== 2
    || !ts.isIdentifier(declaration.parameters[0].name)
    || declaration.parameters[0].name.text !== 'value') return false;
  const valueSymbol = checker.getSymbolAtLocation(call.arguments[0]);
  const valueParameter = checker.getSymbolAtLocation(declaration.parameters[0].name);
  const nameSymbol = checker.getSymbolAtLocation(call.arguments[1]);
  const nameDeclaration = nameSymbol?.declarations?.[0];
  if (valueSymbol === undefined || valueSymbol !== valueParameter
    || nameDeclaration === undefined || !ts.isVariableDeclaration(nameDeclaration)
    || !ts.isIdentifier(nameDeclaration.name) || nameDeclaration.name.text !== 'name'
    || !ts.isVariableDeclarationList(nameDeclaration.parent)
    || !ts.isForOfStatement(nameDeclaration.parent.parent)
    || nameDeclaration.parent.parent.initializer !== nameDeclaration.parent
    || !ts.isIdentifier(nameDeclaration.parent.parent.expression)) return false;
  const namesUse = nameDeclaration.parent.parent.expression;
  const namesSymbol = checker.getSymbolAtLocation(namesUse);
  const namesDeclaration = namesSymbol?.declarations?.[0];
  if (namesDeclaration === undefined || !ts.isVariableDeclaration(namesDeclaration)
    || !ts.isIdentifier(namesDeclaration.name) || namesDeclaration.name.text !== 'names'
    || namesDeclaration.getSourceFile() !== declaration.getSourceFile()
    || namesDeclaration.getStart() <= declaration.getStart()
    || namesDeclaration.getEnd() >= nameDeclaration.parent.parent.getStart()
    || !namesDeclaration.initializer || !ts.isCallExpression(namesDeclaration.initializer)
    || namesDeclaration.initializer.arguments.length !== 1
    || !ts.isIdentifier(namesDeclaration.initializer.arguments[0])
    || !ts.isPropertyAccessExpression(namesDeclaration.initializer.expression)
    || !ts.isIdentifier(namesDeclaration.initializer.expression.expression)
    || namesDeclaration.initializer.expression.expression.text !== 'Object'
    || namesDeclaration.initializer.expression.name.text !== 'getOwnPropertyNames') return false;
  const namesObjectSymbol = checker.getSymbolAtLocation(namesDeclaration.initializer.expression.expression);
  const namesValueSymbol = checker.getSymbolAtLocation(namesDeclaration.initializer.arguments[0]);
  if (namesObjectSymbol?.declarations?.some((entry) => entry.getSourceFile() === node.getSourceFile())
    || namesValueSymbol === undefined || namesValueSymbol !== valueParameter) return false;

  const descriptorDeclaration = call.parent;
  if (!ts.isVariableDeclaration(descriptorDeclaration)
    || descriptorDeclaration.initializer !== call
    || !ts.isIdentifier(descriptorDeclaration.name)
    || descriptorDeclaration.name.text !== 'descriptor') return false;
  const descriptorSymbol = checker.getSymbolAtLocation(descriptorDeclaration.name);
  if (descriptorSymbol === undefined) return false;
  let descriptorUsesAreSafe = true;
  const validateDescriptorUse = (candidate) => {
    if (!descriptorUsesAreSafe) return;
    if (ts.isIdentifier(candidate) && checker.getSymbolAtLocation(candidate) === descriptorSymbol) {
      if (candidate === descriptorDeclaration.name) return;
      const parent = candidate.parent;
      const isPresenceCheck = ts.isPrefixUnaryExpression(parent)
        && parent.operator === ts.SyntaxKind.ExclamationToken && parent.operand === candidate;
      const isEnumerableComparison = ts.isPropertyAccessExpression(parent)
        && parent.expression === candidate && parent.name.text === 'enumerable'
        && ts.isBinaryExpression(parent.parent)
        && parent.parent.left === parent
        && [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken]
          .includes(parent.parent.operatorToken.kind);
      const isExactValuePresenceCheck = ts.isCallExpression(parent)
        && parent.arguments.length === 2 && parent.arguments[0] === candidate
        && ts.isStringLiteral(parent.arguments[1]) && parent.arguments[1].text === 'value'
        && ts.isPropertyAccessExpression(parent.expression)
        && ts.isIdentifier(parent.expression.expression)
        && parent.expression.expression.text === 'Object'
        && parent.expression.name.text === 'hasOwn'
        && !checker.getSymbolAtLocation(parent.expression.expression)?.declarations
          ?.some((entry) => entry.getSourceFile() === node.getSourceFile());
      if (!isPresenceCheck && !isEnumerableComparison && !isExactValuePresenceCheck) {
        descriptorUsesAreSafe = false;
        return;
      }
    }
    ts.forEachChild(candidate, validateDescriptorUse);
  };
  validateDescriptorUse(declaration);
  return descriptorUsesAreSafe;
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
      if (accessedProperty && forbiddenProperties.has(accessedProperty)
        && !isAllowedPumpSilverRegistryDescriptorRead(node, checker, file, directory)
        && !isAllowedPumpSilverStateDescriptorRead(node, checker, file, directory)) {
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
      else if (!allowedModules.has(specifier)
        && !isAllowedPumpSilverUtilImport(file, directory, sourceFile, specifier)) {
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
const seccompLauncher = join(isolationRoot, 'research-seccomp-launcher');
process.on('exit', () => rmSync(isolationRoot, { recursive: true, force: true }));
await writeResearchNetworkDenyFilter(seccompFilter);
await buildResearchSeccompLauncher(seccompLauncher);

function isolatedNode(arguments_, options) {
  return spawnSync(seccompLauncher, [
    seccompFilter,
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
