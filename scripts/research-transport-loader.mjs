const forbidden = [
  '@solana/web3.js',
  'node-fetch',
  'rpc-websockets',
  'ws',
  'undici',
  'http',
  'https',
  'net',
  'tls',
  'node:http',
  'node:https',
  'node:net',
  'node:tls',
];

function isForbidden(specifier) {
  return forbidden.some((name) => specifier === name || specifier.startsWith(`${name}/`));
}

export async function resolve(specifier, context, nextResolve) {
  if (isForbidden(specifier)) throw new Error(`forbidden research transport import: ${specifier}`);
  return nextResolve(specifier, context);
}
