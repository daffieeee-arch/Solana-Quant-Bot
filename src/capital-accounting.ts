import type { PaperPosition } from './portfolio.js';

/** Fase-QH: quarantine/exposure accounting.
 *  Quarantinede posities: geen actieve concurrency-slot, geen paper_exit, geen
 *  realized PnL; hun kapitaal is 'unknown exposure' (niet stilzwijgend vrij).
 *  Het vrijmaken van paperkapitaal voor verdere tests gebeurt via een expliciet
 *  administratief event (administrative_capital_adjustment) dat strategy-PnL niet
 *  wijzigt. */

export const CAPITAL_ADJUSTMENT_EVENT_TYPE = 'administrative_capital_adjustment';

export type ExposureSnapshot = {
  activeExposureLamports: number;
  quarantinedExposureLamports: number;
  unknownExposureLamports: number;
  availablePaperCapitalLamports: number;
  realizedCapitalLamports: number;
};

/** Expliciete exposure-modellering; kapitaal van quarantined posities wordt
 *  als 'unknown exposure' gerapporteerd (niet als vrij beschikbaar). */
export function computeExposure(input: {
  active: readonly PaperPosition[];
  quarantined: readonly PaperPosition[];
  availableLamports: number;
  realizedPnlLamports: number;
}): ExposureSnapshot {
  const activeExposure = input.active.reduce((t, p) => t + p.allocatedLamports, 0);
  const quarantinedExposure = input.quarantined.reduce((t, p) => t + p.allocatedLamports, 0);
  return {
    activeExposureLamports: activeExposure,
    quarantinedExposureLamports: quarantinedExposure,
    unknownExposureLamports: quarantinedExposure, // state = UNKNOWN
    availablePaperCapitalLamports: input.availableLamports,
    realizedCapitalLamports: input.realizedPnlLamports,
  };
}

export type AdminCapitalAdjustment = {
  type: typeof CAPITAL_ADJUSTMENT_EVENT_TYPE;
  at: string;
  /** delta op beschikbaar kapitaal (negatief = release/vrijgave, positief = return) */
  adminCapitalAdjustmentLamports: number;
  availableAfter: number;
  /** strategy-PnL onveranderd */
  realizedPnlAfter: number;
  schemaVersion: number;
};

/** Administratieve kapitaal-vrijgave voor testdoeleinden. Wijzigt nooit realized
 *  strategy-PnL. Duidelijke audit-trail in het WAL-event. */
export function applyAdministrativeCapitalAdjustment(
  input: { availableLamports: number; realizedPnlLamports: number },
  adjustmentLamports: number,
  at: string,
): AdminCapitalAdjustment {
  return {
    type: CAPITAL_ADJUSTMENT_EVENT_TYPE,
    at,
    adminCapitalAdjustmentLamports: adjustmentLamports,
    availableAfter: input.availableLamports + adjustmentLamports,
    realizedPnlAfter: input.realizedPnlLamports,
    schemaVersion: 1,
  };
}
