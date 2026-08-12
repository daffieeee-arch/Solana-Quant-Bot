export type RiskStatus = 'clear' | 'unknown' | 'flagged';

export type RiskDecision =
  | { allowed: true }
  | { allowed: false; reason: 'risk_unknown_in_strict_mode' | 'risk_flagged' };

export function canPaperEnter(riskStatus: RiskStatus, strictRiskMode: boolean): RiskDecision {
  if (riskStatus === 'flagged') return { allowed: false, reason: 'risk_flagged' };
  if (riskStatus === 'unknown' && strictRiskMode) {
    return { allowed: false, reason: 'risk_unknown_in_strict_mode' };
  }
  return { allowed: true };
}
