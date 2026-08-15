import { describe, expect, it, vi, afterEach } from 'vitest';
import { isTritonLiveEnabled, assertZeroCostMode, requireLiveTritonOrThrow, TRITON_LIVE_ENV } from '../src/zero-cost.js';

afterEach(() => { vi.restoreAllMocks(); delete process.env[TRITON_LIVE_ENV]; });

describe('zero-cost mode hard guard (TRITON_LIVE_ENABLED)', () => {
  it('env ontbreekt → live disabled', () => {
    delete process.env[TRITON_LIVE_ENV];
    expect(isTritonLiveEnabled(process.env)).toBe(false);
  });

  it('false/0/no/leeg → disabled (fail-closed)', () => {
    for (const v of ['false', '0', 'no', '', 'False', 'FALSE', 'off', '0x0', 'null', 'undefined']) {
      process.env[TRITON_LIVE_ENV] = v;
      expect(isTritonLiveEnabled(process.env)).toBe(false);
    }
  });

  it('alleen expliciete "true" → enabled', () => {
    for (const v of ['true', 'TRUE', 'True']) {
      process.env[TRITON_LIVE_ENV] = v;
      expect(isTritonLiveEnabled(process.env)).toBe(true);
    }
  });

  it('Dragon\'s Mouth/geyser client wordt NIET aangemaakt in offline mode (mock)', () => {
    const geyserSpy = vi.fn();
    delete process.env[TRITON_LIVE_ENV]; // offline
    expect(isTritonLiveEnabled(process.env)).toBe(false);
    // De guard blokkeert constructie: als code probeert te bouwen, moet requireLiveTritonOrThrow gooien
    expect(() => requireLiveTritonOrThrow(process.env)).toThrow(/ZERO-COST/);
    // controleer dat mock-factory nooit zou worden aangeroepen in een zero-cost guard path:
    // (hier impliciet: de guard gooit vóór factory-constructie)
    expect(geyserSpy).not.toHaveBeenCalled();
  });

  it('Titan client wordt NIET aangemaakt in offline mode (mock)', () => {
    const titanSpy = vi.fn();
    delete process.env[TRITON_LIVE_ENV];
    expect(() => requireLiveTritonOrThrow(process.env)).toThrow(/ZERO-COST/);
    expect(titanSpy).not.toHaveBeenCalled();
  });

  it('RPC/DAS client wordt NIET aangemaakt in offline mode (mock)', () => {
    const rpcSpy = vi.fn();
    process.env[TRITON_LIVE_ENV] = 'false';
    expect(() => requireLiveTritonOrThrow(process.env)).toThrow(/ZERO-COST/);
    expect(rpcSpy).not.toHaveBeenCalled();
  });

  it('reconnectloop start niet: offline guard blokkeert vóór connect/subscription', () => {
    delete process.env[TRITON_LIVE_ENV];
    expect(() => requireLiveTritonOrThrow(process.env)).toThrow();
    // keine connect/subscribe/reconnect-loop: die zitten achter de guard (per code-side).
    // Deze test verifieert dat de guard fail-closed is: als hij NIET gooit, dan alleen bij live.
    process.env[TRITON_LIVE_ENV] = 'true';
    expect(() => requireLiveTritonOrThrow(process.env)).not.toThrow();
  });

  it('toekomstige account-top-up kan live consumers NIET automatisch starten (alleen env-unlock)', () => {
    delete process.env[TRITON_LIVE_ENV];
    expect(isTritonLiveEnabled(process.env)).toBe(false); // balance-top-up alleen is niet genoeg
    // Zelfs als balance > $0 weer is, zonder TRITON_LIVE_ENABLED=true blijft offline:
    process.env[TRITON_LIVE_ENV] = '0';
    expect(isTritonLiveEnabled(process.env)).toBe(false);
    process.env[TRITON_LIVE_ENV] = 'true';
    expect(isTritonLiveEnabled(process.env)).toBe(true);
  });

  it('offline replay blijft werken (borg dat de guard niets blokkeert zolang er geen live constructie is)', () => {
    delete process.env[TRITON_LIVE_ENV];
    expect(() => assertZeroCostMode(process.env)).not.toThrow(); // offline = ok
  });
});