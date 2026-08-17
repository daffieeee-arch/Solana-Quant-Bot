#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { chmod } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const source = resolve(import.meta.dirname, 'research-seccomp-launcher.c');

export async function buildResearchSeccompLauncher(outputPath) {
  const output = resolve(outputPath);
  const result = spawnSync('cc', [
    '-std=c11',
    '-O2',
    '-Wall',
    '-Wextra',
    '-Werror',
    '-fPIE',
    '-pie',
    '-D_FORTIFY_SOURCE=2',
    '-fstack-protector-strong',
    '-Wl,-z,relro,-z,now',
    source,
    '-o', output,
  ], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `C compiler exited ${String(result.status)}`);
  }
  await chmod(output, 0o700);
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : null;
if (invokedPath === import.meta.url) {
  if (process.argv.length !== 3) {
    process.stderr.write('usage: build-research-seccomp-launcher.mjs <output-path>\n');
    process.exit(2);
  }
  try {
    await buildResearchSeccompLauncher(process.argv[2]);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
