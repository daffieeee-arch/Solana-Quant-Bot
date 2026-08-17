import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const result = spawnSync(process.execPath, [
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
process.stdout.write('Research transport-free built graph PASS\n');
