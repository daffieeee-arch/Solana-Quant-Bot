import { createMintInspector } from './server.js';

const [dataRoot, registryPath, staticDirectory, portText, ...extra] = process.argv.slice(2);
if (!dataRoot || !registryPath || !staticDirectory || !/^[0-9]{4,5}$/.test(portText ?? '')
  || Number(portText) < 1024 || Number(portText) > 65535 || extra.length) {
  console.error('Usage: node dist/mint-inspector/main.js DATA_ROOT REGISTRY_RELATIVE_PATH STATIC_DIRECTORY PORT');
  process.exitCode = 1;
} else {
  try {
    const server = await createMintInspector({ dataRoot, registryPath, staticDirectory, port: Number(portText) });
    console.log(`Read-only mint inspector: http://127.0.0.1:${server.port}/ (maximum 900 seconds)`);
    const stop = async () => { clearTimeout(timer); await server.close(); };
    const timer = setTimeout(() => { void stop(); }, 900000);
    process.once('SIGINT', () => { void stop(); });
    process.once('SIGTERM', () => { void stop(); });
  } catch {
    console.error('Mint inspector unavailable: check registered files, hashes, contracts and local port.');
    process.exitCode = 1;
  }
}
