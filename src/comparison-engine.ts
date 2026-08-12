import type { StrategyPerformance } from './strategy-registry.js';
import type { OpportunityRecord } from './opportunity-storage.js';

/** Statistical comparison of two trading strategies */
export class ComparisonEngine {
  /** Compute performance metrics from a list of completed trade opportunities */
  computePerformance(trades: OpportunityRecord[]): StrategyPerformance {
    const completed = trades.filter((trade) => trade.learningSchemaVersion === 2 && trade.decision === 'exit'
      && typeof trade.pnlSol === 'number' && Number.isFinite(trade.pnlSol));
    if (completed.length < 10) {
      return this.emptyPerformance();
    }

    const pnls = completed.map((t) => t.pnlSol!);
    const wins = pnls.filter((p) => p > 0);
    const losses = pnls.filter((p) => p <= 0);

    const grossProfit = wins.reduce((a, b) => a + b, 0);
    const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));

    const winRate = wins.length / completed.length;
    const avgWin = wins.length > 0 ? grossProfit / wins.length : 0;
    const avgLoss = losses.length > 0 ? grossLoss / losses.length : 0;
    const expectancy = (winRate * avgWin) - ((1 - winRate) * avgLoss);

    // Sharpe: (mean return) / std of returns
    const meanReturn = pnls.reduce((a, b) => a + b, 0) / pnls.length;
    const variance = pnls.reduce((sum, p) => sum + (p - meanReturn) ** 2, 0) / pnls.length;
    const stdReturn = Math.sqrt(variance);
    const sharpeRatio = stdReturn > 0 ? meanReturn / stdReturn : 0;

    // Sortino: uses only downside deviation
    const downsidePnls = pnls.filter((p) => p < 0);
    const downsideVariance = downsidePnls.length > 0
      ? downsidePnls.reduce((sum, p) => sum + p ** 2, 0) / downsidePnls.length
      : 0;
    const downsideDeviation = Math.sqrt(downsideVariance);
    const sortinoRatio = downsideDeviation > 0 ? meanReturn / downsideDeviation : meanReturn > 0 ? 999 : 0;

    // Max drawdown from cumulative PnL
    let peak = 0;
    let cumulative = 0;
    let maxDD = 0;
    for (const p of pnls) {
      cumulative += p;
      if (cumulative > peak) peak = cumulative;
      const dd = peak - cumulative;
      if (dd > maxDD) maxDD = dd;
    }

    // Median
    const sorted = [...pnls].sort((a, b) => a - b);
    const median = sorted.length % 2 === 0
      ? (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2
      : sorted[Math.floor(sorted.length / 2)];

    // Profit factor
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 999 : 0;

    return {
      netReturn: cumulative, // cumulative PnL in SOL
      profitFactor,
      sharpeRatio,
      sortinoRatio,
      maxDrawdown: maxDD,
      winRate: winRate * 100,
      avgWin,
      avgLoss,
      totalTrades: completed.length,
      expectancy,
      medianTrade: median,
      volatility: stdReturn,
    };
  }

  /** Compare challenger vs champion — returns true if challenger is statistically better */
  compare(champion: StrategyPerformance, challenger: StrategyPerformance): ComparisonResult {
    const reasons: string[] = [];
    let score = 0;

    // 1. Higher net return
    if (challenger.netReturn > champion.netReturn) {
      score += 3;
      reasons.push(`Higher net return: ${challenger.netReturn.toFixed(4)} vs ${champion.netReturn.toFixed(4)} SOL`);
    }

    // 2. Higher Sharpe
    if (challenger.sharpeRatio > champion.sharpeRatio * 1.1) {
      score += 2;
      reasons.push(`Sharpe: ${challenger.sharpeRatio.toFixed(2)} vs ${champion.sharpeRatio.toFixed(2)}`);
    }

    // 3. Lower max drawdown
    if (challenger.maxDrawdown < champion.maxDrawdown * 0.9) {
      score += 2;
      reasons.push(`Lower drawdown: ${challenger.maxDrawdown.toFixed(4)} vs ${champion.maxDrawdown.toFixed(4)} SOL`);
    }

    // 4. Higher win rate (at least 5% higher)
    if (challenger.winRate > champion.winRate + 5) {
      score += 1;
      reasons.push(`Win rate: ${challenger.winRate.toFixed(1)}% vs ${champion.winRate.toFixed(1)}%`);
    }

    // 5. Higher expectancy
    if (challenger.expectancy > champion.expectancy * 1.2) {
      score += 2;
      reasons.push(`Expectancy: ${challenger.expectancy.toFixed(4)} vs ${champion.expectancy.toFixed(4)} SOL`);
    }

    // 6. More trades (larger sample = more reliable)
    if (challenger.totalTrades > champion.totalTrades * 1.5) {
      score += 1;
      reasons.push(`Sample size: ${challenger.totalTrades} vs ${champion.totalTrades} trades`);
    }

    // Minimum trade count for reliability
    if (challenger.totalTrades < 20) {
      reasons.push(`WARNING: Only ${challenger.totalTrades} trades — results not statistically significant`);
      score -= 3;
    }

    const shouldPromote = score >= 4 && challenger.netReturn > champion.netReturn &&
      challenger.totalTrades >= 20;

    return { shouldPromote, score, reasons, champion, challenger };
  }

  private emptyPerformance(): StrategyPerformance {
    return {
      netReturn: 0, profitFactor: 0, sharpeRatio: 0, sortinoRatio: 0, maxDrawdown: 0,
      winRate: 0, avgWin: 0, avgLoss: 0, totalTrades: 0, expectancy: 0,
      medianTrade: 0, volatility: 0,
    };
  }
}

export interface ComparisonResult {
  shouldPromote: boolean;
  score: number;
  reasons: string[];
  champion: StrategyPerformance;
  challenger: StrategyPerformance;
}
