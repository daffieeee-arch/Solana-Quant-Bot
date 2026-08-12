import { defaultHttpFetcher, fetchWithTimeout, type HttpFetcher } from './http.js';

export type MarketTicker = { symbol: string; name: string; priceUsd: number; change24hPercent: number };
export type MarketNews = { source: 'CoinDesk'; title: string; url: string; publishedAt: string };
export type MarketContext = { updatedAt: string; ticker: MarketTicker[]; solEur?: number; news: MarketNews[] };
type Fetcher = HttpFetcher;

type CoinGeckoMarket = { id?: unknown; current_price?: unknown; price_change_percentage_24h?: unknown };

const MAX_MARKET_CONTEXT_RESPONSE_BYTES = 1024 * 1024;
const REQUESTED_COINS = [
  { id: 'bitcoin', symbol: 'BTC', name: 'Bitcoin' },
  { id: 'ethereum', symbol: 'ETH', name: 'Ethereum' },
  { id: 'solana', symbol: 'SOL', name: 'Solana' },
  { id: 'binancecoin', symbol: 'BNB', name: 'BNB' },
  { id: 'ripple', symbol: 'XRP', name: 'XRP' },
  { id: 'dogecoin', symbol: 'DOGE', name: 'Dogecoin' },
  { id: 'cardano', symbol: 'ADA', name: 'Cardano' },
  { id: 'avalanche-2', symbol: 'AVAX', name: 'Avalanche' },
] as const;
const REQUESTED_COIN_BY_ID: ReadonlyMap<string, typeof REQUESTED_COINS[number]> = new Map(REQUESTED_COINS.map((coin) => [coin.id, coin]));

const COINGECKO_URL = 'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin,ethereum,solana,binancecoin,ripple,dogecoin,cardano,avalanche-2&price_change_percentage=24h';
const SOL_EUR_URL = 'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=eur';
const COINDESK_RSS_URL = 'https://www.coindesk.com/arc/outboundfeeds/rss/';

export class MarketContextProvider {
  private cached: MarketContext | undefined;
  private readonly fetcher: Fetcher;

  constructor(
    fetcher: Fetcher = defaultHttpFetcher,
    private readonly clock: () => Date = () => new Date(),
    private readonly cacheMs = 5 * 60_000,
    timeoutMs = 10_000,
  ) {
    this.fetcher = (url) => fetchWithTimeout(fetcher, url, undefined, timeoutMs, undefined, MAX_MARKET_CONTEXT_RESPONSE_BYTES);
  }

  async get(): Promise<MarketContext> {
    if (this.cached && this.clock().getTime() - Date.parse(this.cached.updatedAt) < this.cacheMs) return this.cached;
    this.cached = undefined;
    try {
      const [marketResponse, newsResponse, solEurResponse] = await Promise.all([this.fetcher(COINGECKO_URL), this.fetcher(COINDESK_RSS_URL), this.fetcher(SOL_EUR_URL)]);
      if (!marketResponse.ok) throw new Error(`CoinGecko HTTP ${marketResponse.status}`);
      const ticker = normalizeTicker(await marketResponse.json());
      const solEur = solEurResponse.ok ? normalizeSolEur(await solEurResponse.json()) : undefined;
      const news = newsResponse.ok ? parseRss(await newsResponse.text()) : [];
      this.cached = { updatedAt: this.clock().toISOString(), ticker, solEur, news };
      return this.cached;
    } catch (error) { throw error; }
  }
}

function normalizeSolEur(value: unknown): number | undefined { const candidate = (value as { solana?: { eur?: unknown } } | null)?.solana?.eur; const price = typeof candidate === 'number' ? candidate : Number(candidate); return Number.isFinite(price) && price > 0 ? price : undefined; }

function normalizeTicker(value: unknown): MarketTicker[] {
  if (!Array.isArray(value)) throw new Error('CoinGecko returned an invalid market response');
  const normalized = new Map<string, MarketTicker>();
  for (const coin of value as CoinGeckoMarket[]) {
    const id = typeof coin?.id === 'string' ? coin.id : '';
    const requested = REQUESTED_COIN_BY_ID.get(id);
    if (!requested || normalized.has(id)) continue;
    const priceUsd = typeof coin.current_price === 'number' ? coin.current_price : Number(coin.current_price);
    const change24hPercent = typeof coin.price_change_percentage_24h === 'number' ? coin.price_change_percentage_24h : Number(coin.price_change_percentage_24h);
    if (!Number.isFinite(priceUsd) || !Number.isFinite(change24hPercent)) continue;
    normalized.set(id, { symbol: requested.symbol, name: requested.name, priceUsd, change24hPercent });
  }
  return REQUESTED_COINS.flatMap((coin) => {
    const ticker = normalized.get(coin.id);
    return ticker ? [ticker] : [];
  });
}

function parseRss(xml: string): MarketNews[] {
  const news: MarketNews[] = [];
  for (const match of Array.from(xml.slice(0, 500_000).matchAll(/<item\b[^>]*>([\s\S]*?)<\/item>/gi))) {
    const item = match[1];
    const title = cleanRssText(tag(item, 'title'));
    const url = cleanRssText(tag(item, 'link'));
    const publishedAt = Date.parse(cleanRssText(tag(item, 'pubDate')));
    if (!title || !isSafeHttpsUrl(url) || !Number.isFinite(publishedAt)) continue;
    news.push({ source: 'CoinDesk', title, url, publishedAt: new Date(publishedAt).toISOString() });
  }
  return news.sort((left, right) => Date.parse(right.publishedAt) - Date.parse(left.publishedAt)).slice(0, 8);
}

function tag(item: string, name: string): string {
  return new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`, 'i').exec(item)?.[1] ?? '';
}

function cleanRssText(value: string): string {
  return value.replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/i, '$1').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
}

function isSafeHttpsUrl(value: string): boolean {
  try { return new URL(value).protocol === 'https:'; } catch { return false; }
}
