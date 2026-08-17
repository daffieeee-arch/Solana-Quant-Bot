const Module = require('node:module');

const originalLoad = Module._load;
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

function isForbidden(request) {
  return forbidden.some((name) => request === name || request.startsWith(`${name}/`));
}

Module._load = function guardedResearchLoad(request, ...args) {
  if (isForbidden(request)) throw new Error(`forbidden research transport import: ${request}`);
  return originalLoad.call(this, request, ...args);
};
