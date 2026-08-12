import { describe, expect, it } from 'vitest';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadConfig, validatePaperConfig } from '../src/config.js';

const validEnv = {
  MODE: 'paper',
  PAPER_STARTING_SOL: '10',
  MAX_POSITION_SOL: '0.25',
  MAX_CONCURRENT_POSITIONS: '2',
  MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000',
  MAX_LIQUIDITY_USD: '2000000',
  MIN_AGE_MINUTES: '3',
  MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8',
  MIN_VOLUME_M5_USD: '5000',
  STOP_LOSS_PERCENT: '15',
  TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15',
  MAX_HOLD_MINUTES: '45',
  SIMULATED_SLIPPAGE_BPS: '150',
  SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30',
  MAX_CYCLES: '0',
  STRICT_RISK_MODE: 'false',
  DATA_DIR: './data',
};

describe('loadConfig', () => {
  it('accepts explicit paper mode and positive risk limits', () => {
    expect(loadConfig(validEnv)).toMatchObject({
      mode: 'paper',
      paperStartingSol: 10,
      maxPositionSol: 0.25,
      maxDailyLossSol: 0.5,
    });
  });

  it('rejects every mode except paper', () => {
    expect(() => loadConfig({ ...validEnv, MODE: 'live' })).toThrow(
      'This build is paper-only; live execution is not implemented.',
    );
  });

  it('rejects non-positive position limits', () => {
    expect(() => loadConfig({ ...validEnv, MAX_POSITION_SOL: '0' })).toThrow(
      'MAX_POSITION_SOL must be a positive number',
    );
  });

  it('rejects unsafe normalized-score, stop and liquidity-fraction bounds', () => {
    const base = loadConfig(validEnv);
    expect(() => validatePaperConfig({ ...base, minMomentumScore: 101 })).toThrow(/minMomentumScore/i);
    expect(() => validatePaperConfig({ ...base, maxStopLossPercent: 101 })).toThrow(/maxStopLossPercent/i);
    expect(() => validatePaperConfig({ ...base, liquidityPositionFraction: 1.01 })).toThrow(/liquidityPositionFraction/i);
  });

  it('keeps the LAN-only dashboard disabled unless explicitly enabled', () => {
    expect(loadConfig(validEnv)).toMatchObject({ dashboardEnabled: false });
    const config = loadConfig({ ...validEnv, DASHBOARD_ENABLED: 'true', DASHBOARD_PORT: '3000' });
    expect(config).toMatchObject({ dashboardEnabled: true, dashboardPort: 3000 });
    expect(config).not.toHaveProperty('dashboardTokenFile');
  });

  it('loads RPC endpoints from read-only secret files and fails closed', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'scanner-secrets-'));
    const httpFile = join(directory, 'rpc-http');
    const wsFile = join(directory, 'rpc-ws');
    await writeFile(httpFile, 'https://rpc.example.test/?api-key=test-only\n');
    await writeFile(wsFile, 'wss://rpc.example.test/?api-key=test-only\n');

    expect(loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT_FILE: httpFile, RPC_WS_ENDPOINT_FILE: wsFile })).toMatchObject({
      rpcHttpEndpoint: 'https://rpc.example.test/?api-key=test-only',
      rpcWsEndpoint: 'wss://rpc.example.test/?api-key=test-only',
    });
    expect(() => loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT_FILE: join(directory, 'missing') })).toThrow(/RPC_HTTP_ENDPOINT_FILE/);
    expect(() => loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT: 'https://direct.example', RPC_HTTP_ENDPOINT_FILE: httpFile })).toThrow(/both RPC_HTTP_ENDPOINT and RPC_HTTP_ENDPOINT_FILE/);
  });

  it('caps and validates endpoint secret files and URL schemes before activation', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'scanner-secrets-'));
    const oversized = join(directory, 'oversized');
    const multiline = join(directory, 'multiline');
    await writeFile(oversized, 'x'.repeat(8193));
    await writeFile(multiline, 'https://one.example\nhttps://two.example\n');

    expect(() => loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT_FILE: oversized })).toThrow(/8192|8 KiB|byte/i);
    expect(() => loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT_FILE: multiline })).toThrow(/one line|single line/i);
    expect(() => loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT: `https://rpc.example/?key=${'x'.repeat(8193)}` })).toThrow(/8192|8 KiB|byte/i);
    expect(() => loadConfig({ ...validEnv, RPC_HTTP_ENDPOINT: 'http://rpc.example' })).toThrow(/https/i);
    expect(() => loadConfig({ ...validEnv, RPC_WS_ENDPOINT: 'ws://rpc.example' })).toThrow(/wss/i);
  });

  it('accepts at most 100 unique canonical Solana whale addresses', () => {
    const alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
    const addresses = Array.from({ length: 101 }, (_, index) => `${'1'.repeat(30)}${alphabet[Math.floor(index / alphabet.length)]}${alphabet[index % alphabet.length]}`);
    expect(() => loadConfig({ ...validEnv, WHALE_WALLETS: addresses.join(',') })).toThrow(/100/);
    expect(() => loadConfig({ ...validEnv, WHALE_WALLETS: `${addresses[0]},${addresses[0]}` })).toThrow(/duplicate/i);
    expect(() => loadConfig({ ...validEnv, WHALE_WALLETS: 'not-a-solana-address' })).toThrow(/whale wallet/i);
  });
});
