import { afterEach, describe, expect, it } from 'vitest';
import { createDashboardServer, type DashboardServer } from '../src/dashboard.js';

let dashboard: DashboardServer | undefined;
afterEach(async () => { await dashboard?.close(); dashboard = undefined; });

describe('paper dashboard', () => {
  it.each(['Error', 'unprintable thrown value'])('returns a generic 500 for an %s and serves subsequent requests', async (kind) => {
    const internalDetail = 'FICTIONAL_INTERNAL_DETAIL /private/example-only/config.json';
    const failure = kind === 'Error'
      ? new Error(internalDetail)
      : { toString() { throw new Error(internalDetail); } };
    const status = { mode: 'paper' as const, updatedAt: '2026-07-25T00:00:00.000Z', availableLamports: 0, openPositions: [], realizedPnlLamports: 0 };
    let failNext = true;
    dashboard = await createDashboardServer({
      port: 0,
      getStatus: () => {
        if (failNext) { failNext = false; throw failure; }
        return status;
      },
    });
    const base = `http://127.0.0.1:${dashboard.port}`;

    const response = await fetch(`${base}/api/status`);
    expect(response.status).toBe(500);
    expect(response.headers.get('content-type')).toBe('application/json; charset=utf-8');
    const body = await response.text();
    expect(JSON.parse(body)).toEqual({ ok: false, error: 'internal_error' });
    expect(body).not.toContain(internalDetail);
    expect(body).not.toContain('/private/');
    expect(body).not.toContain('stack');

    const health = await fetch(`${base}/healthz`);
    expect(health.status).toBe(200);
    await expect(health.text()).resolves.toBe('ok');
    const recovered = await fetch(`${base}/api/status`);
    expect(recovered.status).toBe(200);
    await expect(recovered.json()).resolves.toEqual(status);
  });

  it('escapes literal news markup in the HTML fallback', async () => {
    const title = '<img src=x onerror="FICTIONAL_MARKER">';
    dashboard = await createDashboardServer({
      port: 0,
      getStatus: () => ({
        mode: 'paper' as const, updatedAt: '2026-07-25T00:00:00.000Z', availableLamports: 0, openPositions: [], realizedPnlLamports: 0,
        marketContext: { updatedAt: '2026-07-25T00:00:00.000Z', ticker: [], news: [{ source: 'CoinDesk', title, url: 'https://example.invalid/news', publishedAt: '2026-07-25T00:00:00.000Z' }] },
      }),
    });

    const response = await fetch(`http://127.0.0.1:${dashboard.port}/`);
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img src=x onerror=');
    expect(html).toContain('FICTIONAL_MARKER');
  });

  it('serves a loopback-only paper status page and API without login', async () => {
    dashboard = await createDashboardServer({
      port: 0,
      getStatus: () => ({ mode: 'paper' as const, updatedAt: '2026-07-25T00:00:00.000Z', availableLamports: 9_750_000_000, openPositions: [], realizedPnlLamports: 0 }),
    });
    const base = `http://127.0.0.1:${dashboard.port}`;

    const response = await fetch(`${base}/`);
    expect(response.status).toBe(200);
    await expect(response.text()).resolves.toContain('PAPER ONLY');
    expect((await fetch(`${base}/api/status`)).status).toBe(200);
  });

  it('fails closed before opening a non-loopback listener', async () => {
    await expect(createDashboardServer({
      port: 0,
      bindHost: '0.0.0.0',
      getStatus: () => ({ mode: 'paper' as const, updatedAt: '2026-07-25T00:00:00.000Z', availableLamports: 0, openPositions: [], realizedPnlLamports: 0 }),
    })).rejects.toThrow('LEGACY_DASHBOARD_LOOPBACK_ONLY');
  });
});
