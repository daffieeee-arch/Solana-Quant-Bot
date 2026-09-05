import { access, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { describe, expect, it } from 'vitest';
import {
  B4AOfflineRangeRecorder,
  B4ARecorderError,
  MAX_RESPONSE_ENTITY_BYTES,
  OFFICIAL_B4A_HOST,
  normalisePlan,
  validatePlan,
  planHash,
  sha256Utf8,
} from '../scripts/b4a-offline-range-recorder.mjs';

function basePlan(overrides: Record<string, unknown> = {}) {
  const plan = {
    schemaVersion: 'B4A_OFFLINE_RANGE_RECORDER_PLAN_1',
    planId: 'b4a-offline-plan-01',
    issue: '#83',
    sourceHost: OFFICIAL_B4A_HOST,
    source: {
      epoch: 978,
      carUrl: `https://${OFFICIAL_B4A_HOST}/978/978.car`,
      carSizeBytes: 9,
      carSha256: 'aa'.repeat(32),
      indexUrl: `https://${OFFICIAL_B4A_HOST}/978/978.slots.txt`,
      indexSha256: 'bb'.repeat(32),
      indexEntries: 12,
    },
    slotRange: {
      startInclusive: 431_000_000,
      endExclusive: 431_000_020,
    },
    budget: {
      maxResponseEntityBytes: 4,
      maxRequests: 8,
      maxDiskBytes: 9,
      maxRuntimeMs: 60_000,
      responseTimeoutMs: 15_000,
      requestRetries: 0,
    },
    policy: {
      hostAllowlist: [OFFICIAL_B4A_HOST],
      networkEnabled: false,
      fallbackHostAllowed: false,
      alternateProtocolAllowed: false,
      proxyAllowed: false,
      s3Allowed: false,
    },
    provenance: {
      sourceFingerprint: 'source-epoch-978',
      indexFingerprint: 'index-978',
      codeFingerprint: 'code-b4a-offline',
      toolchainFingerprint: 'toolchain-rust-rs',
    },
    ...overrides,
  } as const;
  return normalisePlan(plan);
}

async function makeWorkingDirectory() {
  const root = await mkdtemp(join(tmpdir(), 'b4a-offline-recorder-'));
  const cleanup = async () => rm(root, { recursive: true, force: true });
  return { root, cleanup };
}

describe('B4A offline range recorder plan', () => {
  it('rejects an unexpected plan schema instead of normalising it away', () => {
    expect(() => validatePlan(basePlan({ schemaVersion: 'B4A_OFFLINE_RANGE_RECORDER_PLAN_0' })))
      .toThrow(/unexpected recorder schema version/);
  });

  it('accepts official host-only and offline policy constraints', () => {
    const plan = basePlan();
    expect(validatePlan(plan)).toMatchObject({
      sourceHost: OFFICIAL_B4A_HOST,
      policy: {
        networkEnabled: false,
        fallbackHostAllowed: false,
        alternateProtocolAllowed: false,
        proxyAllowed: false,
        s3Allowed: false,
      },
    });

    expect(() => validatePlan(basePlan({
      sourceHost: 'files.unauthorized.net',
    }))).toThrowError(/sourceHost must be official old-faithful host/);

    expect(() => validatePlan(basePlan({
      source: {
        ...basePlan().source,
        carUrl: 'https://example.org/978/978.car',
      },
    }))).toThrowError(/carUrl host must be files\.old-faithful\.net/);

    expect(() => validatePlan(basePlan({
      budget: {
        ...basePlan().budget,
        maxResponseEntityBytes: MAX_RESPONSE_ENTITY_BYTES + 1,
      },
    }))).toThrowError(/maxResponseEntityBytes exceeds B4A cap/);

    expect(() => validatePlan(basePlan({
      policy: {
        ...basePlan().policy,
        networkEnabled: true,
      },
    }))).toThrowError(/networkEnabled=false/);
  });

  it('derives contiguous byte segments from plan budget and file size', () => {
    const plan = basePlan();
    expect(planHash(plan)).toBe(planHash(plan));
    const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: '/tmp/unused' });

    expect(recorder.segments).toEqual([
      {
        segmentId: 'b4a-offline-plan-01-seg-0',
        start: 0,
        end: 3,
        expectedEntityBytes: 4,
        rangeHeader: 'bytes=0-3',
      },
      {
        segmentId: 'b4a-offline-plan-01-seg-1',
        start: 4,
        end: 7,
        expectedEntityBytes: 4,
        rangeHeader: 'bytes=4-7',
      },
      {
        segmentId: 'b4a-offline-plan-01-seg-2',
        start: 8,
        end: 8,
        expectedEntityBytes: 1,
        rangeHeader: 'bytes=8-8',
      },
    ]);
  });
});

describe('B4A offline range recorder', () => {
  it('writes all segments in order, validates receipts, and publishes complete summary', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const plan = basePlan();
      const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: root, runId: 'run-offline-test' });

      const segment0 = recorder.nextRequest();
      expect(segment0?.segmentId).toBe('b4a-offline-plan-01-seg-0');

      const status200or206 = await recorder.applyObservedSegment({
        segmentId: segment0!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 0-3/9' }),
        body: new TextEncoder().encode('zero'),
      });
      expect(status200or206.responseEntityBytes).toBe(4);

      const segment1 = recorder.nextRequest();
      await recorder.applyObservedSegment({
        segmentId: segment1!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 4-7/9' }),
        body: new TextEncoder().encode('one2'),
      });

      const segment2 = recorder.nextRequest();
      await recorder.applyObservedSegment({
        segmentId: segment2!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 8-8/9' }),
        body: new TextEncoder().encode('X'),
      });

      const summary = await recorder.publishSummary();
      expect(summary.totalSegments).toBe(3);
      expect(summary.completedSegments).toBe(3);
      expect(summary.observedResponseEntityBytes).toBe(9);
      expect(summary.status).toBe('COMPLETE');

      const raw = await readFile(join(root, 'b4a-offline-car.bin'));
      expect(raw.toString('latin1')).toBe('zeroone2X');

    } finally {
      await cleanup();
    }
  });

  it('rejects mismatched Content-Range and unexpected entity length', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const plan = basePlan();
      const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      const firstSegment = recorder.nextRequest();

      await expect(recorder.applyObservedSegment({
        segmentId: firstSegment!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 1-4/9' }),
        body: new TextEncoder().encode('abcd'),
      })).rejects.toThrow(/segment start mismatch/);

      await expect(recorder.applyObservedSegment({
        segmentId: firstSegment!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 0-3/9' }),
        body: new TextEncoder().encode('abc'),
      })).rejects.toThrow(/response bytes do not match plan/);
    } finally {
      await cleanup();
    }
  });

  it('supports restart through checkpoint hydration only when provenance and plan hash align', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const plan = basePlan();
      const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      const first = recorder.nextRequest();
      await recorder.applyObservedSegment({
        segmentId: first!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 0-3/9' }),
        body: new TextEncoder().encode('abcd'),
      });

      const checkpointPath = join(root, 'b4a-offline-checkpoint.json');
      await access(checkpointPath, constants.F_OK);

      const recovered = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      await recovered.hydrateFromCheckpoint();
      expect(recovered.completedCount).toBe(1);
      expect(recovered.nextRequest()?.segmentId).toBe('b4a-offline-plan-01-seg-1');

      const mutatedCheckpoint = JSON.parse(await readFile(checkpointPath, 'utf8')) as Record<string, unknown>;
      mutatedCheckpoint.planHash = 'bad-hash';
      await writeFile(checkpointPath, JSON.stringify(mutatedCheckpoint), 'utf8');

      const corrupted = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      await expect(corrupted.hydrateFromCheckpoint()).rejects.toThrow(/plan hash changed/);

      mutatedCheckpoint.planHash = planHash(plan);
      mutatedCheckpoint.provenance = { ...plan.provenance, codeFingerprint: null };
      await writeFile(checkpointPath, JSON.stringify(mutatedCheckpoint), 'utf8');
      const missingProvenance = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      await expect(missingProvenance.hydrateFromCheckpoint()).rejects.toThrow(/codeFingerprint changed during resume/);
    } finally {
      await cleanup();
    }
  });

  it('continues capture after resume using the checkpoint run identity', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const plan = basePlan();
      const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: root, runId: 'run-offline-resume' });
      const first = recorder.nextRequest();
      await recorder.applyObservedSegment({
        segmentId: first!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 0-3/9' }),
        body: new TextEncoder().encode('abcd'),
      });

      const recovered = new B4AOfflineRangeRecorder({
        plan,
        workingDir: root,
        runId: 'run-should-be-replaced',
      });
      await recovered.hydrateFromCheckpoint();
      expect(recovered.runId).toBe('run-offline-resume');

      const second = recovered.nextRequest();
      await recovered.applyObservedSegment({
        segmentId: second!.segmentId,
        status: 206,
        headers: new Headers({ 'Content-Range': 'bytes 4-7/9' }),
        body: new TextEncoder().encode('efgh'),
      });

      const resumed = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      await resumed.hydrateFromCheckpoint();
      const summary = await resumed.publishSummary();
      expect(resumed.runId).toBe('run-offline-resume');
      expect(summary.completedSegments).toBe(2);
      expect(resumed.nextRequest()?.segmentId).toBe('b4a-offline-plan-01-seg-2');
      expect(summary.receipts.every((receipt) => receipt.runId === 'run-offline-resume')).toBe(true);
    } finally {
      await cleanup();
    }
  });

  it('requires the request budget and verifies the complete raw hash explicitly', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const bytes = new TextEncoder().encode('abcd');
      const plan = basePlan({
        source: { ...basePlan().source, carSizeBytes: bytes.byteLength, carSha256: sha256Utf8(bytes) },
        budget: { ...basePlan().budget, maxRequests: 1 },
      });
      const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      const segment = recorder.nextRequest();
      await recorder.applyObservedSegment({
        segmentId: segment!.segmentId,
        status: 200,
        headers: new Headers(),
        body: bytes,
      });
      expect(recorder.nextRequest()).toBeNull();
      await expect(recorder.verifyCompleteRaw()).resolves.toEqual({ byteLength: 4, sha256: sha256Utf8(bytes) });
    } finally {
      await cleanup();
    }
  });

  it('rejects duplicate segment observations and supports single-segment 200 responses', async () => {
    const { root: rootA, cleanup: cleanupA } = await makeWorkingDirectory();
    try {
      const planA = basePlan({ source: { ...basePlan().source, carSizeBytes: 4 } });
      const recorderA = new B4AOfflineRangeRecorder({ plan: planA, workingDir: rootA });
      const first = recorderA.nextRequest();

      await recorderA.applyObservedSegment({
        segmentId: first!.segmentId,
        status: 200,
        headers: new Headers(),
        body: new TextEncoder().encode('unit'),
      });

      await expect(recorderA.applyObservedSegment({
        segmentId: first!.segmentId,
        status: 200,
        headers: new Headers(),
        body: new TextEncoder().encode('unit'),
      })).rejects.toThrow(B4ARecorderError);

      const multiSegmentPlan = basePlan({ source: { ...basePlan().source, carSizeBytes: 9 }, budget: { ...basePlan().budget, maxResponseEntityBytes: 4 } });
      const recorderB = new B4AOfflineRangeRecorder({ plan: multiSegmentPlan, workingDir: rootA });
      const firstB = recorderB.nextRequest();

      await expect(recorderB.applyObservedSegment({
        segmentId: firstB!.segmentId,
        status: 200,
        headers: new Headers(),
        body: new TextEncoder().encode('zero'),
      })).rejects.toThrow(/single segment plan only supports 200/);
    } finally {
      await cleanupA();
    }
  });

  it('rejects checkpoint files with duplicate receipts', async () => {
    const { root, cleanup } = await makeWorkingDirectory();
    try {
      const plan = basePlan();
      const checkpoint = {
        snapshotVersion: 'B4A_OFFLINE_RANGE_CHECKPOINT_1',
        planHash: planHash(plan),
        planId: plan.planId,
        runId: 'run-offline-test',
        completedSegments: ['b4a-offline-plan-01-seg-0', 'b4a-offline-plan-01-seg-0'],
        receipts: [
          {
            receiptVersion: 'B4A_OFFLINE_RANGE_RECEIPT_1',
            runId: 'run-offline-test',
            segmentId: 'b4a-offline-plan-01-seg-0',
            requestStart: 0,
            requestEnd: 3,
            responseStatus: 206,
            responseEntityBytes: 4,
            responseTotalBytes: 9,
            responseEntitySha256: 'c'.repeat(64),
            receivedAt: new Date().toISOString(),
          },
          {
            receiptVersion: 'B4A_OFFLINE_RANGE_RECEIPT_1',
            runId: 'run-offline-test',
            segmentId: 'b4a-offline-plan-01-seg-0',
            requestStart: 0,
            requestEnd: 3,
            responseStatus: 206,
            responseEntityBytes: 4,
            responseTotalBytes: 9,
            responseEntitySha256: 'c'.repeat(64),
            receivedAt: new Date().toISOString(),
          },
        ],
        plannedResponseEntityBytes: 9,
        observedResponseEntityBytes: 8,
        provenance: plan.provenance,
        status: 'IN_PROGRESS',
        createdAt: new Date().toISOString(),
      };
      await writeFile(join(root, 'b4a-offline-checkpoint.json'), JSON.stringify(checkpoint), 'utf8');

      const recorder = new B4AOfflineRangeRecorder({ plan, workingDir: root });
      await expect(recorder.hydrateFromCheckpoint()).rejects.toThrow(/duplicate segment receipts/);
    } finally {
      await cleanup();
    }
  });
});
