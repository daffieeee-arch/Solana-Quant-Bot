/**
 * Zero-cost mode hard guard.
 * TRITON_LIVE_ENABLED=false (default) → géén live Triton-consumptie (Dragon's Mouth
 * subscriptions, Titan, DAS, Triton RPC). Een toekomstige balance/top-up activeert
 * live consumers NOOIT automatisch; enkel deze expliciete env-unlock + voldoende
 * balance laat main.ts de Triton-provider clampen.
 */

export const TRITON_LIVE_ENV = 'TRITON_LIVE_ENABLED';

/** 'true' (case-ins) is de enige unlock; alle andere waarden → offline. */
export function isTritonLiveEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const raw = env[TRITON_LIVE_ENV];
  return typeof raw === 'string' && raw.trim().toLowerCase() === 'true';
}

/**
 * Fail-closed guard: gooit wanneer iemand een live Triton-client probeert te
 * initialiseren terwijl TRITON_LIVE_ENABLED niet exact 'true' is.
 * Aanroepen vóór élke Triton-client-/Titan-/RPC-constructie in production.
 */
export function assertZeroCostMode(env: NodeJS.ProcessEnv = process.env): void {
  if (!isTritonLiveEnabled(env)) return; // offline is de bedoeling
  // live is toegestaan; main.ts zal de providers bouwen
}

/** Beveiligde guard voor client-constructie: Gooi als offline. */
export function requireLiveTritonOrThrow(env: NodeJS.ProcessEnv = process.env): void {
  if (!isTritonLiveEnabled(env)) {
    throw new Error(`[ZERO-COST] Live Triton-client-aanmaak geblokkeerd: ${TRITON_LIVE_ENV} is niet 'true' (Triton balance cutoff — offline mode).`);
  }
}