import { fileURLToPath } from 'node:url';
import { isAbsolute, resolve } from 'node:path';
import { createAcquisitionMonitor } from './server.js';

export function parseMonitorArguments(args: readonly string[]): { snapshotsDirectory: string; port: number } {
  let snapshotsDirectory: string | undefined;
  let port = 4173;
  let portSeen = false;
  for (let index = 0; index < args.length; index += 2) {
    const option = args[index];
    const value = args[index + 1];
    if (!value) throw new Error('Usage: --snapshots /absolute/telemetry-directory [--port 4173]');
    if (option === '--snapshots' && snapshotsDirectory === undefined && isAbsolute(value)) snapshotsDirectory = value;
    else if (option === '--port' && !portSeen && /^[1-9]\d{0,4}$/.test(value) && Number(value) <= 65535) { port = Number(value); portSeen = true; }
    else throw new Error('INVALID_MONITOR_ARGUMENT');
  }
  if (!snapshotsDirectory) throw new Error('Usage: --snapshots /absolute/telemetry-directory [--port 4173]');
  return { snapshotsDirectory, port };
}

async function main(): Promise<void> {
  const options = parseMonitorArguments(process.argv.slice(2));
  const server = await createAcquisitionMonitor({
    ...options,
    staticDirectory: fileURLToPath(new URL('../../frontend/dist-monitor/', import.meta.url)),
  });
  process.stdout.write(`Acquisition monitor: http://127.0.0.1:${server.port} (read-only; no acquisition authority)\n`);
  let stopping = false;
  const stop = () => {
    if (stopping) return;
    stopping = true;
    void server.close().then(() => process.exit(0), () => process.exit(1));
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.message : 'MONITOR_START_FAILED'}\n`); process.exitCode = 1; });
}
