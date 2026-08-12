export type HttpFetcher = (url: string, init?: RequestInit) => Promise<Response>;
export const defaultHttpFetcher: HttpFetcher = (url, init) => globalThis.fetch(url, init);

const BODY_METHODS = new Set<PropertyKey>(['arrayBuffer', 'blob', 'formData', 'json', 'text']);
const DEFAULT_MAX_BODY_BYTES = 1024 * 1024;

async function readBoundedBody(response: Response, maxBodyBytes: number): Promise<Uint8Array<ArrayBuffer>> {
  const contentLength = response.headers.get('content-length');
  if (contentLength !== null) {
    const declaredBytes = Number(contentLength);
    if (Number.isFinite(declaredBytes) && declaredBytes > maxBodyBytes) {
      throw new Error(`HTTP response body exceeds ${maxBodyBytes} bytes`);
    }
  }
  if (!response.body) return new Uint8Array();

  const chunks: Uint8Array[] = [];
  const reader = response.body.getReader();
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value?.byteLength) continue;
      totalBytes += value.byteLength;
      if (totalBytes > maxBodyBytes) {
        await reader.cancel(`HTTP response body exceeds ${maxBodyBytes} bytes`).catch(() => undefined);
        throw new Error(`HTTP response body exceeds ${maxBodyBytes} bytes`);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const body: Uint8Array<ArrayBuffer> = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function consumeBoundedBody(response: Response, method: PropertyKey, maxBodyBytes: number): Promise<unknown> {
  const bytes = await readBoundedBody(response, maxBodyBytes);
  if (method === 'arrayBuffer') return bytes.buffer;
  if (method === 'blob') return new Blob([bytes.buffer], { type: response.headers.get('content-type') ?? '' });
  if (method === 'formData') return new Response(bytes.buffer, { headers: response.headers }).formData();
  const text = new TextDecoder().decode(bytes);
  return method === 'json' ? JSON.parse(text) as unknown : text;
}

export async function fetchWithTimeout(
  fetcher: HttpFetcher,
  url: string,
  init: RequestInit | undefined,
  timeoutMs: number,
  activeControllers?: Set<AbortController>,
  maxBodyBytes = DEFAULT_MAX_BODY_BYTES,
): Promise<Response> {
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0) throw new Error('HTTP timeout must be a positive integer');
  if (!Number.isSafeInteger(maxBodyBytes) || maxBodyBytes <= 0) {
    throw new Error('HTTP response body limit must be a positive safe integer');
  }
  const controller = new AbortController();
  activeControllers?.add(controller);
  let cleaned = false;
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    clearTimeout(timer);
    activeControllers?.delete(controller);
  };
  const timer = setTimeout(() => {
    controller.abort();
    cleanup();
  }, timeoutMs);
  if (typeof timer === 'object' && 'unref' in timer) timer.unref();
  try {
    const response = await fetcher(url, { ...init, signal: controller.signal });
    return new Proxy(response, {
      get(target, property) {
        const value = Reflect.get(target, property, target) as unknown;
        if (BODY_METHODS.has(property) && typeof value === 'function') {
          return async (...args: unknown[]) => {
            try {
              return await consumeBoundedBody(target, property, maxBodyBytes);
            } finally {
              cleanup();
            }
          };
        }
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
  } catch (error) {
    cleanup();
    throw error;
  }
}
