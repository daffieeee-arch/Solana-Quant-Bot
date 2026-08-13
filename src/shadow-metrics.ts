/** Fase-LIVE: shadow-evaluatie observability-aggregator.
 *  Telt per scan-cycle de shadow verdicts + MarketIdentity-compleetheid zodat de
 *  live SHADOW-ONLY-run aantoonbaar maakt dat geldige candidates identities
 *  krijgen en ongeldige om de juiste reden worden afgewezen (zonder enige entry-
 *  enforcement). */

export type ShadowMetrics = {
  candidatesTotal: number;
  byProtocol: Record<string, number>;
  identityComplete: number;
  identityIncomplete: number;
  identityUndefined: number;
  gxOnly: number;
  decimalsKnown: number;
  decimalsMissing: number;
  wouldAccept: number;
  wouldReject: number;
  rejectionReasons: Record<string, number>;
};

const EMPTY: ShadowMetrics = {
  candidatesTotal: 0,
  byProtocol: {},
  identityComplete: 0,
  identityIncomplete: 0,
  identityUndefined: 0,
  gxOnly: 0,
  decimalsKnown: 0,
  decimalsMissing: 0,
  wouldAccept: 0,
  wouldReject: 0,
  rejectionReasons: {},
};

export function emptyShadowMetrics(): ShadowMetrics {
  return { ...EMPTY, byProtocol: {}, rejectionReasons: {} };
}

export function recordShadowObservation(
  m: ShadowMetrics,
  o: {
    verdict: 'WOULD_ACCEPT' | 'WOULD_REJECT';
    reasonCode?: string;
    protocol?: string;
    source: string;
    identityKind?: string | null;
    identityUndefined: boolean;
    gxOnly: boolean;
    decimalsKnown: boolean;
  },
): void {
  m.candidatesTotal += 1;
  const proto = o.protocol ?? '(unknown)';
  m.byProtocol[proto] = (m.byProtocol[proto] ?? 0) + 1;
  if (o.identityUndefined) m.identityUndefined += 1;
  else if (o.identityKind) m.identityComplete += 1;
  else m.identityIncomplete += 1;
  if (o.gxOnly) m.gxOnly += 1;
  if (o.decimalsKnown) m.decimalsKnown += 1;
  else m.decimalsMissing += 1;
  if (o.verdict === 'WOULD_ACCEPT') m.wouldAccept += 1;
  else {
    m.wouldReject += 1;
    const rc = o.reasonCode ?? 'unknown';
    m.rejectionReasons[rc] = (m.rejectionReasons[rc] ?? 0) + 1;
  }
}

export function protocolFromSource(source: string): string {
  if (source.includes('pump')) return 'pump_bonding_curve';
  if (source.includes('ammv4')) return 'amm_v4';
  if (source.includes('cpmm')) return 'cpmm';
  if (source.includes('clmm')) return 'clmm';
  if (source.includes('generic_multidex')) return 'generic_multidex';
  return source || '(unknown)';
}

export function gxOnlyPairId(pairId: string | undefined, mint: string): boolean {
  return (pairId ?? '').toLowerCase() === `gx:${mint.toLowerCase()}`;
}