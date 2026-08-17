#!/usr/bin/env node

import { closeSync, constants, fstatSync, openSync, readSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { decodePumpResearchConfig } from './pump-historical-config.js';
import { runPumpResearch, stableResearchJson } from './pump-historical.js';

const LIMITS = {
  manifest: 1024 * 1024,
  records: 256 * 1024 * 1024,
  config: 1024 * 1024,
} as const;

type InputKind = keyof typeof LIMITS;

function parseArgs(args: string[]): Record<InputKind, string> {
  const values = new Map<string, string>();
  if (args.length % 2 !== 0) throw new Error('usage: pump-historical-cli --manifest FILE --records FILE --config FILE');
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    const value = args[index + 1];
    if (!flag?.startsWith('--') || !value || value.startsWith('--')) {
      throw new Error('usage: pump-historical-cli --manifest FILE --records FILE --config FILE');
    }
    const key = flag.slice(2);
    if (!['manifest', 'records', 'config'].includes(key) || values.has(key)) {
      throw new Error(`unsupported or duplicate argument: ${flag}`);
    }
    values.set(key, value);
  }
  for (const key of ['manifest', 'records', 'config'] as const) {
    if (!values.has(key)) throw new Error(`missing required argument: --${key}`);
  }
  return Object.fromEntries(values) as Record<InputKind, string>;
}

function readJson(kind: InputKind, pathValue: string): unknown {
  const path = resolve(pathValue);
  let descriptor: number;
  try {
    descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  } catch {
    throw new Error(`${kind} input could not be opened as a regular non-symlink file`);
  }
  try {
    const initialStats = fstatSync(descriptor);
    if (!initialStats.isFile()) throw new Error(`${kind} input must be a regular file`);
    if (initialStats.size > LIMITS[kind]) throw new Error(`${kind} input exceeds ${LIMITS[kind]} bytes`);
    const chunks: Buffer[] = [];
    const chunk = Buffer.alloc(64 * 1024);
    let total = 0;
    while (true) {
      const count = readSync(descriptor, chunk, 0, chunk.length, null);
      if (count === 0) break;
      total += count;
      if (total > LIMITS[kind]) throw new Error(`${kind} input exceeds ${LIMITS[kind]} bytes`);
      chunks.push(Buffer.from(chunk.subarray(0, count)));
    }
    const finalStats = fstatSync(descriptor);
    if (total !== initialStats.size || finalStats.size !== initialStats.size) {
      throw new Error(`${kind} input changed while being read`);
    }
    try {
      return JSON.parse(Buffer.concat(chunks, total).toString('utf8')) as unknown;
    } catch {
      throw new Error(`invalid ${kind} JSON`);
    }
  } finally {
    closeSync(descriptor);
  }
}

export function runPumpHistoricalCli(args: string[]): number {
  const paths = parseArgs(args);
  const manifest = readJson('manifest', paths.manifest);
  const records = readJson('records', paths.records);
  const configValue = readJson('config', paths.config);
  if (!Array.isArray(records)) throw new Error('records input must be a JSON array');
  const config = decodePumpResearchConfig(configValue);
  const report = runPumpResearch(manifest, records, config);
  process.stdout.write(stableResearchJson(report));
  return typeof report === 'object' && report !== null
    && 'status' in report && report.status === 'READY' ? 0 : 2;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = runPumpHistoricalCli(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`pump historical research failed: ${error instanceof Error ? error.message : 'unknown error'}\n`);
    process.exitCode = 1;
  }
}
