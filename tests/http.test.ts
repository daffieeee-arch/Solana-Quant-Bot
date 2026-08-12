import { describe, expect, it } from 'vitest';
import { fetchWithTimeout } from '../src/providers/http.js';

describe('bounded HTTP response ingestion', () => {
  it('applies a one MiB body cap even when the caller omits an explicit limit', async () => {
    const response = await fetchWithTimeout(
      async () => new Response(JSON.stringify({ payload: 'x'.repeat(1024 * 1024) })),
      'https://bounded.example', undefined, 10_000,
    );
    await expect(response.json()).rejects.toThrow(/exceeds 1048576 bytes/i);
  });

  it('continues to parse a bounded JSON response', async () => {
    const response = await fetchWithTimeout(
      async () => new Response(JSON.stringify({ ok: true })),
      'https://bounded.example', undefined, 10_000,
    );
    await expect(response.json()).resolves.toEqual({ ok: true });
  });
});