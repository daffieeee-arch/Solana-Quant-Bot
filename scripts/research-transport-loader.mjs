const forbidden = [
  '@solana/web3.js',
  'node-fetch',
  'rpc-websockets',
  'ws',
  'undici',
  'http',
  'http2',
  'https',
  'net',
  'tls',
  'dgram',
  'dns',
  'child_process',
  'cluster',
  'worker_threads',
  'module',
  'vm',
  'inspector',
  'inspector/promises',
  'node:http',
  'node:http2',
  'node:https',
  'node:net',
  'node:tls',
  'node:dgram',
  'node:dns',
  'node:child_process',
  'node:cluster',
  'node:worker_threads',
  'node:module',
  'node:vm',
  'node:inspector',
  'node:inspector/promises',
];

function isForbidden(specifier) {
  return forbidden.some((name) => specifier === name || specifier.startsWith(`${name}/`));
}

export async function resolve(specifier, context, nextResolve) {
  if (isForbidden(specifier)) throw new Error(`forbidden research transport import: ${specifier}`);
  return nextResolve(specifier, context);
}
