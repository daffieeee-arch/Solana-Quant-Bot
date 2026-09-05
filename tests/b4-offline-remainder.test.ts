import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  B4OfflineRemainderError,
  CANDIDATE_JETSTREAMER_V0_7_0_GIT_SHA,
  COVERAGE,
  DENIED_OVERRIDE_KEYS,
  PROVISIONAL_EXAMPLE_SLOT_RANGE,
  SLOTS_PER_EPOCH,
  assertOverrideDeny,
  assertPersistedBeforeParse,
  classifySlotCoverage,
  closeSlotRange,
  epochSlotBounds,
  persistRawBeforeParse,
  renderProgress,
  runInjectedDemo,
  sha256Bytes,
} from '../scripts/b4-offline-remainder.mjs';

async function makeWorkingDirectory() {
  const root = await mkdtemp(join(tmpdir(), 'b4-offline-remainder-'));
  const cleanup = async () => rm(root, { recursive: true, force: true });
  return { root, cleanup };
}

describe('B4 offline remainder default-deny', () => {
  it('accepts an empty override environment', () => {
    expect(assertOverrideDeny({})).toEqual({
      ok: true,
      deniedKeys: [...DENIED_OVERRIDE_KEYS],
    });
    expect(assertOverrideDeny({
      JETSTREAMER_HTTP_BASE_URL: '',
      ARCHIVE_BASE: '   ',
      PATH: '/usr/bin',
    }).ok).toBe(true);
  });

  it.each([
    'JETSTREAMER_HTTP_BASE_URL',
    'ARCHIVE_BASE',
    'ARCHIVE_BACKEND',
    'JETSTREAMER_S3_BUCKET',
    'ARCHIVE_S3_ENDPOINT',
    'AWS_ENDPOINT_URL',
  ])('fails closed when %s is non-empty', (key) => {
    expect(() => assertOverrideDeny({ [key]: 'https://example.invalid' }))
      .toThrowError(/default-deny/);
    try {
      assertOverrideDeny({ [key]: 'https://example.invalid' });
    } catch (error) {
      expect(error).toBeInstanceOf(B4OfflineRemainderError);
      expect((error as B4OfflineRemainderError).code).toBe('OVERRIDE_DENIED');
      expect((error as B4OfflineRemainderError).metadata).toMatchObject({ denied: [key] });
    }
  });
});

describe('B4 offline remainder slot-range closure', () => {
  it('closes a half-open slot window and refuses a full-epoch CAR', () => {
    expect(closeSlotRange({ startSlot: 10, endSlot: 12 })).toMatchObject({
      startSlot: 10,
      endSlot: 12,
      length: 2,
      objectKind: 'SLOT_WINDOW',
      approvedPlan: false,
    });

    expect(() => closeSlotRange({
      startSlot: 0,
      endSlot: SLOTS_PER_EPOCH,
      objectKind: 'FULL_EPOCH_CAR',
      epoch: 0,
    })).toThrowError(/full-epoch CAR is not a V2 slot-range closure/);
  });

  it('refuses a requested window that equals the full epoch span', () => {
    const epoch978 = epochSlotBounds(978);
    expect(epoch978).toEqual({
      epoch: 978,
      firstSlot: 422_496_000,
      endExclusive: 422_928_000,
    });
    expect(() => closeSlotRange({
      startSlot: epoch978.firstSlot,
      endSlot: epoch978.endExclusive,
      epoch: 978,
    })).toThrowError(/full-epoch span/);
  });

  it('marks the documented provisional example without approving it', () => {
    const closed = closeSlotRange({
      ...PROVISIONAL_EXAMPLE_SLOT_RANGE,
      epoch: 978,
    });
    expect(closed.provisionalExample).toBe(true);
    expect(closed.approvedPlan).toBe(false);
  });
});

describe('B4 offline remainder coverage classes', () => {
  it('distinguishes UNAVAILABLE, GAP and QUARANTINED and never zero-fills', () => {
    const requested = { startSlot: 100, endSlot: 104 };

    expect(classifySlotCoverage({ requested, observedSlots: [] })).toMatchObject({
      status: COVERAGE.UNAVAILABLE,
      missingSlots: [100, 101, 102, 103],
      filledMissingWithZero: false,
    });

    expect(classifySlotCoverage({ requested, observedSlots: [100, 102] })).toMatchObject({
      status: COVERAGE.GAP,
      observedSlots: [100, 102],
      missingSlots: [101, 103],
      filledMissingWithZero: false,
    });

    expect(classifySlotCoverage({
      requested,
      observedSlots: [100],
      quarantinedSlots: [101],
    })).toMatchObject({
      status: COVERAGE.QUARANTINED,
      observedSlots: [100],
      quarantinedSlots: [101],
      missingSlots: [102, 103],
      filledMissingWithZero: false,
    });

    expect(classifySlotCoverage({
      requested,
      observedSlots: [100, 101, 102, 103],
    }).status).toBe(COVERAGE.CLOSED);
  });
});

describe('B4 offline remainder persist-before-parse', () => {
  it('writes raw bytes, identity and receipt before any parse', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const rawBytes = new TextEncoder().encode('remainder-raw-fixture');
      const persisted = await persistRawBeforeParse({
        workingDir: root,
        requested: { startSlot: 10, endSlot: 12 },
        rawBytes,
        identity: { cid: 'fixture-cid-1' },
        acquiredAt: '2026-09-05T00:00:00.000Z',
        hostClass: 'FIXTURE',
        env: {},
      });

      expect(persisted.parseAttempted).toBe(false);
      expect(persisted.receipt).toMatchObject({
        hostClass: 'FIXTURE',
        requestedRange: { startSlot: 10, endSlot: 12 },
        byteCount: rawBytes.byteLength,
        sha256: sha256Bytes(rawBytes),
        cid: 'fixture-cid-1',
        acquired_at: '2026-09-05T00:00:00.000Z',
        acquiredAtRole: 'operational_provenance_only',
        networkEnabled: false,
        parseAttempted: false,
        jetstreamerCandidateSha: CANDIDATE_JETSTREAMER_V0_7_0_GIT_SHA,
        approved: false,
      });

      const raw = await readFile(join(root, 'b4-offline-raw.bin'));
      expect(Buffer.from(raw).equals(Buffer.from(rawBytes))).toBe(true);
      const identity = JSON.parse(await readFile(join(root, 'b4-offline-identity.json'), 'utf8'));
      expect(identity.sha256).toBe(sha256Bytes(rawBytes));

      const gate = await assertPersistedBeforeParse(root);
      expect(gate.decodeInScope).toBe(false);
      expect(gate.receipt.parseAttempted).toBe(false);
    } finally {
      await cleanup();
    }
  });

  it('refuses parse when persist artifacts are missing', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      await expect(assertPersistedBeforeParse(root)).rejects.toMatchObject({
        code: 'PARSE_BEFORE_PERSIST',
      });
    } finally {
      await cleanup();
    }
  });
});

describe('B4 offline remainder injected progress', () => {
  it('renders deterministic progress only through an injected writer', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const chunks: string[] = [];
      const demo = await runInjectedDemo({
        workingDir: root,
        env: {},
        write: (chunk: string) => {
          chunks.push(chunk);
        },
        observedSlots: [10],
      });

      expect(demo.coverage.status).toBe(COVERAGE.GAP);
      expect(demo.text).toContain('B4-offline remainder');
      expect(demo.text).toContain('networkEnabled=false');
      expect(demo.text).toContain('overrides=denied');
      expect(demo.text).toContain('slotRange=[10,12)');
      expect(demo.text).toContain('coverage=GAP');
      expect(demo.text).toContain('receipt=written');
      expect(demo.text).toContain('parse=not-attempted-before-persist');
      expect(demo.text).toContain('approved=false');
      expect(demo.text).toContain('lease=unauthorized');
      expect(chunks.join('')).toBe(demo.text);

      expect(() => renderProgress({
        networkEnabled: false,
        overridesDenied: true,
        startSlot: 10,
        endSlot: 12,
        coverage: 'GAP',
        rawSha256: 'abc',
        receiptWritten: true,
        parseAttempted: false,
        approved: false,
      })).toThrowError(/progress writer is required/);
    } finally {
      await cleanup();
    }
  });

  it('does not run the demo when an override is present', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      await expect(runInjectedDemo({
        workingDir: root,
        env: { ARCHIVE_BACKEND: 's3' },
        write: () => {},
      })).rejects.toMatchObject({ code: 'OVERRIDE_DENIED' });
    } finally {
      await cleanup();
    }
  });
});

describe('B4 offline remainder draft plan', () => {
  it('keeps the committed draft plan unapproved and offline', () => {
    const plan = JSON.parse(readFileSync('docs/research/B4_OFFLINE_REMAINDER_DRAFT_PLAN.json', 'utf8'));
    expect(plan.approved).toBe(false);
    expect(plan.networkEnabled).toBe(false);
    expect(plan.host).toBeNull();
    expect(plan.slot_range).toBeNull();
    expect(plan.purpose).toBe('ENGINEERING_VALIDATION_ONLY');
  });
});
