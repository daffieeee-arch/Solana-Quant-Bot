import { V1Client } from '@titanexchange/sdk-ts';
import bs58 from 'bs58';

const MAX_DIAGNOSTICS = 500;
const MAX_CACHE_ENTRIES = 5_000;
const SOL_MINT = 'So11111111111111111111111111111111111111112';
// Token-lamports decimaal voor het input (SOL) → gebruik 1 SOL input voor de quote.
const SOL_DECIMALS = 9;

/**
 * Titan Swap API price-quote provider (Argos meta-aggregator). Fetches a LIVE
 * aggregated best-route execution price for an exact token mint via the Triton
 * Titan Swap WebSocket API (SDK @titanexchange/sdk-ts):
 *
 *   wss://<endpoint>/<token>/titan/api/v1/ws
 *   client.getSwapPrice({ inputMint: SOL, outputMint: mint, amount })
 *     → { amountIn, amountOut }  →  SOL-per-token = amountIn / amountOut
 *
 * Dit is de Triton-gedocumenteerde route (Titan is websocket-only; er is GEEN
 * REST quote/price endpoint). Bounded, cached, fail-closed. De scanner gebruikt
 * dit voor de echte multi-venue fill-prijs van een entry+trade-kosten.
 */
export type TitanQuote = {
  price: number;          // SOL per token (input SOL)
  inputMint: string;
  outputMint: string;
  amountIn: number;
  amountOut: number;
  source: 'titan';
};

export class TitanQuoteProvider {
  private readonly diagnostics: string[] = [];
  private readonly cache = new Map<string, { expiresAt: number; quote: TitanQuote }>();
  private clientPromise: Promise<V1Client> | undefined;
  private readonly wsUrl: string;
  private readonly cacheMs: number;
  private readonly clock: () => number;

  constructor(
    endpoint: string,
    token: string,
    private readonly timeoutMs = 8_000,
    cacheMs = 10_000,
    clock: () => number = () => Date.now(),
  ) {
    if (!endpoint || endpoint.includes('\n') || endpoint.includes('\r')) {
      throw new Error('Titan endpoint must be a non-empty single line');
    }
    if (!token || token.includes('\n') || token.includes('\r')) {
      throw new Error('Titan token must be a non-empty single line');
    }
    // Titan is websocket-only op Triton: geen REST quote endpoint.
    this.wsUrl = `wss://${endpoint.replace(/^https?:/i, '').replace(/^wss?:/i, '')}/${token}/titan/api/v1/ws`;
    this.cacheMs = cacheMs;
    this.clock = clock;
  }

  private pushDiagnostic(message: string): void {
    if (this.diagnostics.length >= MAX_DIAGNOSTICS) this.diagnostics.shift();
    this.diagnostics.push(message);
  }

  drainDiagnostics(): string[] {
    return this.diagnostics.splice(0);
  }

  /** Eén gedeelde V1Client-verbinding (lazy, reconnect bij falltimeout). */
  private async client(): Promise<V1Client | undefined> {
    if (this.clientPromise) return this.clientPromise;
    this.clientPromise = (async () => {
      try {
        return await V1Client.connect(this.wsUrl);
      } catch (error) {
        this.clientPromise = undefined;
        this.pushDiagnostic(`titan: connect faal: ${error instanceof Error ? error.message : String(error)}`);
        throw error;
      }
    })();
    return this.clientPromise;
  }

  /**
   * Fetch a live route-quoted price (SOL input → mint output). Returns a TitanQuote,
   * or undefined if unavailable (fail-closed).
   */
  async fetchQuote(options: { outputMint: string; amountLamports?: number }): Promise<TitanQuote | undefined> {
    const { outputMint } = options;
    if (!isMint(outputMint)) return undefined;
    const amountLamports = options.amountLamports ?? 10 ** SOL_DECIMALS; // 1 SOL default probe
    if (!Number.isFinite(amountLamports) || amountLamports <= 0) return undefined;

    const key = `${outputMint}\0${amountLamports}`;
    const now = this.clock();
    const cached = this.cache.get(key);
    if (cached && cached.expiresAt > now) return cached.quote;

    const client = await this.client();
    if (!client) return undefined;

    try {
      const response = await withTimeout(
        client.getSwapPrice({
          inputMint: bs58.decode(SOL_MINT),
          outputMint: bs58.decode(outputMint),
          amount: amountLamports,
        }),
        this.timeoutMs,
      );
      const amountOut = toNumber(response.amountOut);
      if (amountOut === undefined || amountOut <= 0 || !Number.isFinite(amountOut)) {
        this.pushDiagnostic('titan: invalid quote payload (amountOut<=0)');
        return undefined;
      }
      const price = amountLamports / amountOut; // SOL per token
      if (!Number.isFinite(price) || price <= 0) {
        this.pushDiagnostic('titan: invalid quote price');
        return undefined;
      }
      const quote: TitanQuote = {
        price,
        inputMint: SOL_MINT,
        outputMint,
        amountIn: amountLamports,
        amountOut,
        source: 'titan',
      };
      this.cache.set(key, { expiresAt: this.clock() + this.cacheMs, quote });
      return quote;
    } catch (error) {
      // verbreek de verbinding op timeout zodat de volgende call reconnect
      this.clientPromise = undefined;
      this.pushDiagnostic(`titan: ${error instanceof Error ? error.message : String(error)}`);
      return undefined;
    }
  }

  /** Convert SOL-per-token price to USD using SOL price. */
  quoteToUsd(quote: TitanQuote, solPriceUsd: number): number | undefined {
    if (!Number.isFinite(solPriceUsd) || solPriceUsd <= 0) return undefined;
    return quote.price * solPriceUsd;
  }

  destroy(): void {
    if (this.clientPromise) {
      this.clientPromise.then((c) => c.close()).catch(() => undefined);
      this.clientPromise = undefined;
    }
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timeout ${ms}ms`)), ms);
    promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

function toNumber(value: number | bigint | undefined): number | undefined {
  if (value === undefined) return undefined;
  const n = typeof value === 'bigint' ? Number(value) : value;
  return Number.isFinite(n) ? n : undefined;
}

function isMint(value: string): boolean {
  return typeof value === 'string' && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value);
}