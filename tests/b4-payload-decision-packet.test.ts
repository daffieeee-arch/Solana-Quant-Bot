import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const PACKET_PATH = 'docs/research/B4_PAYLOAD_RUN_DECISION_PACKET.json';
const DOC_PATH = 'docs/research/B4_PAYLOAD_RUN_DECISION_PACKET.md';
const SYNC_PATH = 'docs/research/TERRAPC_GITHUB_SYNC_STATUS.md';
const B5_PATH = 'docs/research/B5_ENTRY_GATE.md';
const EDGE_PATH = 'docs/research/EDGE_PATH_B6_B8_CONTRACT.md';
const LIVE_PATH = 'docs/research/SHADOW_PAPER_LIVE_GATES.md';

describe('B4 payload decision packet', () => {
  const packet = JSON.parse(readFileSync(PACKET_PATH, 'utf8'));
  const doc = readFileSync(DOC_PATH, 'utf8');

  it('stays unapproved and non-executable', () => {
    expect(packet.schemaVersion).toBe('B4_PAYLOAD_RUN_DECISION_PACKET_1');
    expect([
      packet.approved,
      packet.networkEnabled,
      packet.readyToRun,
      packet.executablePlan,
      packet.creditSpendAuthorized,
    ]).toEqual([false, false, false, false, false]);
    expect(packet.purpose).toBe('ENGINEERING_VALIDATION_ONLY');
    expect(packet.sliceClass).toBe('ENGINEERING_VALIDATION_ONLY');
    expect(packet.bindsToAuthenticMetadata.metadataGoReusable).toBe(false);
    expect(packet.candidatePayload.status).toBe('UNAPPROVED_CANDIDATE_ONLY');
    expect(
      packet.remainingAggregateCapsAfterMetadata.remainderIsNotPayloadAuthorization,
    ).toBe(true);
  });

  it('pins the retained metadata result hash and forbids the doc-only window', () => {
    expect(packet.bindsToAuthenticMetadata.metadataRunResultSha256).toBe(
      '8c77b3f6d7a1daec92835284bdc81ba4e18cb0a0166abcec5135252da5c81b21',
    );
    expect(packet.candidatePayload.rejectedAlternate.slotRange).toEqual([
      422506000, 422506128,
    ]);
    expect(packet.hostAllowlist).toEqual(['files.old-faithful.net']);
    expect(packet.redirectsAllowed).toBe(false);
  });

  it('documents the hard stop in prose', () => {
    expect(doc).toContain('`approved: false`');
    expect(doc).toContain('authorizes **no** OF1/Triton call');
    expect(doc).toContain('8c77b3f6d7a1daec92835284bdc81ba4e18cb0a0166abcec5135252da5c81b21');
  });
});

describe('post-B4 gated path docs', () => {
  it('keep later phases blocked and non-claiming', () => {
    const sync = readFileSync(SYNC_PATH, 'utf8');
    const b5 = readFileSync(B5_PATH, 'utf8');
    const edge = readFileSync(EDGE_PATH, 'utf8');
    const live = readFileSync(LIVE_PATH, 'utf8');

    expect(sync).toContain('79d1e9c828525d7eaf3efa5529d1d7b0952fb8cd');
    expect(sync).toContain('daffieeee-arch/Solana-Quant-Bot');
    expect(b5).toContain('Current state: **blocked**');
    expect(b5).toContain('permanently excluded');
    expect(edge).toContain('INSUFFICIENT_SAMPLE');
    expect(edge).toContain('FALSIFIED');
    expect(edge).toContain('Profitability is never a milestone label');
    expect(live).toContain('authorizes no wallet, order, or live funds');
    expect(live).toContain('Phases 7–10 are **not started**');
  });
});
