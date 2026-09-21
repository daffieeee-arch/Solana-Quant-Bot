import { describe, expect, it } from 'vitest';
import { MarketContextProvider } from '../src/providers/market-context.js';

const coinResponse = [
  { id: 'bitcoin', symbol: 'btc', name: 'Bitcoin', current_price: 64_000, price_change_percentage_24h: 1.25 },
  { id: 'solana', symbol: 'sol', name: 'Solana', current_price: 150, price_change_percentage_24h: -2.5 },
];
const rss = `<?xml version="1.0"?><rss><channel>
  <item><title><![CDATA[Bitcoin sees renewed interest]]></title><link>https://www.coindesk.com/example</link><pubDate>Sat, 25 Jul 2026 20:00:00 GMT</pubDate></item>
</channel></rss>`;

describe('MarketContextProvider', () => {
  it.each([
    ['ordinary formatting', '  Bitcoin <b>news</b>\n', 'Bitcoin news'],
    ['nested tag fragments', '<scr<script>ipt>update</scr</script>ipt>', 'scriptupdate/script'],
    ['overlapping delimiters', '<<script>update</script>>', 'update'],
    ['nested comment opener', '<!<!-->update-->', '!update--'],
    ['unmatched delimiters', '< update > > <', ''],
    ['encoded text', '&lt;script&gt;update&lt;/script&gt;', '&lt;script&gt;update&lt;/script&gt;'],
  ])('keeps RSS %s as text without reconstructing markup', async (_label, title, expected) => {
    const feed = rss.replace('Bitcoin sees renewed interest', title);
    const provider = new MarketContextProvider(async (url) => new Response(
      url.includes('coingecko') ? '[]' : feed,
    ));

    const { news } = await provider.get();
    if (!expected) {
      expect(news).toEqual([]);
    } else {
      expect(news).toHaveLength(1);
      expect(news[0].title).toBe(expected);
      expect(news[0].title).not.toMatch(/[<>]/);
    }
  });

  it('handles a large malformed RSS title within the existing input and test limits', async () => {
    const feed = rss.replace('Bitcoin sees renewed interest', '<'.repeat(400_000) + 'bounded headline');
    const provider = new MarketContextProvider(async (url) => new Response(
      url.includes('coingecko') ? '[]' : feed,
    ));

    const { news } = await provider.get();
    expect(news).toHaveLength(1);
    expect(news[0].title).toBe('bounded headline');
  });

  it('retains HTTPS-only RSS links after text normalization', async () => {
    const feed = `<rss><channel>${['https://example.invalid/news', 'http://example.invalid/news', 'javascript:alert(1)'].map((url) =>
      `<item><title>Headline</title><link><![CDATA[${url}]]></link><pubDate>Sat, 25 Jul 2026 20:00:00 GMT</pubDate></item>`,
    ).join('')}</channel></rss>`;
    const provider = new MarketContextProvider(async (url) => new Response(
      url.includes('coingecko') ? '[]' : feed,
    ));

    expect((await provider.get()).news.map((item) => item.url)).toEqual(['https://example.invalid/news']);
  });

  it('normalizes a major-coin ticker and a safe RSS headline without exposing a trading signal', async () => {
    const provider = new MarketContextProvider(async (url) => new Response(
      url.includes('simple/price') ? JSON.stringify({ solana: { eur: 125.5 } }) : url.includes('coingecko') ? JSON.stringify(coinResponse) : rss,
      { status: 200 },
    ), () => new Date('2026-07-25T22:00:00.000Z'));

    await expect(provider.get()).resolves.toEqual({
      updatedAt: '2026-07-25T22:00:00.000Z',
      solEur: 125.5,
      ticker: [
        { symbol: 'BTC', name: 'Bitcoin', priceUsd: 64_000, change24hPercent: 1.25 },
        { symbol: 'SOL', name: 'Solana', priceUsd: 150, change24hPercent: -2.5 },
      ],
      news: [{ source: 'CoinDesk', title: 'Bitcoin sees renewed interest', url: 'https://www.coindesk.com/example', publishedAt: '2026-07-25T20:00:00.000Z' }],
    });
  });

  it('bounds the cached ticker to the eight requested CoinGecko ids and ignores duplicates or unknown coins', async () => {
    const markets = [
      ...coinResponse,
      { id: 'ethereum', symbol: 'eth', name: 'Ethereum', current_price: 3_000, price_change_percentage_24h: 2 },
      { id: 'binancecoin', symbol: 'bnb', name: 'BNB', current_price: 600, price_change_percentage_24h: 3 },
      { id: 'ripple', symbol: 'xrp', name: 'XRP', current_price: 1, price_change_percentage_24h: 4 },
      { id: 'dogecoin', symbol: 'doge', name: 'Dogecoin', current_price: 0.2, price_change_percentage_24h: 5 },
      { id: 'cardano', symbol: 'ada', name: 'Cardano', current_price: 0.5, price_change_percentage_24h: 6 },
      { id: 'avalanche-2', symbol: 'avax', name: 'Avalanche', current_price: 40, price_change_percentage_24h: 7 },
      { id: 'attacker-coin', symbol: 'evil', name: 'Unrequested', current_price: 1, price_change_percentage_24h: 99 },
      { id: 'bitcoin', symbol: 'evil-duplicate', name: 'Duplicate', current_price: 1, price_change_percentage_24h: 99 },
    ];
    const provider = new MarketContextProvider(async (url) => new Response(
      url.includes('simple/price') ? JSON.stringify({ solana: { eur: 125.5 } }) : url.includes('coingecko') ? JSON.stringify(markets) : rss,
      { status: 200 },
    ), () => new Date('2026-07-25T22:00:00.000Z'));

    const context = await provider.get();
    expect(context.ticker).toHaveLength(8);
    expect(context.ticker.map((coin) => coin.symbol)).toEqual(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX']);
    expect(context.ticker.some((coin) => coin.symbol.includes('EVIL'))).toBe(false);
  });

  it('requests the exact eight whitelisted CoinGecko ids, including ripple rather than the ticker symbol xrp', async () => {
    let requestedMarketUrl = '';
    const provider = new MarketContextProvider(async (url) => {
      if (url.includes('/coins/markets')) requestedMarketUrl = url;
      return new Response(
        url.includes('simple/price') ? JSON.stringify({ solana: { eur: 125.5 } }) : url.includes('coingecko') ? JSON.stringify(coinResponse) : rss,
        { status: 200 },
      );
    }, () => new Date('2026-07-25T22:00:00.000Z'));

    await provider.get();
    expect(new URL(requestedMarketUrl).searchParams.get('ids')?.split(',')).toEqual([
      'bitcoin', 'ethereum', 'solana', 'binancecoin', 'ripple', 'dogecoin', 'cardano', 'avalanche-2',
    ]);
  });

  it('rejects a chunked market-context response once the streamed body exceeds one MiB', async () => {
    const oversized = JSON.stringify([{ id: 'bitcoin', symbol: 'btc', name: 'X'.repeat(1_100_000), current_price: 1, price_change_percentage_24h: 1 }]);
    const provider = new MarketContextProvider(async (url) => new Response(
      url.includes('simple/price') ? JSON.stringify({ solana: { eur: 125.5 } }) : url.includes('coingecko') ? oversized : rss,
      { status: 200 },
    ), () => new Date('2026-07-25T22:00:00.000Z'));

    await expect(provider.get()).rejects.toThrow(/response body exceeds 1048576 bytes/i);
  });

  it('discards an expired cache when refresh fails instead of retaining stale market context', async () => {
    let now = new Date('2026-07-25T22:00:00.000Z');
    let failing = false;
    const provider = new MarketContextProvider(async (url) => {
      if (failing) throw new Error('upstream unavailable');
      return new Response(
        url.includes('simple/price') ? JSON.stringify({ solana: { eur: 125.5 } }) : url.includes('coingecko') ? JSON.stringify(coinResponse) : rss,
        { status: 200 },
      );
    }, () => now);

    await expect(provider.get()).resolves.toMatchObject({ updatedAt: now.toISOString() });
    now = new Date(now.getTime() + 5 * 60_000 + 1);
    failing = true;
    await expect(provider.get()).rejects.toThrow('upstream unavailable');
    expect((provider as unknown as { cached?: unknown }).cached).toBeUndefined();
  });

  it('aborts stalled CoinGecko and CoinDesk requests at the shared deadline', async () => {
    const hangingFetcher = (_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    });
    const provider = new MarketContextProvider(hangingFetcher, () => new Date(), 0, 10);
    await expect(provider.get()).rejects.toMatchObject({ name: 'AbortError' });
  });
});
