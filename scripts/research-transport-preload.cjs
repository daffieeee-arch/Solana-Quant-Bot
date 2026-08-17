const Module = require('node:module');
const { realpathSync } = require('node:fs');
const { resolve } = require('node:path');

const originalLoad = Module._load;
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

function isForbidden(request) {
  return forbidden.some((name) => request === name || request.startsWith(`${name}/`));
}

Module._load = function guardedResearchLoad(request, ...args) {
  if (isForbidden(request)) throw new Error(`forbidden research transport import: ${request}`);
  return originalLoad.call(this, request, ...args);
};

function blockCapability(target, name, label) {
  if (typeof target[name] !== 'function') return;
  Object.defineProperty(target, name, {
    configurable: false,
    writable: false,
    value() { throw new Error(`forbidden research transport capability: ${label}`); },
  });
}

for (const name of ['fetch', 'WebSocket', 'EventSource', 'XMLHttpRequest']) {
  blockCapability(globalThis, name, name);
}

for (const name of ['binding', '_linkedBinding']) {
  blockCapability(process, name, `process.${name}`);
}

const originalDlopen = process.dlopen.bind(process);
const allowedFsExtAddon = realpathSync(resolve(
  __dirname,
  '../node_modules/fs-ext/build/Release/fs_ext.node',
));
Object.defineProperty(process, 'dlopen', {
  configurable: false,
  writable: false,
  value(module, filename, flags) {
    let canonical;
    try {
      canonical = typeof filename === 'string' ? realpathSync(filename) : '';
    } catch {
      canonical = '';
    }
    if (canonical !== allowedFsExtAddon) {
      throw new Error('forbidden research transport capability: process.dlopen');
    }
    return flags === undefined
      ? originalDlopen(module, canonical)
      : originalDlopen(module, canonical, flags);
  },
});

if (typeof process.getBuiltinModule === 'function') {
  Object.defineProperty(process, 'getBuiltinModule', {
    configurable: false,
    writable: false,
    value(request) {
      throw new Error(`forbidden research transport import: ${String(request)}`);
    },
  });
}
