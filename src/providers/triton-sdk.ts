// Real Triton One Vixen client factory backed by @triton-one/vixen-stream.
//
// The production process injects this factory into TritonProvider so the
// provider stays pure (parsers + bounded queues) and the SDK stays a seam.
import { ProgramStreamsServiceClient, ProgramAddress, credentials, createCallCredentials } from '@triton-one/vixen-stream';
import type { TritonClientFactory, TritonClientLike, TritonStreamLike, VixenUpdate } from './triton.js';

// ProgramAddress enum values are the canonical program ID strings.
const PROGRAM_ADDRESS: Record<string, ProgramAddress> = {
  '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P': ProgramAddress.Pumpfun,
  '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': ProgramAddress.RaydiumAmmv4,
  'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C': ProgramAddress.RaydiumCpmm,
} as const;

/**
 * Build a TritonClientFactory that connects to the real Vixen gRPC endpoint.
 * `endpoint` is e.g. "johnb-mainnet-2781.mainnet.rpcpool.com" (no scheme; the
 * SDK uses TLS). `token` is the Triton x-token (works for RPC, gRPC, Vixen).
 */
export function createVixenClientFactory(hostOverride?: string, tokenOverride?: string): TritonClientFactory {
  return (endpoint: string, token: string) => {
    const host = hostOverride ?? endpoint;
    const apiToken = tokenOverride ?? token;

    const creds = credentials.combineChannelCredentials(
      credentials.createSsl(),
      createCallCredentials(apiToken),
    );

    // The SDK exports a ServiceClientConstructor; instantiate like the docs:
    // new ProgramStreamsServiceClient(endpoint, combinedCredentials, options)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rawClient: any = new (ProgramStreamsServiceClient as unknown as new (...args: unknown[]) => unknown)(
      host,
      creds,
      { 'grpc.keepalive_time_ms': 5_000, 'grpc.keepalive_timeout_ms': 10_000 },
    );

    const client: TritonClientLike = {
      Subscribe(request: { program: string }) {
        const programAddress = PROGRAM_ADDRESS[request.program];
        if (programAddress === undefined) {
          throw new Error(`Unsupported Triton Vixen program: ${request.program}`);
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const stream = rawClient.Subscribe({ program: programAddress });
        const adapter: TritonStreamLike = {
          // The SDK stream emits decoded ProgramUpdateType objects whose runtime
          // shape matches our captured VixenUpdate payloads.
          on(event: 'data' | 'error', listener: (arg: VixenUpdate | Error) => void) {
            // Use a permissive listener type at the SDK boundary; runtime shape
            // matches, and TritonProvider re-validates every field before use.
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            stream.on(event, listener as any);
            return adapter;
          },
          cancel() {
            if (stream && typeof stream.cancel === 'function') stream.cancel();
          },
        };
        return adapter;
      },
    };
    return client;
  };
}
