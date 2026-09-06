import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const allowed = new Map([
  ['node:fs', new Set(['constants'])],
  ['node:fs/promises', new Set(['open', 'opendir', 'realpath'])],
  ['node:crypto', new Set(['createHash'])],
  ['node:path', new Set(['isAbsolute', 'join', 'normalize', 'resolve', 'extname'])],
  ['node:url', new Set(['fileURLToPath'])],
  ['node:http', new Set(['createServer'])],
]);

function boundaryErrors(source: string, name: string): string[] {
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const errors: string[] = [];
  function visit(node: ts.Node) {
    if (ts.isImportDeclaration(node) && !node.importClause?.isTypeOnly) {
      if (!ts.isStringLiteral(node.moduleSpecifier)) errors.push('dynamic import');
      else {
        const path = node.moduleSpecifier.text;
        if (path.startsWith('.')) {
          if (!['./server.js', './reader.js'].includes(path)) errors.push(`unapproved module ${path}`);
        } else {
          const names = node.importClause?.namedBindings;
          if (!allowed.has(path) || !names || !ts.isNamedImports(names) || node.importClause?.name) errors.push(`unapproved capability ${path}`);
          else for (const item of names.elements) {
            if (!item.isTypeOnly && !allowed.get(path)!.has((item.propertyName ?? item.name).text)) errors.push(`unapproved import ${path}:${item.name.text}`);
          }
        }
      }
    }
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) errors.push('dynamic import');
    if (ts.isIdentifier(node) && ['fetch', 'WebSocket', 'EventSource', 'require', 'eval', 'spawn', 'writeFile', 'writeFileSync', 'flock', 'flockSync'].includes(node.text)) errors.push(`forbidden capability ${node.text}`);
    ts.forEachChild(node, visit);
  }
  visit(file);
  return errors;
}

describe('independent V2 monitor capability boundary', () => {
  it('contains only bounded read, hash, static serving and loopback-listener capabilities', () => {
    for (const name of ['main.ts', 'server.ts', 'reader.ts']) {
      const source = readFileSync(resolve('src/acquisition-monitor', name), 'utf8');
      expect(boundaryErrors(source, name), name).toEqual([]);
      expect(source).not.toMatch(/cockpit-server|cockpit-main|dashboard\.js|providers\/|of1-acquire|metadata-capture|payload-capture|process\.env/);
    }
  });

  it('would reject a provider/scanner, subprocess, writer lock or outbound HTTP client', () => {
    for (const source of [
      "import { start } from '../scanner.js';",
      "import { spawn } from 'node:child_process';",
      "import { writeFile } from 'node:fs/promises';",
      "import { flock } from 'fs-ext';",
      "import { request } from 'node:http';",
      "await import('../providers/triton.js');",
      "await fetch('https://files.old-faithful.net');",
    ]) expect(boundaryErrors(source, 'probe.ts').length).toBeGreaterThan(0);
  });
});
