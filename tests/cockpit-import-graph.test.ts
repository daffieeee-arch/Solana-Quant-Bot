import { describe, expect, it } from 'vitest';
import { validateCockpitGraphSources } from '../scripts/assert-cockpit-runtime-inert.mjs';

const safe = {
  'dist/cockpit-main.js': "import { start } from './cockpit-server.js'; import { provider } from './research/phase8a-research-provider.js'; export { start, provider };",
  'dist/cockpit-server.js': "import { createServer } from 'node:http'; import { readFile } from 'node:fs/promises'; import { resolve } from 'node:path'; export const start = () => createServer();",
  'dist/research/phase8a-research-provider.js': "import { openSync } from 'node:fs'; import { join } from 'node:path'; import { metrics } from './phase8a-observability.js'; export const provider = [openSync, join, metrics];",
  'dist/research/phase8a-observability.js': 'export const metrics = Object.freeze({});',
};

describe('cockpit compiled/import graph policy', () => {
  it('accepts the closed cockpit-only graph', () => {
    expect(validateCockpitGraphSources(safe, 'dist/cockpit-main.js')).toEqual([]);
  });

  it.each([
    ['direct scanner', { ...safe, 'dist/cockpit-main.js': "import './scanner.js';" }, 'dist/scanner.js'],
    ['transitive ledger', { ...safe, 'dist/cockpit-server.js': "import './helper.js';", 'dist/helper.js': "import './ledger.js';" }, 'dist/ledger.js'],
    ['provider runtime', { ...safe, 'dist/cockpit-main.js': "import './providers/triton.js';" }, 'dist/providers/triton.js'],
    ['child process', { ...safe, 'dist/cockpit-main.js': "import { spawn } from 'node:child_process'; spawn('x');" }, undefined],
    ['filesystem write', { ...safe, 'dist/cockpit-main.js': "import { writeFileSync } from 'node:fs'; writeFileSync('/tmp/x','x');" }, undefined],
    ['aliased computed process capability', { ...safe, 'dist/cockpit-main.js': "const proc = process; const key = proc.argv[2]; proc[key]('node:child_process');" }, undefined],
    ['dynamic import', { ...safe, 'dist/cockpit-main.js': "const name = './scanner.js'; await import(name);" }, undefined],
    ['computed require', { ...safe, 'dist/cockpit-main.js': "globalThis['requ' + 'ire']('./ledger.js');" }, undefined],
    ['outbound http client', { ...safe, 'dist/cockpit-server.js': "import { request } from 'node:http'; request('http://example.invalid');" }, undefined],
  ])('rejects %s reachability', (_name, sources, missing) => {
    const input = missing ? { ...sources, [missing]: 'export const value = 1;' } : sources;
    expect(validateCockpitGraphSources(input, 'dist/cockpit-main.js').join('\n')).toMatch(/forbidden|unapproved|dynamic|loader/i);
  });
});
