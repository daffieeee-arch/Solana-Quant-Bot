import type { MarketSnapshot } from './scoring.js';

/**
 * Rug / honeypot risk assessment (Triton-first).
 *
 * Uses on-chain data Triton exposes via DAS `getAsset` + `getTokenLargestAccounts`:
 *   - `burnt`          : bool — supply partially/mostly burned (strong anti-rug).
 *   - `mutable`        : bool — metadata mutable (team can change name/icon).
 *   - `authorities`    : list of {address, scopes} — leftover mint/freeze authority.
 *   - holder concentration from the largest token accounts.
 *
 * Returns a risk CLASS + a normalized risk score (0 = safe, 1 = rug-like) that
 * callers use to reject or down-rank a candidate. Fail-closed (missing data →
 * treated as elevated risk, because unknown tokens are riskier).
 */

export type RugRisk = {
  /** Literal class label for dashboards/logs. */
  className: 'clean' | 'moderate' | 'high';
  /** 0..1, higher = riskier. */
  score: number;
  /** Human-readable flags that contributed. */
  flags: string[];
};

export type RugEvidence = {
  burnt?: boolean;
  mutable?: boolean;
  /** Number of leftover authorities with 'full' scope (mint/freeze). */
  fullAuthorityCount?: number;
  /** Top-holder concentration 0..1 (share of total supply held by top accounts). */
  holderConcentration?: number;
};

export function assessRugRisk(evidence: RugEvidence | undefined): RugRisk {
  const flags: string[] = [];
  if (!evidence) {
    return { className: 'high', score: 1, flags: ['no_rug_evidence'] };
  }
  let score = 0;

  // Authority left: strong rug vector (team can mint / freeze holders out).
  const fullAuthorities = evidence.fullAuthorityCount ?? 0;
  if (fullAuthorities > 0) {
    score += 0.3;
    flags.push(`full_authority_${fullAuthorities}`);
  }

  // Mutable metadata: team can rename/repoint — mild rug vector.
  if (evidence.mutable) {
    score += 0.15;
    flags.push('mutable_metadata');
  }

  // Holder concentration: top accounts hold too much → dump risk.
  const concentration = evidence.holderConcentration;
  if (concentration === undefined) {
    flags.push('holder_concentration_unknown');
  } else if (concentration >= 0.5 && concentration < 0.8) {
    score += 0.2;
    flags.push('top_holder_concentration');
  } else if (concentration >= 0.8) {
    score += 0.35;
    flags.push('extreme_holder_concentration');
  }

  // Burned supply is a strong protector — reduces overall risk.
  if (evidence.burnt) {
    score -= 0.4;
    flags.push('supply_burnt');
  }

  const clamped = Math.max(0, Math.min(1, score));
  return {
    className: clamped >= 0.5 ? 'high' : clamped >= 0.25 ? 'moderate' : 'clean',
    score: Number(clamped.toFixed(3)),
    flags,
  };
}

/** Convenience: attach rug classification to a snapshot for the dashboard/score. */
export function isRugRisky(risk: RugRisk, threshold = 0.5): boolean {
  return risk.score >= threshold;
}
