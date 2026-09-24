/** Shared bounded JSON transport; each view validates its own display contract. */
export async function readJsonResponse(response: Response, limit: number, expectedSha256?: string): Promise<unknown> {
  if (!response.ok || !response.body) throw new Error('Unavailable');
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try {
    for (;;) { const result = await reader.read(); if (result.done) break;
      size += result.value.byteLength; if (size > limit) throw new Error('Response limit'); chunks.push(result.value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  if (expectedSha256 !== undefined) {
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    const actual = Array.from(digest, b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== expectedSha256) throw new SnapshotMismatch();
  }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}

export class SnapshotMismatch extends Error { constructor() { super('SNAPSHOT_MISMATCH'); } }
/** Capture a page pin before starting an asynchronous read. No polling or fallback snapshot. */
export function registeredResponse(name: 'inspection' | 'pilot-quality' | 'mint-flow', limit: number): (r: Response) => Promise<unknown> {
  const pin = document.querySelector<HTMLMetaElement>(`meta[name="inspector-snapshot-${name}"]`)?.content;
  return async response => {
    if (!pin || !/^[0-9a-f]{64}$/.test(pin)) throw new Error('Unregistered response');
    return readJsonResponse(response, limit, pin);
  };
}
