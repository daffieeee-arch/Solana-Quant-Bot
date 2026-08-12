import https from 'node:https';
import crypto from 'node:crypto';
import { createWriteStream, unlinkSync } from 'node:fs';

const host = '192.168.1.234';
const expectedFingerprint = 'AEC15E0DC0F2F7B215D1B4C35067106F68B59A661B31B9FD95829AC4250F6881';
const relativeUrl = process.env.TRUENAS_DOWNLOAD_URL;
const outputPath = process.env.TRUENAS_DOWNLOAD_OUTPUT;
if (!relativeUrl?.startsWith('/')) throw new Error('TRUENAS_DOWNLOAD_URL must be a relative path');
if (!outputPath) throw new Error('TRUENAS_DOWNLOAD_OUTPUT is required');

let verified = false;
const request = https.get({ host, port: 443, path: relativeUrl, rejectUnauthorized: false }, (response) => {
  if (!verified) {
    response.resume();
    request.destroy(new Error('TLS certificate was not fingerprint-verified'));
    return;
  }
  if (response.statusCode !== 200) {
    response.resume();
    request.destroy(new Error(`Unexpected HTTP status ${response.statusCode}`));
    return;
  }
  const file = createWriteStream(outputPath, { flags: 'wx', mode: 0o600 });
  response.pipe(file);
  file.on('finish', () => file.close(() => console.log('download=success certificate_pinned=true')));
  file.on('error', (error) => {
    try { unlinkSync(outputPath); } catch {}
    request.destroy(error);
  });
});

request.on('socket', (socket) => {
  socket.once('secureConnect', () => {
    const certificate = socket.getPeerCertificate(true);
    const actualFingerprint = certificate.raw
      ? crypto.createHash('sha256').update(certificate.raw).digest('hex').toUpperCase()
      : '';
    if (actualFingerprint !== expectedFingerprint) {
      request.destroy(new Error('Certificate fingerprint mismatch'));
      return;
    }
    verified = true;
  });
});
request.on('error', (error) => {
  try { unlinkSync(outputPath); } catch {}
  console.error(`download=failed reason=${error.message}`);
  process.exitCode = 1;
});
request.setTimeout(30_000, () => request.destroy(new Error('Download timeout')));
