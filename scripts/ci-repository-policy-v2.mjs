#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync, statSync } from 'node:fs';
import { extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Remove a YAML comment while preserving # characters inside quoted scalars. */
export function stripYamlComment(line) {
  let quote = null;
  let escaped = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (quote === '"' && escaped) {
      escaped = false;
      continue;
    }
    if (quote === '"' && character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) {
        if (quote === "'" && line[index + 1] === "'") {
          index += 1;
          continue;
        }
        quote = null;
      }
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '#') return line.slice(0, index);
  }
  return line;
}

function unquoteYamlScalar(raw) {
  const value = raw.trim();
  if (value.length >= 2 && value.startsWith("'") && value.endsWith("'")) {
    return value.slice(1, -1).replace(/''/g, "'");
  }
  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
    try {
      return JSON.parse(value);
    } catch {
      return value.slice(1, -1);
    }
  }
  return value;
}

/** Return all active scalar assignments for one YAML key, ignoring comments. */
export function activeYamlScalarValues(yaml, key) {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`^\\s*${escapedKey}\\s*:\\s*(.*?)\\s*$`);
  const values = [];
  for (const line of yaml.split(/\r?\n/)) {
    const activeLine = stripYamlComment(line).trimEnd();
    if (!activeLine.trim()) continue;
    const match = activeLine.match(pattern);
    if (!match || !match[1]?.trim()) continue;
    values.push(unquoteYamlScalar(match[1]));
  }
  return values;
}

/** Validate effective security-sensitive values in the CI workflow. */
export function validateWorkflowConfiguration(workflow) {
  const errors = [];
  const requireExactly = (key, expected) => {
    const values = activeYamlScalarValues(workflow, key);
    if (values.length !== 1) {
      errors.push(`workflow must contain exactly one active ${key} assignment; found ${values.length}`);
      return;
    }
    if (String(values[0]).trim().toLowerCase() !== expected.toLowerCase()) {
      errors.push(`workflow ${key} must equal ${expected}; found ${JSON.stringify(values[0])}`);
    }
  };

  requireExactly('contents', 'read');
  requireExactly('persist-credentials', 'false');
  requireExactly('CI', 'true');
  requireExactly('MODE', 'paper');
  requireExactly('TRITON_LIVE_ENABLED', 'false');
  requireExactly('ENTRY_SHADOW_MODE', 'true');

  const activeText = workflow
    .split(/\r?\n/)
    .map(stripYamlComment)
    .filter((line) => line.trim().length > 0)
    .join('\n');

  if (/\bsecrets\.[A-Za-z0-9_]+/.test(activeText)) {
    errors.push('CI workflow must not consume repository or production secrets');
  }
  for (const forbidden of ['docker push', 'kubectl ', 'ssh ', 'scp ']) {
    if (activeText.toLowerCase().includes(forbidden)) {
      errors.push(`CI workflow contains forbidden deployment fragment: ${forbidden}`);
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
  const warnings = [];

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
    'package-lock.json',
    'tests/ci-policy.test.ts',
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
  if (packageJson.scripts?.['ci:policy'] !== 'node scripts/ci-repository-policy.mjs') {
    errors.push('package.json must expose the expected ci:policy script');
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

  console.log(`Repository policy PASS (${tracked.length} tracked files checked; no tracked ignored files)`);
  for (const warning of warnings) console.warn(`warning: ${warning}`);
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath && invokedPath === fileURLToPath(import.meta.url)) runPolicy();
