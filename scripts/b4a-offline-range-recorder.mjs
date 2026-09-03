import { createHash } from 'node:crypto';
import { access, constants, mkdir, open, readFile, rename, stat, writeFile } from 'node:fs/promises';

export const B4A_SCHEMA_VERSION = 'B4A_OFFLINE_RANGE_RECORDER_PLAN_1';
export const B4A_CHECKPOINT_VERSION = 'B4A_OFFLINE_RANGE_CHECKPOINT_1';
export const B4A_RECEIPT_VERSION = 'B4A_OFFLINE_RANGE_RECEIPT_1';
export const OFFICIAL_B4A_HOST = 'files.old-faithful.net';
export const MAX_RESPONSE_ENTITY_BYTES = 16 * 1024 * 1024;

export class B4ARecorderError extends Error {
  constructor(message, code, metadata = {}) {
    super(message);
    this.name = 'B4ARecorderError';
    this.code = code;
    this.metadata = metadata;
  }
}

function assert(condition, code, message, metadata = {}) {
  if (!condition) {
    throw new B4ARecorderError(message, code, metadata);
  }
}

function canonicalJson(value) {
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => canonicalJson(entry)).join(',')}]`;
  }
  if (typeof value !== 'object') {
    throw new B4ARecorderError('unsupported canonical payload', 'CANONICALIZATION_ERROR');
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

export function sha256Utf8(value) {
  return createHash('sha256').update(value).digest('hex');
}

function parseRangeHeader(raw) {
  if (typeof raw !== 'string') return null;
  const match = /^bytes\s+(\d+)-(\d+)\/(\d+|\*)$/i.exec(raw);
  if (!match) return null;
  return {
    start: Number.parseInt(match[1], 10),
    end: Number.parseInt(match[2], 10),
    total: match[3] === '*' ? null : Number.parseInt(match[3], 10),
  };
}

function normalizeUrl(rawUrl, fieldName) {
  try {
    const parsed = new URL(rawUrl);
    assert(parsed.protocol === 'https:', 'PLAN_URL_BAD_SCHEME', `invalid scheme for ${fieldName}`);
    return parsed;
  } catch (error) {
    throw new B4ARecorderError(`invalid URL for ${fieldName}`, 'PLAN_URL_INVALID', { rawUrl, error: String(error) });
  }
}

function ensureHex64(value, code, message) {
  assert(typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value), code, message);
}

export function normalisePlan(raw) {
  return {
    schemaVersion: raw?.schemaVersion,
    planId: raw?.planId,
    issue: raw?.issue ?? '#83',
    sourceHost: raw?.sourceHost ?? OFFICIAL_B4A_HOST,
    source: {
      epoch: raw?.source?.epoch,
      carUrl: raw?.source?.carUrl,
      carSizeBytes: raw?.source?.carSizeBytes,
      carSha256: raw?.source?.carSha256,
      indexUrl: raw?.source?.indexUrl,
      indexSha256: raw?.source?.indexSha256,
      indexEntries: raw?.source?.indexEntries,
    },
    slotRange: {
      startInclusive: raw?.slotRange?.startInclusive,
      endExclusive: raw?.slotRange?.endExclusive,
    },
    budget: {
      maxResponseEntityBytes: raw?.budget?.maxResponseEntityBytes ?? 8 * 1024 * 1024,
      maxRequests: raw?.budget?.maxRequests ?? 8,
      maxDiskBytes: raw?.budget?.maxDiskBytes ?? raw?.source?.carSizeBytes,
      maxRuntimeMs: raw?.budget?.maxRuntimeMs ?? 60 * 60 * 1000,
      responseTimeoutMs: raw?.budget?.responseTimeoutMs ?? 15_000,
      requestRetries: raw?.budget?.requestRetries ?? 0,
    },
    policy: {
      hostAllowlist: raw?.policy?.hostAllowlist ?? [OFFICIAL_B4A_HOST],
      networkEnabled: raw?.policy?.networkEnabled ?? false,
      fallbackHostAllowed: raw?.policy?.fallbackHostAllowed ?? false,
      alternateProtocolAllowed: raw?.policy?.alternateProtocolAllowed ?? false,
      proxyAllowed: raw?.policy?.proxyAllowed ?? false,
      s3Allowed: raw?.policy?.s3Allowed ?? false,
    },
    provenance: {
      sourceFingerprint: raw?.provenance?.sourceFingerprint ?? null,
      indexFingerprint: raw?.provenance?.indexFingerprint ?? null,
      codeFingerprint: raw?.provenance?.codeFingerprint ?? null,
      toolchainFingerprint: raw?.provenance?.toolchainFingerprint ?? null,
    },
  };
}

export function validatePlan(rawPlan) {
  const plan = normalisePlan(rawPlan);

  assert(plan.schemaVersion === B4A_SCHEMA_VERSION, 'PLAN_SCHEMA_MISMATCH', 'unexpected recorder schema version');
  assert(typeof plan.planId === 'string' && plan.planId.length > 0, 'PLAN_ID_MISSING', 'planId required');

  assert(typeof plan.issue === 'string' && /^#\d+$/.test(plan.issue), 'PLAN_ISSUE_INVALID', 'issue must be #N');

  const carUrl = normalizeUrl(plan.source.carUrl, 'carUrl');
  const indexUrl = normalizeUrl(plan.source.indexUrl, 'indexUrl');
  assert(plan.sourceHost === OFFICIAL_B4A_HOST, 'PLAN_HOST_FORBIDDEN', 'sourceHost must be official old-faithful host');
  assert(carUrl.hostname === OFFICIAL_B4A_HOST, 'PLAN_HOST_FORBIDDEN', 'carUrl host must be files.old-faithful.net');
  assert(indexUrl.hostname === OFFICIAL_B4A_HOST, 'PLAN_HOST_FORBIDDEN', 'indexUrl host must be files.old-faithful.net');

  assert(Array.isArray(plan.policy.hostAllowlist), 'PLAN_HOST_ALLOWLIST_INVALID', 'hostAllowlist must be an array');
  assert(plan.policy.hostAllowlist.length === 1 && plan.policy.hostAllowlist[0] === OFFICIAL_B4A_HOST, 'PLAN_HOST_ALLOWLIST_INVALID', 'hostAllowlist must be exactly files.old-faithful.net');
  assert(plan.policy.networkEnabled === false, 'PLAN_NETWORK_POLICY', 'B4A offline contract keeps networkEnabled=false');
  assert(plan.policy.fallbackHostAllowed === false, 'PLAN_NETWORK_POLICY', 'fallbackHostAllowed must be false');
  assert(plan.policy.alternateProtocolAllowed === false, 'PLAN_NETWORK_POLICY', 'alternateProtocolAllowed must be false');
  assert(plan.policy.proxyAllowed === false, 'PLAN_NETWORK_POLICY', 'proxyAllowed must be false');
  assert(plan.policy.s3Allowed === false, 'PLAN_NETWORK_POLICY', 's3Allowed must be false');

  assert(Number.isSafeInteger(plan.source.epoch) && plan.source.epoch > 0, 'PLAN_EPOCH_INVALID', 'epoch must be positive integer');
  assert(Number.isSafeInteger(plan.source.carSizeBytes) && plan.source.carSizeBytes > 0, 'PLAN_SIZE_INVALID', 'carSizeBytes must be positive integer');
  ensureHex64(plan.source.carSha256, 'PLAN_CAR_HASH_INVALID', 'carSha256 must be sha256 hex');
  ensureHex64(plan.source.indexSha256, 'PLAN_INDEX_HASH_INVALID', 'indexSha256 must be sha256 hex');
  assert(Number.isSafeInteger(plan.source.indexEntries) && plan.source.indexEntries > 0, 'PLAN_INDEX_ENTRIES_INVALID', 'indexEntries must be positive integer');

  assert(Number.isSafeInteger(plan.slotRange.startInclusive), 'PLAN_SLOT_RANGE_INVALID', 'slotRange.startInclusive must be integer');
  assert(Number.isSafeInteger(plan.slotRange.endExclusive), 'PLAN_SLOT_RANGE_INVALID', 'slotRange.endExclusive must be integer');
  assert(plan.slotRange.startInclusive < plan.slotRange.endExclusive, 'PLAN_SLOT_RANGE_INVALID', 'slot range must be non-empty');

  assert(Number.isSafeInteger(plan.budget.maxResponseEntityBytes) && plan.budget.maxResponseEntityBytes > 0, 'PLAN_BUDGET_INVALID', 'maxResponseEntityBytes must be positive');
  assert(plan.budget.maxResponseEntityBytes <= MAX_RESPONSE_ENTITY_BYTES, 'PLAN_BUDGET_INVALID', 'maxResponseEntityBytes exceeds B4A cap');
  assert(Number.isSafeInteger(plan.budget.maxRequests) && plan.budget.maxRequests > 0, 'PLAN_BUDGET_INVALID', 'maxRequests must be positive');
  assert(Number.isSafeInteger(plan.budget.maxDiskBytes) && plan.budget.maxDiskBytes > 0, 'PLAN_BUDGET_INVALID', 'maxDiskBytes must be positive');
  assert(plan.budget.maxDiskBytes >= plan.source.carSizeBytes, 'PLAN_BUDGET_INVALID', 'maxDiskBytes must cover the planned CAR');
  assert(Number.isSafeInteger(plan.budget.maxRuntimeMs) && plan.budget.maxRuntimeMs > 0, 'PLAN_BUDGET_INVALID', 'maxRuntimeMs must be positive');
  assert(Number.isSafeInteger(plan.budget.responseTimeoutMs) && plan.budget.responseTimeoutMs > 0, 'PLAN_BUDGET_INVALID', 'responseTimeoutMs must be positive');
  assert(Number.isSafeInteger(plan.budget.requestRetries) && plan.budget.requestRetries >= 0, 'PLAN_BUDGET_INVALID', 'requestRetries must be 0 or greater');

  return plan;
}

export function planHash(plan) {
  return sha256Utf8(canonicalJson(normalisePlan(plan)));
}

export function deriveSegments(plan) {
  const validated = validatePlan(plan);
  const segments = [];
  let offset = 0;
  const chunkSize = Math.min(validated.budget.maxResponseEntityBytes, MAX_RESPONSE_ENTITY_BYTES);

  while (offset < validated.source.carSizeBytes) {
    const end = Math.min(offset + chunkSize - 1, validated.source.carSizeBytes - 1);
    segments.push({
      segmentId: `${validated.planId}-seg-${segments.length}`,
      start: offset,
      end,
      expectedEntityBytes: end - offset + 1,
      rangeHeader: `bytes=${offset}-${end}`,
    });
    offset = end + 1;
  }

  return {
    plan: validated,
    segments,
    plannedResponseEntityBytes: segments.reduce((acc, segment) => acc + segment.expectedEntityBytes, 0),
    plannedSegments: segments.length,
    budgetedAt: new Date().toISOString(),
  };
}

async function pathExists(path) {
  try {
    await access(path, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

export async function readCheckpoint(checkpointPath) {
  const data = await readFile(checkpointPath, 'utf8');
  const parsed = JSON.parse(data);
  assert(parsed && parsed.snapshotVersion === B4A_CHECKPOINT_VERSION, 'CHECKPOINT_VERSION_MISMATCH', 'checkpoint schema mismatch');
  return parsed;
}

export async function validateResumeState(
  expectedPlan,
  options = {},
) {
  const planned = validatePlan(expectedPlan);
  const plannedHash = planHash(planned);
  const {
    planHash: observedPlanHash,
    provenance = {},
    receipts = [],
  } = options;

  assert(plannedHash === observedPlanHash, 'RESUME_PLAN_MISMATCH', 'plan hash changed');

  const compare = (name, expected, observed) => {
    if (expected == null || observed == null) return;
    assert(expected === observed, `RESUME_${name.toUpperCase()}_MISMATCH`, `${name} changed during resume`, { expected, observed });
  };

  compare('sourceFingerprint', planned.provenance.sourceFingerprint, provenance.sourceFingerprint);
  compare('indexFingerprint', planned.provenance.indexFingerprint, provenance.indexFingerprint);
  compare('codeFingerprint', planned.provenance.codeFingerprint, provenance.codeFingerprint);
  compare('toolchainFingerprint', planned.provenance.toolchainFingerprint, provenance.toolchainFingerprint);
  assert(Array.isArray(receipts), 'RESUME_RECEIPTS_INVALID', 'receipts must be array');

  return {
    ok: true,
    plannedHash,
    planId: planned.planId,
    receiptCount: receipts.length,
  };
}

function buildSegmentReceipt({ runId, segment, status, responseEntityBytes, body, responseTotalBytes }) {
  return {
    receiptVersion: B4A_RECEIPT_VERSION,
    runId,
    segmentId: segment.segmentId,
    requestStart: segment.start,
    requestEnd: segment.end,
    responseStatus: status,
    responseEntityBytes,
    responseTotalBytes,
    responseEntitySha256: sha256Utf8(body),
    receivedAt: new Date().toISOString(),
  };
}

export class B4AOfflineRangeRecorder {
  constructor({ plan, workingDir, runId = `run-${Date.now().toString(36)}` }) {
    this.plan = validatePlan(plan);
    this.planHash = planHash(this.plan);
    this.workingDir = workingDir;
    this.runId = runId;

    const derived = deriveSegments(this.plan);
    this.segments = derived.segments;
    this._completed = new Set();
    this._receipts = [];
    this._attemptCount = 0;
    this._startedAt = Date.now();
    this.plannedResponseEntityBytes = derived.plannedResponseEntityBytes;

    this.rawPath = `${workingDir}/b4a-offline-car.bin`;
    this.checkpointPath = `${workingDir}/b4a-offline-checkpoint.json`;
    this.receiptsPath = `${workingDir}/b4a-offline-receipts.ndjson`;

    this._nextIndex = 0;
  }

  get completedCount() {
    return this._completed.size;
  }

  get observedResponseEntityBytes() {
    return this._receipts.reduce((acc, receipt) => acc + receipt.responseEntityBytes, 0);
  }

  get isComplete() {
    return this.completedCount === this.segments.length;
  }

  async hydrateFromCheckpoint() {
    if (!(await pathExists(this.checkpointPath))) return;
    const checkpoint = await readCheckpoint(this.checkpointPath);
    const expectedPlan = this.plan;
    await validateResumeState(expectedPlan, {
      planHash: checkpoint.planHash,
      provenance: checkpoint.provenance ?? {},
      receipts: checkpoint.receipts,
    });

    this._receipts = checkpoint.receipts ?? [];
    this._attemptCount = checkpoint.requestsAttempted ?? this._receipts.length;
    const segmentIds = new Set(this._receipts.map((entry) => entry.segmentId));
    assert(segmentIds.size === this._receipts.length, 'CHECKPOINT_DUPLICATE_RECEIPTS', 'checkpoint contains duplicate segment receipts');
    for (const receipt of this._receipts) {
      this._completed.add(receipt.segmentId);
    }

    assert(
      [...this._completed].every((segmentId) => this.segments.some((segment) => segment.segmentId === segmentId)),
      'CHECKPOINT_RECEIPT_UNKNOWN_SEGMENT',
      'checkpoint contains unknown segment receipt',
    );

    await this._verifyPersistedReceipts();

    this._nextIndex = 0;
    while (this._nextIndex < this.segments.length && this._completed.has(this.segments[this._nextIndex].segmentId)) {
      this._nextIndex += 1;
    }
  }

  nextRequest() {
    while (this._nextIndex < this.segments.length) {
      const segment = this.segments[this._nextIndex];
      if (!this._completed.has(segment.segmentId)) {
        return segment;
      }
      this._nextIndex += 1;
    }
    return null;
  }

  async applyObservedSegment({ segmentId, status, headers, body }) {
    const segment = this.segments.find((candidate) => candidate.segmentId === segmentId);
    assert(segment, 'SEGMENT_UNKNOWN', 'segment is not in current plan', { segmentId });
    assert(!this._completed.has(segmentId), 'SEGMENT_ALREADY_CAPTURED', 'segment already recorded', { segmentId });
    assert(this._attemptCount < this.plan.budget.maxRequests, 'REQUEST_BUDGET_EXCEEDED', 'request budget exhausted', {
      maxRequests: this.plan.budget.maxRequests,
      attempts: this._attemptCount,
    });
    this._attemptCount += 1;
    assert(Date.now() - this._startedAt <= this.plan.budget.maxRuntimeMs, 'RUNTIME_BUDGET_EXCEEDED', 'runtime budget exhausted', {
      maxRuntimeMs: this.plan.budget.maxRuntimeMs,
    });
    assert(status === 206 || status === 200, 'HTTP_STATUS_INVALID', 'only status 206/200 are accepted', { segmentId, status });

    const payload = body instanceof Uint8Array ? body : new Uint8Array(body);
    const responseEntityBytes = payload.byteLength;
    const contentRangeHeader = headers?.get?.('content-range') ?? headers?.get?.('Content-Range');

    if (status === 206) {
      const contentRange = parseRangeHeader(contentRangeHeader);
      assert(contentRange, 'CONTENT_RANGE_MISSING', '206 requires Content-Range', { segmentId });
      assert(contentRange.start === segment.start, 'CONTENT_RANGE_MISMATCH', 'segment start mismatch', { segmentId, expected: segment.start, observed: contentRange.start });
      assert(contentRange.end === segment.end, 'CONTENT_RANGE_MISMATCH', 'segment end mismatch', { segmentId, expected: segment.end, observed: contentRange.end });
      assert(contentRange.total === this.plan.source.carSizeBytes, 'CONTENT_RANGE_MISMATCH', 'content-range total mismatch', {
        segmentId,
        expected: this.plan.source.carSizeBytes,
        observed: contentRange.total,
      });
    } else {
      assert(this.segments.length === 1, 'HTTP_STATUS_INVALID', 'single segment plan only supports 200', { segments: this.segments.length });
    }

    assert(responseEntityBytes === segment.expectedEntityBytes, 'ENTITY_BYTES_MISMATCH', 'response bytes do not match plan', {
      segmentId,
      expected: segment.expectedEntityBytes,
      observed: responseEntityBytes,
    });

    await mkdir(this.workingDir, { recursive: true });
    let file;
    try {
      file = await open(this.rawPath, 'r+');
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      file = await open(this.rawPath, 'w+');
    }
    try {
      await file.write(payload, 0, responseEntityBytes, segment.start);
    } finally {
      await file.close();
    }

    const receipt = buildSegmentReceipt({
      runId: this.runId,
      segment,
      status,
      responseEntityBytes,
      body: payload,
      responseTotalBytes: this.plan.source.carSizeBytes,
    });

    this._receipts.push(receipt);
    this._completed.add(segmentId);
    await writeFile(this.receiptsPath, `${JSON.stringify(receipt)}\n`, { flag: 'a' });
    await this._persistCheckpoint();

    const finalSize = await this._rawSize();
    assert(finalSize <= this.plan.budget.maxDiskBytes, 'RAW_SIZE_VIOLATION', 'raw size exceeded disk budget', {
      expectedMax: this.plan.budget.maxDiskBytes,
      observed: finalSize,
    });

    return receipt;
  }

  async _persistCheckpoint() {
    const checkpoint = {
      snapshotVersion: B4A_CHECKPOINT_VERSION,
      planHash: this.planHash,
      planId: this.plan.planId,
      runId: this.runId,
      completedSegments: [...this._completed],
      receipts: this._receipts,
      requestsAttempted: this._attemptCount,
      plannedResponseEntityBytes: this.plannedResponseEntityBytes,
      observedResponseEntityBytes: this.observedResponseEntityBytes,
      provenance: this.plan.provenance,
      status: this.isComplete ? 'COMPLETE' : 'IN_PROGRESS',
      createdAt: new Date().toISOString(),
    };

    const temporaryPath = `${this.checkpointPath}.tmp`;
    await writeFile(temporaryPath, JSON.stringify(checkpoint, null, 2));
    await rename(temporaryPath, this.checkpointPath);
  }

  async _rawSize() {
    if (!(await pathExists(this.rawPath))) return 0;
    const fileStat = await stat(this.rawPath);
    return fileStat.size;
  }

  async _verifyPersistedReceipts() {
    if (this._receipts.length === 0) return;
    assert(await pathExists(this.rawPath), 'RESUME_RAW_MISSING', 'checkpoint receipts require captured raw bytes');
    const raw = await readFile(this.rawPath);
    assert(raw.byteLength <= this.plan.budget.maxDiskBytes, 'RESUME_RAW_SIZE_VIOLATION', 'captured raw bytes exceed disk budget');
    for (const receipt of this._receipts) {
      const segment = this.segments.find((candidate) => candidate.segmentId === receipt.segmentId);
      assert(segment, 'CHECKPOINT_RECEIPT_UNKNOWN_SEGMENT', 'checkpoint contains unknown segment receipt', { segmentId: receipt.segmentId });
      assert(receipt.requestStart === segment.start && receipt.requestEnd === segment.end, 'CHECKPOINT_RECEIPT_RANGE_MISMATCH', 'checkpoint receipt range changed', { segmentId: receipt.segmentId });
      const payload = raw.subarray(segment.start, segment.end + 1);
      assert(payload.byteLength === segment.expectedEntityBytes, 'RESUME_RAW_INCOMPLETE', 'captured raw bytes are incomplete', { segmentId: receipt.segmentId });
      assert(sha256Utf8(payload) === receipt.responseEntitySha256, 'RESUME_RAW_HASH_MISMATCH', 'captured raw bytes changed', { segmentId: receipt.segmentId });
    }
  }

  async verifyCompleteRaw() {
    assert(this.isComplete, 'RAW_INCOMPLETE', 'cannot verify a partial capture as complete');
    const raw = await readFile(this.rawPath);
    assert(raw.byteLength === this.plan.source.carSizeBytes, 'RAW_SIZE_MISMATCH', 'complete raw capture has unexpected size', {
      expected: this.plan.source.carSizeBytes,
      observed: raw.byteLength,
    });
    const observedHash = sha256Utf8(raw);
    assert(observedHash === this.plan.source.carSha256, 'RAW_HASH_MISMATCH', 'complete raw capture hash differs from plan', {
      expected: this.plan.source.carSha256,
      observed: observedHash,
    });
    return { byteLength: raw.byteLength, sha256: observedHash };
  }

  async publishSummary() {
    return {
      summaryVersion: B4A_CHECKPOINT_VERSION,
      planHash: this.planHash,
      planId: this.plan.planId,
      runId: this.runId,
      totalSegments: this.segments.length,
      completedSegments: this.completedCount,
      plannedResponseEntityBytes: this.plannedResponseEntityBytes,
      observedResponseEntityBytes: this.observedResponseEntityBytes,
      requestsAttempted: this._attemptCount,
      status: this.isComplete ? 'COMPLETE' : 'IN_PROGRESS',
      receipts: this._receipts,
    };
  }
}

export async function makeOfflineRangeReceiptEnvelope({
  plan,
  workingDir,
  runId,
}) {
  const recorder = new B4AOfflineRangeRecorder({ plan, workingDir, runId });
  return {
    summaryVersion: B4A_CHECKPOINT_VERSION,
    receiptVersion: B4A_RECEIPT_VERSION,
    script: 'scripts/b4a-offline-range-recorder.mjs',
    planHash: recorder.planHash,
    plan: recorder.plan,
    workingDir,
    summary: await recorder.publishSummary(),
  };
}
