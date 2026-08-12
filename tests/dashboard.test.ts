import { afterEach, describe, expect, it } from 'vitest';
import { createDashboardServer, type DashboardServer } from '../src/dashboard.js';

let dashboard: DashboardServer | undefined;
afterEach(async () => { await dashboard?.close(); dashboard = undefined; });

describe('paper dashboard', () => {
  it('serves a LAN-only paper status page and API without login', async () => {
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
});
