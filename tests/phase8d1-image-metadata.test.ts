import { describe, expect, it } from 'vitest';
import { validateImageMetadata } from '../scripts/phase8d1/validate-image-metadata.mjs';

describe('Phase 8D1 image metadata credential boundary', () => {
  it('accepts ordinary runtime environment and history metadata', () => {
    expect(validateImageMetadata(['PATH=/usr/bin:/bin','NODE_ENV=production'], ['{"CreatedBy":"ENV NODE_ENV=production"}'])).toEqual([]);
  });
  it.each([
    [['HARMLESS=https://user:password@example.invalid'], []],
    [['API_TOKEN=supersecretvalue'], []],
    [[], ['{"CreatedBy":"ENV HARMLESS=https://user:password@example.invalid"}']],
    [[], ['{"CreatedBy":"RUN printf API_TOKEN=supersecretvalue"}']],
  ])('rejects credential-bearing Config.Env or history values', (env, history) => {
    expect(validateImageMetadata(env as string[], history as string[]).join('\n')).toMatch(/credential/i);
  });
});
