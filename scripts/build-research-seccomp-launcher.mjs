#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { chmod } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const source = resolve(import.meta.dirname, 'research-seccomp-launcher.c');
const compilationLimitMs = 10_000;
const outputLimitBytes = 256 * 1024;

export async function buildResearchSeccompLauncher(outputPath) {
  const output = resolve(outputPath);
  await new Promise((resolveBuild, reject) => {
    // A private process group bounds the compiler and its cc1/as/ld children.
    // The timeout/output-limit path can signal only the group created here.
    const child = spawn('cc', [
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
    ], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let reason;
    let captured = '';
    let bytes = 0;
    const stop = (failure) => {
      reason ??= failure;
      if (child.pid) {
        try { process.kill(-child.pid, 'SIGKILL'); }
        catch (error) { if (error.code !== 'ESRCH') reason += `; compiler group cleanup failed: ${error.message}`; }
      }
    };
    const timer = setTimeout(() => stop(`compilation exceeded ${compilationLimitMs}ms`), compilationLimitMs);
    for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => {
      const remaining = Math.max(0, outputLimitBytes - bytes);
      captured += chunk.subarray(0, remaining).toString('utf8');
      bytes += chunk.length;
      if (bytes > outputLimitBytes) stop(`compiler output exceeded ${outputLimitBytes} bytes`);
    });
    child.once('error', error => { reason ??= `compiler start failed: ${error.message}`; });
    child.once('close', (status, signal) => {
      clearTimeout(timer);
      if (reason || status !== 0 || signal) {
        reject(new Error(`seccomp launcher compilation failed (limit ${compilationLimitMs}ms): ${reason ?? 'nonzero exit'}; status=${String(status)} signal=${String(signal)}\n${captured}`));
      } else resolveBuild();
    });
  });
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
