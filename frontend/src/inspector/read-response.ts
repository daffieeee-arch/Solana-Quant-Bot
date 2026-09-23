/** Shared bounded JSON transport; each view validates its own display contract. */
export async function readJsonResponse(response: Response, limit: number): Promise<unknown> {
  if (!response.ok || !response.body) throw new Error('Unavailable');
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try {
    for (;;) { const result = await reader.read(); if (result.done) break;
      size += result.value.byteLength; if (size > limit) throw new Error('Response limit'); chunks.push(result.value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}
