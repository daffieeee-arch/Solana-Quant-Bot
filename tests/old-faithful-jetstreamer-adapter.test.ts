import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import bs58 from 'bs58';
import { base58Encode, deriveBondingCurve, PUMP_PROGRAM_ID } from '../src/pump-address.js';
import { PUMP_DISCRIMINATORS } from '../src/pump-parser.js';
import {
  adaptOldFaithfulJetstreamerPumpRecord,
  buildOldFaithfulCoverageLedger,
} from '../src/research/old-faithful-jetstreamer-adapter.js';

const EPOCH = 1_000;
const EPOCH_START = 432_000_000;
const EPOCH_END = 432_432_000;
const signature = bs58.encode(Buffer.alloc(64, 7));
const secondSignature = bs58.encode(Buffer.alloc(64, 8));
const payer = base58Encode(Buffer.alloc(32, 1));
const mint = base58Encode(Buffer.alloc(32, 4));
const curve = deriveBondingCurve(mint);

function sourceManifest() {
  return {
    schemaVersion: 'OLD_FAITHFUL_EPOCH_SOURCE_1',
    epoch: EPOCH,
    epochCid: 'bafyreihvgnloaiilehijbe42cloousmwvl2z666zr7wvqprs6wnqfeqo4q',
    carSha256: '3c9727378e617f5cba8b5e206bce8fc6ae5df34d3a4408eeb69aea4d62ac7218',
    carFileSizeBytes: '767389334224',
    slotsFileSha256: 'b04cec20c168d256fadcebc8626b711ac12558b207142dd6872d0deb382e8930',
    slotsFileSizeBytes: 4_317_820,
    slotsFileEntryCount: 431_782,
    slotsFirst: 431_999_999,
    slotsLast: 432_431_999,
    slotRange: { startInclusive: EPOCH_START, endExclusive: EPOCH_END },
  };
}

function adapterProvenance() {
  return {
    schemaVersion: 'JETSTREAMER_ADAPTER_PROVENANCE_1',
    jetstreamerGitSha: 'cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24',
    pluginGitSha: 'a'.repeat(40),
    pluginSourceSha256: 'b'.repeat(64),
  };
}

function manifestForSlots(slotsFileBytes: string) {
  const slots = slotsFileBytes.trimEnd().split('\n').map(Number);
  const slotsFirst = slots[0];
  const slotsLast = slots.at(-1);
  if (slotsFirst === undefined || slotsLast === undefined) throw new Error('empty test slot inventory');
  return {
    ...sourceManifest(),
    slotsFileSha256: createHash('sha256').update(slotsFileBytes).digest('hex'),
    slotsFileSizeBytes: Buffer.byteLength(slotsFileBytes),
    slotsFileEntryCount: slots.length,
    slotsFirst,
    slotsLast,
  };
}

function record(overrides: { signature?: string; transactionSlotIndex?: number; slot?: number } = {}) {
  const slot = overrides.slot ?? EPOCH_START + 1;
  return {
    schemaVersion: 'JETSTREAMER_TRANSACTION_BLOCK_1',
    block: { slot, blockTimeUnixSeconds: 1_786_924_800 },
    transaction: {
      slot,
      transactionSlotIndex: overrides.transactionSlotIndex ?? 7,
      signature: overrides.signature ?? signature,
      isVote: false,
      message: {
        staticAccountKeys: [payer, PUMP_PROGRAM_ID, mint, curve],
        instructions: [{
          programIdIndex: 1,
          accountIndices: [2, 3],
          dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(8)}`,
        }],
      },
      meta: {
        err: null,
        feeLamports: '5000',
        logMessages: ['Program log: Instruction: Buy'],
        loadedAddresses: { writable: [], readonly: [] },
        preBalancesLamports: ['10000', '0', '0', '5000'],
        postBalancesLamports: ['4000', '0', '0', '11000'],
        innerInstructionGroups: [],
        preTokenBalances: [],
        postTokenBalances: [],
      },
    },
  };
}

function block(slot: number, blockTimeUnixSeconds = 1_786_924_800) {
  return { schemaVersion: 'JETSTREAMER_BLOCK_1', slot, status: 'block', blockTimeUnixSeconds };
}

function skipped(slot: number) {
  return { schemaVersion: 'JETSTREAMER_BLOCK_1', slot, status: 'possible_leader_skipped', blockTimeUnixSeconds: null };
}

function adapt(manifest = sourceManifest(), provenance = adapterProvenance(), input = record()) {
  return adaptOldFaithfulJetstreamerPumpRecord(manifest, provenance, input);
}

function build(
  manifest: unknown,
  requestedSlotRange: { startInclusive: number; endExclusive: number },
  slotsFileBytes: string,
  blockObservations: unknown[],
  transactionObservations: unknown[],
  provenance: unknown = adapterProvenance(),
) {
  return buildOldFaithfulCoverageLedger(
    manifest,
    provenance,
    requestedSlotRange,
    slotsFileBytes,
    blockObservations,
    transactionObservations,
  );
}

describe('Old Faithful Jetstreamer Pump adapter', () => {
  it('binds a Jetstreamer transaction/block envelope to separate source and adapter provenance', () => {
    const observation = adapt();

    expect(observation).toMatchObject({
      schemaVersion: 'OLD_FAITHFUL_PUMP_V2_OBSERVATION_1',
      sourceManifest: sourceManifest(),
      sourceManifestSha256: expect.stringMatching(/^[0-9a-f]{64}$/),
      adapterProvenance: adapterProvenance(),
      adapterProvenanceSha256: expect.stringMatching(/^[0-9a-f]{64}$/),
      bronze: {
        schemaVersion: 'PUMP_V2_BRONZE_TRANSACTION_1',
        slot: EPOCH_START + 1,
        transactionIndex: 7,
        signature,
        blockTime: '2026-08-17T00:00:00.000Z',
        executionStatus: 'succeeded',
        feeLamports: '5000',
        accountKeys: [payer, PUMP_PROGRAM_ID, mint, curve],
        pumpCandidates: [{
          parserStatus: 'known_discriminator',
          variant: 'buy',
          kind: 'buy',
          mint,
          curve,
          executionStatus: 'succeeded',
        }],
        quarantines: [],
      },
    });
  });

  it('persists the static-account boundary and rejects a top-level program loaded from dynamic addresses', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const observation = adapt(manifest);
    const persisted = observation as unknown as Record<string, unknown>;

    expect(persisted.staticAccountCount).toBe(4);
    persisted.staticAccountCount = 1;
    expect(() => build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [block(EPOCH_START + 1)],
      [observation],
    )).toThrow(/invalid_transaction_observation/);
  });

  it('keeps source identity stable when only adapter provenance changes', () => {
    const first = adapt();
    const second = adapt(sourceManifest(), {
      ...adapterProvenance(),
      pluginGitSha: 'c'.repeat(40),
      pluginSourceSha256: 'd'.repeat(64),
    });

    expect(second.sourceManifestSha256).toBe(first.sourceManifestSha256);
    expect(second.adapterProvenanceSha256).not.toBe(first.adapterProvenanceSha256);
  });

  it.each([
    ['wrong manifest schema', { manifest: { schemaVersion: 'OLD_FAITHFUL_EPOCH_SOURCE_2' } }, /invalid_source_manifest/],
    ['unknown manifest field', { manifest: { futureSelfApproval: true } }, /invalid_source_manifest/],
    ['negative epoch', { manifest: { epoch: -1 } }, /invalid_source_manifest/],
    ['epoch/range mismatch', { manifest: { epoch: 999 } }, /invalid_source_manifest/],
    ['zero CAR size', { manifest: { carFileSizeBytes: '0' } }, /invalid_source_manifest/],
    ['non-canonical epoch CID', { manifest: { epochCid: 'BAFY-not-canonical' } }, /invalid_source_manifest/],
    ['regex-shaped but structurally invalid CID', { manifest: { epochCid: `b${'a'.repeat(58)}` } }, /invalid_source_manifest/],
    ['malformed CAR hash', { manifest: { carSha256: 'abc' } }, /invalid_source_manifest/],
    ['wrong provenance schema', { provenance: { schemaVersion: 'JETSTREAMER_ADAPTER_PROVENANCE_2' } }, /invalid_adapter_provenance/],
    ['unknown provenance field', { provenance: { approval: 'self' } }, /invalid_adapter_provenance/],
    ['malformed Jetstreamer revision', { provenance: { jetstreamerGitSha: 'cffaf3d' } }, /invalid_adapter_provenance/],
    ['block and transaction slot mismatch', { record: { block: { slot: EPOCH_START + 2 } } }, /jetstreamer_slot_mismatch/],
    ['transaction outside source epoch', { record: { block: { slot: EPOCH_END }, transaction: { slot: EPOCH_END } } }, /transaction_outside_source_range/],
    ['vote transaction', { record: { transaction: { isVote: true } } }, /vote_transaction_not_supported/],
    ['fractional block time', { record: { block: { blockTimeUnixSeconds: 1_786_924_800.5 } } }, /invalid_block_time/],
  ] as const)('rejects %s before Bronze capture', (_label, mutation, error) => {
    const candidateManifest = structuredClone(sourceManifest()) as Record<string, unknown>;
    const candidateProvenance = structuredClone(adapterProvenance()) as Record<string, unknown>;
    const candidateRecord = structuredClone(record()) as Record<string, unknown>;
    if ('manifest' in mutation) Object.assign(candidateManifest, mutation.manifest);
    if ('provenance' in mutation) Object.assign(candidateProvenance, mutation.provenance);
    if ('record' in mutation) {
      for (const [key, value] of Object.entries(mutation.record)) {
        candidateRecord[key] = typeof value === 'object' && value !== null
          ? { ...(candidateRecord[key] as Record<string, unknown>), ...value }
          : value;
      }
    }
    expect(() => adaptOldFaithfulJetstreamerPumpRecord(
      candidateManifest,
      candidateProvenance,
      candidateRecord,
    )).toThrow(error);
  });

  it('reconciles a bounded requested range against the complete pinned epoch inventory', () => {
    const slotsFileBytes = '431999999\n432000000\n432000001\n432000002\n432000003\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 3 },
      slotsFileBytes,
      [block(EPOCH_START + 2, 1_786_924_802), block(EPOCH_START + 1)],
      [],
    );

    expect(ledger).toMatchObject({
      sourceManifestSha256: adapt(manifest).sourceManifestSha256,
      adapterProvenanceSha256: adapt(manifest).adapterProvenanceSha256,
      slotRange: { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 3 },
      callbackRangeComplete: true,
      archiveSlotInventoryReconciled: true,
      coverageStatus: 'ARCHIVE_SLOT_INVENTORY_RECONCILED',
      researchReady: false,
      observedRanges: [{ startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 3 }],
      missingRanges: [],
    });
  });

  it('finalizes deterministic coverage with archive-backed skipped slots and exact retries', () => {
    const slotsFileBytes = '431999999\n432000000\n432000002\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const requested = { startInclusive: EPOCH_START, endExclusive: EPOCH_START + 3 };
    const ledger = build(manifest, requested, slotsFileBytes, [
      block(EPOCH_START + 2, 1_786_924_802),
      block(EPOCH_START),
      skipped(EPOCH_START + 1),
      block(EPOCH_START),
    ], []);

    expect(ledger).toEqual({
      schemaVersion: 'OLD_FAITHFUL_COVERAGE_LEDGER_1',
      sourceManifestSha256: adapt(manifest).sourceManifestSha256,
      adapterProvenanceSha256: adapt(manifest).adapterProvenanceSha256,
      slotRange: requested,
      callbackRangeComplete: true,
      archiveSlotInventoryReconciled: true,
      coverageStatus: 'ARCHIVE_SLOT_INVENTORY_RECONCILED',
      researchReady: false,
      observedRanges: [requested],
      missingRanges: [],
      blockCount: 2,
      possibleLeaderSkippedCount: 1,
      resolvedPossibleLeaderSkippedCount: 0,
      duplicateBlockObservationCount: 1,
      transactionProjection: 'PUMP_V2_NON_VOTE_ONLY',
      transactionObservationCount: 0,
      duplicateTransactionObservationCount: 0,
      pumpCandidateCount: 0,
      quarantinedCandidateCount: 0,
      observationsSha256: expect.stringMatching(/^[0-9a-f]{64}$/),
    });
  });

  it.each([
    ['skip then block', [skipped(EPOCH_START + 1), block(EPOCH_START + 1)]],
    ['block then skip', [block(EPOCH_START + 1), skipped(EPOCH_START + 1)]],
  ] as const)('resolves a provisional callback when a matching archived block arrives: %s', (_label, observations) => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [...observations],
      [],
    );

    expect(ledger).toMatchObject({
      callbackRangeComplete: true,
      archiveSlotInventoryReconciled: true,
      blockCount: 1,
      possibleLeaderSkippedCount: 0,
      resolvedPossibleLeaderSkippedCount: 1,
    });
  });

  it('counts a provisional slot resolution once when a later provisional retry follows the definitive block', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [skipped(EPOCH_START + 1), block(EPOCH_START + 1), skipped(EPOCH_START + 1)],
      [],
    );

    expect(ledger).toMatchObject({
      archiveSlotInventoryReconciled: true,
      resolvedPossibleLeaderSkippedCount: 1,
      duplicateBlockObservationCount: 1,
    });
  });

  it('does not call archive inventory reconciled while callback coverage is incomplete', () => {
    const slotsFileBytes = '431999999\n432000000\n432000002\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START, endExclusive: EPOCH_START + 3 },
      slotsFileBytes,
      [block(EPOCH_START), block(EPOCH_START + 2)],
      [],
    );

    expect(ledger).toMatchObject({
      callbackRangeComplete: false,
      archiveSlotInventoryReconciled: false,
      coverageStatus: 'CALLBACK_RANGE_ONLY_NOT_ARCHIVE_VERIFIED',
      missingRanges: [{ startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 }],
    });
  });

  it('keeps an inventory-backed provisional skip non-reconciled without a block callback', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [skipped(EPOCH_START + 1)],
      [],
    );

    expect(ledger).toMatchObject({
      callbackRangeComplete: true,
      archiveSlotInventoryReconciled: false,
      coverageStatus: 'CALLBACK_RANGE_ONLY_NOT_ARCHIVE_VERIFIED',
      researchReady: false,
    });
  });

  it('deduplicates exact and semantically identical reordered transaction retries', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const observation = adapt(manifest);
    const reordered = {
      bronze: structuredClone(observation.bronze),
      adapterProvenanceSha256: observation.adapterProvenanceSha256,
      adapterProvenance: structuredClone(observation.adapterProvenance),
      sourceManifestSha256: observation.sourceManifestSha256,
      sourceManifest: structuredClone(observation.sourceManifest),
      staticAccountCount: observation.staticAccountCount,
      schemaVersion: observation.schemaVersion,
    };
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [block(EPOCH_START + 1)],
      [observation, observation, reordered],
    );

    expect(ledger).toMatchObject({
      transactionObservationCount: 1,
      duplicateTransactionObservationCount: 2,
      pumpCandidateCount: 1,
      quarantinedCandidateCount: 0,
    });
  });

  it.each([
    ['same slot/index with another signature', adapt(sourceManifest(), adapterProvenance(), record({ signature: secondSignature }))],
    ['same signature at another index', adapt(sourceManifest(), adapterProvenance(), record({ transactionSlotIndex: 8 }))],
  ] as const)('rejects conflicting transaction identity: %s', (_label, conflicting) => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const first = adapt(manifest);
    const rebound = {
      ...conflicting,
      sourceManifest: first.sourceManifest,
      sourceManifestSha256: first.sourceManifestSha256,
    };

    expect(() => build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [block(EPOCH_START + 1)],
      [first, rebound],
    )).toThrow(/conflicting_transaction_observation/);
  });

  it('accepts a legitimate failed Bronze observation without losing candidate coverage', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const failedRecord = record();
    (failedRecord.transaction.meta as { err: unknown }).err = { InstructionError: [0, { Custom: 6_001 }] };
    const observation = adapt(manifest, adapterProvenance(), failedRecord);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [block(EPOCH_START + 1)],
      [observation],
    );

    expect(observation.bronze.executionStatus).toBe('failed');
    expect(observation.bronze.pumpCandidates[0].executionStatus).toBe('failed');
    expect(ledger).toMatchObject({
      transactionObservationCount: 1,
      pumpCandidateCount: 1,
      quarantinedCandidateCount: 0,
    });
  });

  it('accepts a legitimate unknown-discriminator quarantine and counts it exactly once', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const quarantinedRecord = record();
    quarantinedRecord.transaction.message.instructions[0].dataHex = `ffffffffffffffff${'00'.repeat(8)}`;
    const observation = adapt(manifest, adapterProvenance(), quarantinedRecord);
    const ledger = build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [block(EPOCH_START + 1)],
      [observation],
    );

    expect(observation.bronze.pumpCandidates[0].parserStatus).toBe('quarantined');
    expect(observation.bronze.quarantines).toHaveLength(1);
    expect(ledger).toMatchObject({
      transactionObservationCount: 1,
      pumpCandidateCount: 1,
      quarantinedCandidateCount: 1,
    });
  });

  it.each([
    ['fabricated candidate', (observation: ReturnType<typeof adapt>) => { observation.bronze.pumpCandidates = [{ invented: true }] as never; }],
    ['duplicate valid-shaped candidate with forged event identity', (observation: ReturnType<typeof adapt>) => {
      observation.bronze.pumpCandidates.push({
        ...observation.bronze.pumpCandidates[0],
        eventKey: 'forged-valid-shaped-candidate',
      });
    }],
    ['known discriminator with mismatched variant and kind', (observation: ReturnType<typeof adapt>) => {
      observation.bronze.pumpCandidates[0].variant = 'sell';
      observation.bronze.pumpCandidates[0].kind = 'sell';
    }],
    ['impossible top-level stack height', (observation: ReturnType<typeof adapt>) => {
      observation.bronze.instructions[0].stackHeight = 2;
    }],
    ['duplicate resolved account key', (observation: ReturnType<typeof adapt>) => {
      observation.bronze.accountKeys[0] = PUMP_PROGRAM_ID;
    }],
    ['out-of-range token-balance account index', (observation: ReturnType<typeof adapt>) => {
      observation.bronze.preTokenBalances = [{
        accountIndex: 999,
        mint,
        owner: payer,
        programId: PUMP_PROGRAM_ID,
        decimals: 6,
        amount: '1',
      }];
    }],
    ['duplicate token-balance account index', (observation: ReturnType<typeof adapt>) => {
      const balance = {
        accountIndex: 0,
        mint,
        owner: payer,
        programId: PUMP_PROGRAM_ID,
        decimals: 6,
        amount: '1',
      };
      observation.bronze.preTokenBalances = [balance, { ...balance, amount: '2' }];
    }],
    ['producer log-byte limit exceeded', (observation: ReturnType<typeof adapt>) => {
      observation.bronze.logMessages = ['x'.repeat(10_001)];
    }],
    ['instruction-reference budget and coordinates violated', (observation: ReturnType<typeof adapt>) => {
      const accountIndices = Array.from({ length: 50 }, (_, index) => index % 4);
      const accounts = accountIndices.map((index) => observation.bronze.accountKeys[index]);
      observation.bronze.instructions = Array.from({ length: 100 }, () => ({
        instructionLocation: 'top_level' as const,
        instructionIndex: 0,
        programIdIndex: 0,
        programId: observation.bronze.accountKeys[0],
        accountIndices,
        accounts,
        dataHex: '',
      }));
      observation.bronze.pumpCandidates = [];
      observation.bronze.quarantines = [];
    }],
    ['sparse instructions', (observation: ReturnType<typeof adapt>) => { observation.bronze.instructions = new Array(1); }],
    ['unknown Bronze field', (observation: ReturnType<typeof adapt>) => { (observation.bronze as unknown as Record<string, unknown>).padding = 'x'; }],
    ['oversized nested text', (observation: ReturnType<typeof adapt>) => { observation.bronze.logMessages = ['x'.repeat(4_194_305)]; }],
  ] as const)('rejects corrupted or unbounded Bronze observations: %s', (_label, mutate) => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    const observation = adapt(manifest);
    mutate(observation);

    expect(() => build(
      manifest,
      { startInclusive: EPOCH_START + 1, endExclusive: EPOCH_START + 2 },
      slotsFileBytes,
      [block(EPOCH_START + 1)],
      [observation],
    )).toThrow(/invalid_transaction_observation/);
  });

  it('rejects a requested range outside the pinned epoch source range', () => {
    const slotsFileBytes = '431999999\n432000001\n432431999\n';
    const manifest = manifestForSlots(slotsFileBytes);
    expect(() => build(
      manifest,
      { startInclusive: EPOCH_END, endExclusive: EPOCH_END + 1 },
      slotsFileBytes,
      [],
      [],
    )).toThrow(/invalid_requested_slot_range/);
  });
});
