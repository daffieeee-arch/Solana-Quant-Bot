import type { ScanDecision } from './scanner.js';
import type { MarketSnapshot } from './scoring.js';

export type Observation = {
  cycle: number;
  learningSchemaVersion?: 2;
  checkedAt: string;
  at: string;
  pairId: string;
  mint: string;
  symbol: string;
  strategyId: string;
  tradeId?: string;
  score?: number;
  token: {
    mint: string;
    symbol: string;
    pairId: string;
  };
  dex: string;
  source: string;
  priceUsd: number;
  liquidityUsd: number | undefined;
  volumeM5Usd: number | undefined;
  priceChangeM5Percent: number | undefined;
  buysM5: number | undefined;
  sellsM5: number | undefined;
  entryPriceUsd: number | undefined;
  pnlLamports: number;
  openedAt: string;
  closedAt: string;
  outcome: 'win' | 'loss' | 'open';
  holdMinutes: number;
  decision: 'entry' | 'exit' | 'rejected' | 'duplicate' | 'skipped' | 'too_late';
  // Exit metadata (populated only for paper_exit decisions) — required by
  // OpportunityStorage to complete a matched entry+exit pair.
  exitPriceUsd?: number;
  exitReason?: string;
  pnlSol?: number;
  holdingMinutes?: number;
  position: {
    pairId: string;
    mint: string;
    entryPriceUsd: number;
    pnlLamports: number;
    openedAt: string;
    closedAt: string;
    outcome: 'win' | 'loss' | 'open';
  };
};

function extractMint(decision: ScanDecision): string {
  return 'mint' in decision ? decision.mint : decision.pairId.split('-')[0];
}

function extractSymbol(decision: ScanDecision): string {
  return 'symbol' in decision ? decision.symbol : '';
}

function extractPosition(decision: ScanDecision) {
  if (decision.type === 'paper_entry') {
    return {
      pairId: decision.pairId,
      mint: decision.mint,
      entryPriceUsd: decision.entryPriceUsd,
      pnlLamports: 0,
      openedAt: decision.openedAt,
      closedAt: '',
      outcome: 'open' as const,
    };
  }
  if (decision.type === 'paper_exit') {
    return {
      pairId: decision.pairId,
      mint: decision.mint,
      entryPriceUsd: decision.exitPriceUsd,
      pnlLamports: decision.pnlLamports,
      openedAt: decision.openedAt,
      closedAt: '',
      outcome: 'loss' as const,
    };
  }
  return {
    pairId: decision.pairId,
    mint: extractMint(decision),
    entryPriceUsd: 0,
    pnlLamports: 0,
    openedAt: '',
    closedAt: '',
    outcome: 'open' as const,
  };
}

export function buildLearningObservations(
  championId: string,
  snapshots: readonly MarketSnapshot[],
  decisions: readonly ScanDecision[],
  checkedAt: string,
): Observation[] {
  return decisions.map((decision) => {
    const snapshot = snapshots.find((s) => s.pairId === decision.pairId && s.mint === extractMint(decision));
    if (!snapshot) {
      throw new Error(`No snapshot for decision pairId=${decision.pairId} mint=${extractMint(decision)}`);
    }
    const position = extractPosition(decision);
    const isExit = decision.type === 'paper_exit';
    return {
      cycle: 0,
      learningSchemaVersion: 2,
      checkedAt,
      at: checkedAt,
      pairId: decision.pairId,
      mint: extractMint(decision),
      symbol: extractSymbol(decision) || snapshot.symbol,
      strategyId: championId,
      tradeId: 'tradeId' in decision ? (decision as { tradeId?: string }).tradeId : undefined,
      score: 'score' in decision ? (decision as { score?: number }).score : undefined,
      token: {
        mint: extractMint(decision),
        symbol: extractSymbol(decision) || snapshot.symbol,
        pairId: decision.pairId,
      },
      dex: snapshot.source.includes('pump') ? 'pump-fun' : snapshot.source.includes('raydium') ? 'raydium' : snapshot.source.includes('birdeye') ? 'birdeye' : 'geckoterminal',
      source: snapshot.source,
      priceUsd: snapshot.priceUsd,
      liquidityUsd: snapshot.liquidityUsd,
      volumeM5Usd: snapshot.volumeM5Usd,
      priceChangeM5Percent: snapshot.priceChangeM5Percent,
      buysM5: snapshot.buysM5,
      sellsM5: snapshot.sellsM5,
      entryPriceUsd: position.entryPriceUsd,
      pnlLamports: position.pnlLamports,
      openedAt: position.openedAt,
      closedAt: position.closedAt,
      outcome: position.outcome,
      holdMinutes: 0,
      decision: decision.type === 'paper_entry' ? 'entry' : isExit ? 'exit' : 'rejected',
      // Populate exit metadata so OpportunityStorage can complete the pair.
      ...(isExit ? {
        exitPriceUsd: decision.exitPriceUsd,
        exitReason: decision.reason,
        pnlSol: decision.pnlLamports / 1e9,
        holdingMinutes: Math.max(0, (Date.parse(checkedAt) - Date.parse(decision.openedAt)) / 60_000),
      } : {}),
      position,
    };
  });
}