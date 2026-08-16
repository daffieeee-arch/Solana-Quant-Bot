#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync, statSync } from 'node:fs';
import { extname } from 'node:path';

const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
process.chdir(root);

const tracked = execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' })
  .split('\0')
  .filter(Boolean)
  .sort();

const errors = [];
const warnings = [];

const forbiddenPathRules = [
  ['runtime/cache directory', /^(?:\.backtest-cache|data-bot(?:-[^/]+)?|data-stream(?:-[^/]+)?)(?:\/|$)/],
  ['dependency/build output', /^(?:node_modules|dist)(?:\/|$)/],
  ['secret directory', /(?:^|\/)secrets(?:\/|$)/],
  ['real env file', /(?:^|\/)\.env(?:\.(?!example$)[^/]*)?$/],
  ['token file', /(?:^|\/)[^/]*-token[^/]*\.txt$/i],
  ['admin websocket script', /(?:^|\/)[^/]*-wss-admin[^/]*\.mjs$/i],
  ['API-key artifact', /(?:^|\/)[^/]*api-key[^/]*\.json$/i],
  ['runtime log', /\.(?:log|ndjson)$/i],
];

for (const path of tracked) {
  for (const [label, pattern] of forbiddenPathRules) {
    if (pattern.test(path)) errors.push(`${label} must not be tracked: ${path}`);
  }
}

const requiredTracked = [
  'AGENTS.md',
  'docs/HANDOFF.md',
  'docs/CURRENT_STATE.md',
  'docs/CI.md',
  '.github/workflows/ci.yml',
  '.env.example',
  'package-lock.json',
];
for (const path of requiredTracked) {
  if (!tracked.includes(path)) errors.push(`required repository file is not tracked: ${path}`);
}

function text(path) {
  return readFileSync(path, 'utf8');
}

const envExample = text('.env.example');
for (const requiredLine of ['MODE=paper', 'TRITON_LIVE_ENABLED=false', 'ENTRY_SHADOW_MODE=true']) {
  if (!envExample.split(/\r?\n/).includes(requiredLine)) {
    errors.push(`.env.example must contain exact safe default: ${requiredLine}`);
  }
}
if (/^(?:TRITON_TOKEN|TRITON_ENDPOINT|RPC_HTTP_ENDPOINT|RPC_WS_ENDPOINT)=\S+/m.test(envExample)) {
  errors.push('.env.example must not contain live endpoint or token values');
}

const packageJson = JSON.parse(text('package.json'));
if (packageJson.private !== true) errors.push('package.json must keep private=true');
if (packageJson.scripts?.['ci:policy'] !== 'node scripts/ci-repository-policy.mjs') {
  errors.push('package.json must expose the expected ci:policy script');
}

const zeroCost = text('src/zero-cost.ts');
if (!zeroCost.includes("raw.trim().toLowerCase() === 'true'")) {
  errors.push('zero-cost unlock must remain strict: only explicit true enables live Triton');
}

const workflow = text('.github/workflows/ci.yml');
if (!/permissions:\s*\n\s+contents:\s*read\b/.test(workflow)) {
  errors.push('CI workflow must declare read-only contents permission');
}
if (!workflow.includes("TRITON_LIVE_ENABLED: 'false'")) {
  errors.push('CI workflow must force TRITON_LIVE_ENABLED=false');
}
if (!workflow.includes('persist-credentials: false')) {
  errors.push('CI checkout must not persist GitHub credentials');
}
if (/\bsecrets\.[A-Za-z0-9_]+/.test(workflow)) {
  errors.push('CI workflow must not consume GitHub/production secrets');
}
for (const forbiddenWorkflowFragment of ['docker push', 'kubectl ', 'ssh ', 'scp ', 'TRITON_LIVE_ENABLED: true']) {
  if (workflow.toLowerCase().includes(forbiddenWorkflowFragment.toLowerCase())) {
    errors.push(`CI workflow contains forbidden deployment/live fragment: ${forbiddenWorkflowFragment}`);
  }
}

const textExtensions = new Set([
  '.cjs', '.css', '.env', '.html', '.js', '.json', '.jsx', '.md', '.mjs', '.py', '.sh', '.ts', '.tsx', '.txt', '.yaml', '.yml',
]);
const explicitTextFiles = new Set(['Dockerfile', '.dockerignore', '.gitignore']);
const privateKeyHeader = /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/;
const githubToken = /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/g;
const credentialAssignment = /\b(TRITON_TOKEN|BIRDEYE_API_KEY|HELIUS_API_KEY|ALCHEMY_API_KEY|TRUENAS_API_KEY|GRAFANA_TOKEN|CLICKHOUSE_PASSWORD)\s*[:=]\s*["']?([^"'\s,;}]+)/g;
const safeValue = /^(?:<[^>]+>|\$\{|\*+|0{8}|dummy|example|fake|placeholder|redacted|test)/i;

for (const path of tracked) {
  const extension = extname(path).toLowerCase();
  if (!textExtensions.has(extension) && !explicitTextFiles.has(path)) continue;
  let stats;
  try { stats = statSync(path); } catch { continue; }
  if (!stats.isFile() || stats.size > 2 * 1024 * 1024) continue;

  let content;
  try { content = text(path); } catch { continue; }
  if (content.includes('\0')) continue;

  if (privateKeyHeader.test(content)) errors.push(`private-key header detected in tracked file: ${path}`);
  if (githubToken.test(content)) errors.push(`GitHub token-shaped value detected in tracked file: ${path}`);
  githubToken.lastIndex = 0;

  credentialAssignment.lastIndex = 0;
  for (const match of content.matchAll(credentialAssignment)) {
    const value = match[2] ?? '';
    if (!safeValue.test(value) && value.length >= 12) {
      errors.push(`possible hardcoded ${match[1]} value in ${path}; use a protected _FILE mount or test placeholder`);
    }
  }
}

if (tracked.some((path) => path.startsWith('.hermes/') && !path.startsWith('.hermes/plans/'))) {
  warnings.push('unexpected .hermes content is tracked; review whether it belongs in source control');
}

if (errors.length > 0) {
  console.error('Repository policy FAILED');
  for (const error of errors) console.error(`- ${error}`);
  for (const warning of warnings) console.error(`- warning: ${warning}`);
  process.exit(1);
}

console.log(`Repository policy PASS (${tracked.length} tracked files checked)`);
for (const warning of warnings) console.warn(`warning: ${warning}`);
