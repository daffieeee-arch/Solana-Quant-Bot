import tls from 'node:tls';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';

// Project-local, certificate-pinned, strictly read-only TrueNAS WSS client.
const host = '192.168.1.234';
// TrueNAS 26 certificate fingerprint — rotated 2026-07-27
const expectedFingerprint = 'AEC15E0DC0F2F7B215D1B4C35067106F68B59A661B31B9FD95829AC4250F6881';
const READ_ONLY_METHODS = new Set([
  'app.image.query',
  'app.query',
  'core.get_jobs',
  'disk.query',
  'filesystem.stat',
  'interface.query',
  'pool.dataset.query',
  'service.query',
  'system.info',
]);
const apiMethod = process.env.TRUENAS_API_METHOD ?? 'system.info';
if (!READ_ONLY_METHODS.has(apiMethod)) {
  throw new Error(`TRUENAS_API_METHOD is not allowlisted for read-only use: ${apiMethod}`);
}

const keyPath = process.env.TRUENAS_API_KEY_PATH || '/opt/data/solana-paper-scanner/secrets/truenasapikey.txt';
const rawKey = readFileSync(keyPath, 'utf8').trim();
// Support both raw API key and "api key: <value>" format
const key = rawKey.includes('api key:')
  ? rawKey.split('\n').find((line) => line.startsWith('api key:'))?.split(':').slice(1).join(':').trim() ?? rawKey
  : rawKey;
const paramsSource = process.env.TRUENAS_API_PARAMS_FILE
  ? readFileSync(process.env.TRUENAS_API_PARAMS_FILE, 'utf8')
  : (process.env.TRUENAS_API_PARAMS ?? '[]');
const apiParams = JSON.parse(paramsSource);

if (!key) throw new Error('Local API-key file is empty.');
if (!Array.isArray(apiParams)) throw new Error('TRUENAS_API_PARAMS must be a JSON array.');

let state = 'tls_connect';
let upgraded = false;
let buffer = Buffer.alloc(0);
let done = false;
const socket = tls.connect({ host, port: 443, rejectUnauthorized: false });

const finish = (line, failed = false) => {
  if (done) return;
  done = true;
  clearTimeout(timer);
  console.log(line);
  process.exitCode = failed ? 1 : 0;
  socket.destroy();
};

const timer = setTimeout(() => finish(`secure_api_readonly_test=timeout phase=${state}`, true), 12_000);

function sendText(payload) {
  const data = Buffer.from(payload);
  const mask = crypto.randomBytes(4);
  let header;

  if (data.length < 126) {
    header = Buffer.from([0x81, 0x80 | data.length]);
  } else if (data.length <= 0xffff) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 0x80 | 126;
    header.writeUInt16BE(data.length, 2);
  } else {
    throw new Error('Request payload unexpectedly large.');
  }

  const masked = Buffer.alloc(data.length);
  for (let index = 0; index < data.length; index += 1) {
    masked[index] = data[index] ^ mask[index % 4];
  }
  socket.write(Buffer.concat([header, mask, masked]));
}

function processFrames() {
  while (buffer.length >= 2) {
    const opcode = buffer[0] & 0x0f;
    const masked = Boolean(buffer[1] & 0x80);
    let length = buffer[1] & 0x7f;
    let offset = 2;

    if (length === 126) {
      if (buffer.length < 4) return;
      length = buffer.readUInt16BE(2);
      offset = 4;
    } else if (length === 127) {
      finish('secure_api_readonly_test=unexpected_large_frame', true);
      return;
    }

    const maskLength = masked ? 4 : 0;
    if (buffer.length < offset + maskLength + length) return;

    const mask = masked ? buffer.subarray(offset, offset + 4) : null;
    offset += maskLength;
    const payload = Buffer.from(buffer.subarray(offset, offset + length));
    buffer = buffer.subarray(offset + length);

    if (mask) {
      for (let index = 0; index < payload.length; index += 1) {
        payload[index] ^= mask[index % 4];
      }
    }

    if (opcode === 0x8) {
      finish('secure_api_readonly_test=server_closed_connection', true);
      return;
    }
    if (opcode === 0x9) {
      socket.write(Buffer.from([0x8a, 0x00]));
      continue;
    }
    if (opcode !== 0x1) continue;

    let message;
    try {
      message = JSON.parse(payload.toString('utf8'));
    } catch {
      continue;
    }

    if (message.id === 1) {
      if (message.error || message.result !== true) {
        finish('secure_api_readonly_test=authentication_failed', true);
        return;
      }
      state = apiMethod;
      sendText(JSON.stringify({ jsonrpc: '2.0', id: 2, method: apiMethod, params: apiParams }));
    } else if (message.id === 2) {
      if (message.error) {
        finish(`secure_api_readonly_test=method_denied method=${apiMethod} code=${message.error.code ?? 'unknown'}`, true);
        return;
      }
      const result = message.result ?? {};
      if (apiMethod === 'system.info') {
        finish(`secure_api_readonly_test=success method=system.info version=${String(result.version ?? 'unknown')} hostname=${String(result.hostname ?? 'unknown')} certificate_pinned=true`);
      } else {
        finish(JSON.stringify({ secure_api_readonly_test: 'success', method: apiMethod, certificate_pinned: true, result }));
      }
      return;
    }
  }
}

socket.once('secureConnect', () => {
  const certificate = socket.getPeerCertificate(true);
  const actualFingerprint = certificate.raw
    ? crypto.createHash('sha256').update(certificate.raw).digest('hex').toUpperCase()
    : '';

  if (actualFingerprint !== expectedFingerprint) {
    finish('secure_api_readonly_test=certificate_mismatch', true);
    return;
  }

  state = 'websocket_upgrade';
  const websocketKey = crypto.randomBytes(16).toString('base64');
  socket.write([
    'GET /api/current HTTP/1.1',
    `Host: ${host}`,
    'Upgrade: websocket',
    'Connection: Upgrade',
    `Sec-WebSocket-Key: ${websocketKey}`,
    'Sec-WebSocket-Version: 13',
    '',
    '',
  ].join('\r\n'));
});

socket.on('data', (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);

  if (!upgraded) {
    const headerBoundary = buffer.indexOf('\r\n\r\n');
    if (headerBoundary < 0) return;

    const header = buffer.subarray(0, headerBoundary).toString('utf8');
    buffer = buffer.subarray(headerBoundary + 4);

    if (!header.startsWith('HTTP/1.1 101')) {
      finish('secure_api_readonly_test=websocket_upgrade_failed', true);
      return;
    }

    upgraded = true;
    state = 'authenticate';
    sendText(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'auth.login_with_api_key', params: [key] }));
  }

  processFrames();
});

socket.on('error', () => finish(`secure_api_readonly_test=connection_error phase=${state}`, true));
