import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { loadCockpitConfig } from './cockpit-config.js';
import { createCockpitServer } from './cockpit-server.js';
import { createFilePhase8AResearchProvider } from './research/phase8a-research-provider.js';

export type RunningCockpit = Readonly<{
  bindHost: string;
  port: number;
  providerConfigured: boolean;
  close(): Promise<void>;
}>;

export type StartCockpitOptions = Readonly<{
  env?: Readonly<Record<string, string | undefined>>;
  staticDir?: string;
  portOverride?: number;
}>;

export async function startCockpit(options: StartCockpitOptions = {}): Promise<RunningCockpit> {
  const config = loadCockpitConfig(options.env ?? process.env);
  const staticDir = options.staticDir ?? resolve(fileURLToPath(new URL('../frontend/cockpit-dist/', import.meta.url)));
  const researchProvider = config.outputDirectory
    ? createFilePhase8AResearchProvider(config.outputDirectory)
    : undefined;
  const server = await createCockpitServer({
    bindHost: config.bindHost,
    port: options.portOverride ?? config.port,
    staticDir,
    indexFile: 'cockpit.html',
    researchProvider,
  });
  return Object.freeze({
    bindHost: config.bindHost,
    port: server.port,
    providerConfigured: researchProvider !== undefined,
    close: () => server.close(),
  });
}

async function main(): Promise<void> {
  const running = await startCockpit();
  process.stdout.write(`${JSON.stringify({
    event: 'cockpit_started',
    mode: 'COCKPIT_ONLY',
    bindHost: running.bindHost,
    port: running.port,
    providerConfigured: running.providerConfigured,
  })}\n`);
  let stopping = false;
  const stop = () => {
    if (stopping) return;
    stopping = true;
    void running.close().then(() => process.exit(0), () => process.exit(1));
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === fileURLToPath(import.meta.url)) {
  main().catch(() => {
    process.stderr.write('cockpit_start_failed\n');
    process.exitCode = 1;
  });
}
