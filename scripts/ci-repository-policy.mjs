#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync, statSync } from 'node:fs';
import { extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  validateTrackedWorkflowPaths,
  validateWorkflowConfiguration,
} from './lib/workflow-policy.mjs';
import {
  validateCitationModuleGraph,
  validateTrackedResearchPaths,
} from './ci-research-citations.mjs';

export { parseWorkflowYaml } from './lib/strict-yaml.mjs';
export {
  validateTrackedWorkflowPaths,
  validateWorkflowConfiguration,
} from './lib/workflow-policy.mjs';

const SAFE_DEFAULT_BUILD = [
  'tsc -p tsconfig.json',
  'npm run verify:research-transport',
  'npm run verify:phase8a-runner-offline',
  'npm run build:cockpit',
  'npm run verify:cockpit-runtime',
].join(' && ');

export function validateTrackedRepositoryPaths(trackedPaths) {
  const errors = [];
  for (const path of trackedPaths) {
    if (/^\.hermes(?:\/|$)/.test(path)) {
      errors.push(`retired Hermes path must not be tracked: ${path}`);
    }
    if (/^legacy(?:\/|$)/.test(path)) {
      errors.push(`permanent root legacy directory must not be tracked: ${path}`);
    }
  }
  return errors;
}

export function validatePackageScripts(scripts) {
  const errors = [];
  if (scripts === null || typeof scripts !== 'object' || Array.isArray(scripts)) {
    return ['package.json scripts must be an object'];
  }

  for (const name of ['dev', 'start']) {
    if (Object.prototype.hasOwnProperty.call(scripts, name)) {
      errors.push(`package.json must not expose the frozen legacy ${name} entrypoint`);
    }
  }

  for (const [name, value] of Object.entries(scripts)) {
    const command = String(value).replaceAll('\\', '/');
    if (/(?:^|[^A-Za-z0-9_.-])(?:\.\/)*(?:src\/main\.ts|dist\/main\.js)(?=$|[^A-Za-z0-9_.-])/.test(command)) {
      errors.push(`package script ${name} must not launch the frozen legacy runtime`);
    }
  }

  if (scripts['start:cockpit'] !== 'node dist/cockpit-main.js') {
    errors.push('package.json must expose only the reviewed explicit cockpit monitor start command');
  }
  if (scripts.build !== SAFE_DEFAULT_BUILD) {
    errors.push('package.json build must match the reviewed B2A offline command graph exactly');
  }
  for (const name of ['verify:phase8c-contracts', 'verify:phase8d1-supply-chain']) {
    if (Object.prototype.hasOwnProperty.call(scripts, name)) {
      errors.push(`retired package script must be absent: ${name}`);
    }
  }

  return errors;
}

function runPolicy() {
  const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
  process.chdir(root);

  const tracked = execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' })
    .split('\0')
    .filter(Boolean)
    .sort();
  const trackedIgnored = execFileSync('git', ['ls-files', '-ci', '--exclude-standard', '-z'], { encoding: 'utf8' })
    .split('\0')
    .filter(Boolean)
    .sort();

  const errors = [];
  errors.push(...validateTrackedWorkflowPaths(tracked));
  errors.push(...validateTrackedRepositoryPaths(tracked));
  errors.push(...validateTrackedResearchPaths(tracked));
  errors.push(...validateCitationModuleGraph({ root }));
  for (const path of trackedIgnored) {
    errors.push(`tracked file is ignored by .gitignore and must be reconciled: ${path}`);
  }

  const forbiddenPathRules = [
    ['runtime/cache directory', /^(?:\.backtest-cache|data|data-bot[^/]*|data-stream[^/]*)(?:\/|$)/],
    ['dependency/build output', /^(?:node_modules|dist)(?:\/|$)/],
    ['secret directory', /(?:^|\/)secrets(?:\/|$)/],
    ['real env file', /(?:^|\/)\.env(?:\.(?!example$)[^/]*)?$/],
    ['token file', /(?:^|\/)[^/]*-token[^/]*\.txt$/i],
    ['admin websocket script', /(?:^|\/)[^/]*-wss-admin[^/]*\.mjs$/i],
    ['API-key artifact', /(?:^|\/)[^/]*api-key[^/]*\.json$/i],
    ['runtime log', /\.(?:log|ndjson)$/i],
    ['legacy destructive deploy helper', /^scripts\/(?:gen-inline-yaml\.py|reinstall-bot\.py)$/],
  ];
  for (const path of tracked) {
    for (const [label, pattern] of forbiddenPathRules) {
      if (pattern.test(path)) errors.push(`${label} must not be tracked: ${path}`);
    }
  }

  const requiredTracked = [
    '.env.example',
    '.github/workflows/ci.yml',
    '.gitignore',
    'AGENTS.md',
    'docs/CI.md',
    'docs/CURRENT_STATE.md',
    'docs/HANDOFF.md',
    'docs/PHASE7A_PUMP_ACTIVATION_EVIDENCE.md',
    'docs/research/PUMP_ACTIVATION_EVIDENCE_EPOCH_978.json',
    'docs/research/PUMP_ACTIVATION_SCRATCH_MANIFEST.json',
    'package-lock.json',
    'roadmap/b2a-invariant-salvage-manifest.json',
    'scripts/ci-research-citations.mjs',
    'scripts/lib/strict-yaml.mjs',
    'scripts/lib/workflow-policy.mjs',
    'tests/ci-policy.test.ts',
    'tests/ci-research-citations.test.ts',
  ];
  for (const path of requiredTracked) {
    if (!tracked.includes(path)) errors.push(`required repository file is not tracked: ${path}`);
  }

  const text = (path) => readFileSync(path, 'utf8');
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
  errors.push(...validatePackageScripts(packageJson.scripts));
  if (packageJson.scripts?.['ci:policy'] !== 'node scripts/ci-repository-policy.mjs') {
    errors.push('package.json must expose the expected ci:policy script');
  }
  if (packageJson.scripts?.['ci:research-citations'] !== 'node scripts/ci-research-citations.mjs') {
    errors.push('package.json must expose the expected offline ci:research-citations script');
  }

  const zeroCost = text('src/zero-cost.ts');
  if (!zeroCost.includes("raw.trim().toLowerCase() === 'true'")) {
    errors.push('zero-cost unlock must remain strict: only explicit true enables live Triton');
  }
  errors.push(...validateWorkflowConfiguration(text('.github/workflows/ci.yml')));

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
    try {
      stats = statSync(path);
    } catch {
      continue;
    }
    if (!stats.isFile() || stats.size > 2 * 1024 * 1024) continue;

    let content;
    try {
      content = text(path);
    } catch {
      continue;
    }
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

  if (errors.length > 0) {
    console.error('Repository policy FAILED');
    for (const error of [...new Set(errors)]) console.error(`- ${error}`);
    process.exit(1);
  }

  console.log(`Repository policy PASS (${tracked.length} tracked files checked; no tracked ignored files; workflow parsed semantically)`);
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath && invokedPath === fileURLToPath(import.meta.url)) runPolicy();
