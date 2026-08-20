import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import * as citationModule from '../scripts/ci-research-citations.mjs';

const {
  validateResearchCitationData,
  validateResearchCitationFiles,
} = citationModule;
const validateTrackedResearchPaths = (citationModule as unknown as {
  validateTrackedResearchPaths?: (paths: string[]) => string[];
}).validateTrackedResearchPaths;
const validateCitationModuleSources = (citationModule as unknown as {
  validateCitationModuleSources?: (sources: Record<string, string>, entry: string) => string[];
}).validateCitationModuleSources;

type Json = Record<string, any>;
const ROOT = process.cwd();
const EVIDENCE_PATH = 'docs/research/PUMP_ACTIVATION_EVIDENCE_EPOCH_978.json';
const SCRATCH_PATH = 'docs/research/PUMP_ACTIVATION_SCRATCH_MANIFEST.json';
const DOC_PATH = 'docs/PHASE7A_PUMP_ACTIVATION_EVIDENCE.md';

const load = <T = Json>(path: string): T => JSON.parse(readFileSync(path, 'utf8')) as T;
const clone = <T>(value: T): T => structuredClone(value);
const baseline = () => ({
  evidence: load(EVIDENCE_PATH),
  scratchManifest: load(SCRATCH_PATH),
  documents: { [DOC_PATH]: readFileSync(DOC_PATH, 'utf8') },
});
const errors = (mutate?: (fixture: ReturnType<typeof baseline>) => void) => {
  const fixture = baseline();
  mutate?.(fixture);
  return validateResearchCitationData(fixture).join('\n');
};

const firstImmutableSource = (fixture: ReturnType<typeof baseline>) =>
  fixture.evidence.sources.find((source: Json) => source.immutableRefRequired === true);
const firstLoadBearingClaim = (fixture: ReturnType<typeof baseline>) =>
  fixture.evidence.claims.find((claim: Json) => claim.loadBearing === true);

describe('offline Phase 7B research citation gate', () => {
  it('accepts the tracked activation evidence, scratch binding and document markers', () => {
    expect(validateResearchCitationData(baseline())).toEqual([]);
    expect(validateResearchCitationFiles({ root: ROOT })).toEqual([]);
  });

  it('runs the actual CLI successfully without network access', () => {
    expect(execFileSync(process.execPath, ['scripts/ci-research-citations.mjs'], {
      cwd: ROOT,
      encoding: 'utf8',
      env: { ...process.env, NO_PROXY: '*', HTTPS_PROXY: 'http://127.0.0.1:1', HTTP_PROXY: 'http://127.0.0.1:1' },
    })).toMatch(/Research citation gate PASS/);
  });

  it('rejects a missing required primary source, missing required claim, and unknown claim source ID', () => {
    expect(errors(({ evidence }) => {
      evidence.sources = evidence.sources.filter((source: Json) => source.sourceId !== 'SRC-AGAVE-BPF-LOADER');
    })).toMatch(/required source|source ID/i);
    expect(errors(({ evidence }) => {
      evidence.claims = evidence.claims.filter((claim: Json) => claim.claimId !== 'ACT-BINARY-UNPROVEN');
    })).toMatch(/required claim|claim ID/i);
    expect(errors(({ evidence }) => { firstLoadBearingClaim({ evidence } as any).sourceIds = ['SRC-NOT-THERE']; })).toMatch(/unknown source/i);
  });

  it('rejects duplicate source and claim IDs', () => {
    expect(errors(({ evidence }) => evidence.sources.push(clone(evidence.sources[0])))).toMatch(/duplicate source/i);
    expect(errors(({ evidence }) => evidence.claims.push(clone(evidence.claims[0])))).toMatch(/duplicate claim/i);
  });

  it('rejects mutable GitHub main/master URLs when an immutable ref is required', () => {
    expect(errors((fixture) => {
      const source = firstImmutableSource(fixture);
      source.url = 'https://raw.githubusercontent.com/anza-xyz/solana-sdk/master/loader-v3-interface/src/state.rs';
    })).toMatch(/mutable GitHub|immutable ref/i);
  });

  it('rejects missing, malformed and scratch-mismatched response hashes', () => {
    expect(errors(({ evidence }) => { delete evidence.sources[0].responseSha256; })).toMatch(/response.*sha/i);
    expect(errors(({ evidence }) => { evidence.sources[0].responseSha256 = '0'.repeat(64); })).toMatch(/scratch.*hash|response.*hash/i);
    expect(errors(({ evidence }) => { evidence.sources[0].responseSha256 = 'xyz'; })).toMatch(/response.*sha/i);
  });

  it('rejects missing or drifted scratch-inventory binding', () => {
    expect(errors(({ evidence }) => { delete evidence.scratchBinding.inventorySha256; })).toMatch(/scratch.*inventory/i);
    expect(errors(({ evidence }) => { evidence.scratchBinding.inventorySha256 = '0'.repeat(64); })).toMatch(/scratch.*inventory/i);
    expect(errors(({ scratchManifest }) => { scratchManifest.inventory.sha256 = '0'.repeat(64); })).toMatch(/scratch.*inventory/i);
  });

  it('rejects caller-rebound scratch file and response hashes against the pinned inventory digest', () => {
    expect(errors(({ evidence, scratchManifest }) => {
      const source = evidence.sources.find((candidate: Json) => candidate.scratchPath !== 'scratch-inventory.json');
      const file = scratchManifest.sourceInventory.files.find((candidate: Json) => candidate.path === source.scratchPath);
      source.responseSha256 = '0'.repeat(64);
      file.sha256 = '0'.repeat(64);
    })).toMatch(/scratch.*inventory.*digest|inventory.*hash/i);
  });

  it('rejects a non-primary source for a load-bearing activation claim even when self-labelled primary', () => {
    expect(errors((fixture) => {
      const claim = firstLoadBearingClaim(fixture);
      const source = fixture.evidence.sources.find((candidate: Json) => candidate.sourceId === claim.sourceIds[0]);
      source.primary = false;
    })).toMatch(/load-bearing.*primary|primary source/i);
    expect(errors((fixture) => {
      const claim = firstLoadBearingClaim(fixture);
      const source = fixture.evidence.sources.find((candidate: Json) => candidate.sourceId === claim.sourceIds[0]);
      source.primary = true;
      source.sourceType = 'THIRD_PARTY_EXPLORER';
    })).toMatch(/source type|primary source/i);
    expect(errors((fixture) => {
      const claim = firstLoadBearingClaim(fixture);
      const source = fixture.evidence.sources.find((candidate: Json) => candidate.sourceId === claim.sourceIds[0]);
      source.primary = true;
      source.url = 'https://explorer.example.invalid/parsed';
    })).toMatch(/source URL|official source/i);
  });

  it('rejects citations to absent documents and absent manifest fields', () => {
    expect(errors(({ evidence }) => { evidence.claims[0].documentRefs[0].path = 'docs/DOES_NOT_EXIST.md'; })).toMatch(/document.*does not exist/i);
    expect(errors(({ evidence }) => { evidence.claims[0].evidencePointers = ['/does/not/exist']; })).toMatch(/evidence pointer/i);
  });

  it('rejects missing document claim markers', () => {
    expect(errors((fixture) => {
      const marker = fixture.evidence.claims[0].documentRefs[0].marker;
      fixture.documents[DOC_PATH] = fixture.documents[DOC_PATH].replace(marker, '');
    })).toMatch(/claim marker/i);
  });

  it('rejects stale or promoted activation verdicts', () => {
    expect(errors(({ evidence }) => { evidence.activationVerdict = 'PROVEN_AT_SLOT_RANGE'; })).toMatch(/activation verdict/i);
    expect(errors(({ evidence }) => { evidence.promotionCount = 1; })).toMatch(/promotion.*zero|proven/i);
    expect(errors(({ evidence }) => {
      evidence.registryEntries[0].recommendedRegistryVerdict = 'PROVEN_AT_SLOT_RANGE';
      evidence.registryEntries[0].provenAtSlotRange = true;
    })).toMatch(/registry.*unproven|proven/i);
  });

  it.each([
    ['candidate blocktime falsification', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.candidateRange.startBlockTime = 0; }],
    ['upgrade boundary falsification', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.upgradeHistory.lastUpgradeBeforeRange = 1; }],
    ['history completeness promotion', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.upgradeHistory.classification = 'COMPLETE_UPGRADE_HISTORY'; }],
    ['binary mapping promotion', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.binary.classification = 'SOURCE_TO_BINARY_PROVEN'; }],
    ['official structure falsification', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.officialIdl.allTenEntriesPresent = false; }],
    ['onchain structure falsification', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.onchainIdl.allTenRelevantStructuresMatchOfficial = false; }],
    ['range observation promotion', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.rangeObservations.status = 'PROVEN_AT_SLOT_RANGE'; }],
    ['network budget falsification', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.networkBudget.responseBodyBytes = 1; }],
    ['index payload falsification', (fixture: ReturnType<typeof baseline>) => { fixture.evidence.networkBudget.indexPayloadBytes = 1; }],
    ['source identity/body swap', (fixture: ReturnType<typeof baseline>) => {
      const first = fixture.evidence.sources.find((source: Json) => source.sourceId === 'SRC-SOLANA-SLOT-START-TIME');
      const second = fixture.evidence.sources.find((source: Json) => source.sourceId === 'SRC-SOLANA-SLOT-END-TIME');
      [first.sourceId, second.sourceId] = [second.sourceId, first.sourceId];
    }],
  ])('rejects %s against the trusted evidence digest', (_label, mutate) => {
    expect(errors(mutate)).toMatch(/trusted evidence semantic digest/i);
  });

  it.each([
    ['verdict prose', 'activationVerdict: HOLD_UNPROVEN_ACTIVATION', 'activationVerdict: PROVEN_AT_SLOT_RANGE'],
    ['promotion prose', 'Zero of ten entries meet', 'One of ten entries meets'],
    ['registry prose', 'STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION', 'PROVEN_AT_SLOT_RANGE'],
  ])('rejects contradictory %s while claim markers remain', (_label, from, to) => {
    expect(errors((fixture) => {
      fixture.documents[DOC_PATH] = fixture.documents[DOC_PATH].replace(from, to);
    })).toMatch(/trusted citation document digest/i);
  });

  it('rejects researchReady or pilotEligible promotion', () => {
    expect(errors(({ evidence }) => { evidence.researchReady = true; })).toMatch(/researchReady.*false/i);
    expect(errors(({ evidence }) => { evidence.pilotEligible = true; })).toMatch(/pilotEligible.*false/i);
  });

  it('rejects explicit or implicit bandwidth-preflight and Pilot A authorization', () => {
    expect(errors(({ evidence }) => { evidence.authorizations.bandwidthPreflightAuthorized = true; })).toMatch(/authorization.*false|bandwidth/i);
    expect(errors(({ evidence }) => { evidence.authorizations.pilotAExecutionAuthorized = true; })).toMatch(/authorization.*false|Pilot A/i);
    expect(errors((fixture) => {
      fixture.documents[DOC_PATH] += '\nPilot A: authorized\n';
    })).toMatch(/implicit.*authorization|Pilot A.*authorized/i);
    expect(errors((fixture) => {
      fixture.documents[DOC_PATH] += '\nPilot A is authorized.\n';
    })).toMatch(/implicit.*authorization|trusted citation document digest/i);
    expect(errors((fixture) => {
      fixture.documents[DOC_PATH] += '\nBandwidth preflight is approved.\n';
    })).toMatch(/implicit.*authorization|trusted citation document digest/i);
  });

  it('rejects tracked raw scratch response paths', () => {
    expect(validateTrackedResearchPaths).toBeTypeOf('function');
    expect(validateTrackedResearchPaths?.([
      'docs/research/PUMP_ACTIVATION_EVIDENCE_EPOCH_978.json',
      'docs/research/PUMP_ACTIVATION_SCRATCH_MANIFEST.json',
    ])).toEqual([]);
    for (const path of [
      'docs/research/responses/999-probe.body',
      'docs/research/999-probe.body',
      'docs/research/responses/999-probe.meta.json',
    ]) {
      expect(validateTrackedResearchPaths?.([path]).join('\n')).toMatch(/raw scratch response/i);
    }
  });

  it.each([
    ['direct bare http', (source: string) => `import 'http';\n${source}`, {}],
    ['computed dynamic import', (source: string) => `${source}\nawait import('node:' + 'http');\n`, {}],
    ['createRequire loader', (source: string) => `${source}\ncreateRequire(import.meta.url)('http');\n`, {}],
    ['transitive local helper', (source: string) => `import './probe-helper.mjs';\n${source}`, {
      'scripts/probe-helper.mjs': "import 'node:http';\n",
    }],
  ])('rejects %s in the complete citation module graph', (_label, mutate, extras) => {
    expect(validateCitationModuleSources).toBeTypeOf('function');
    const sources = {
      'scripts/ci-research-citations.mjs': mutate(readFileSync('scripts/ci-research-citations.mjs', 'utf8')),
      ...extras,
    };
    expect(validateCitationModuleSources?.(sources, 'scripts/ci-research-citations.mjs').join('\n')).toMatch(/offline module graph|forbidden|dynamic import|loader/i);
  });

  it('is implemented only with offline deterministic Node built-ins', () => {
    const source = readFileSync('scripts/ci-research-citations.mjs', 'utf8');
    expect(source).not.toMatch(/node:(?:http|https|net|tls|dns|dgram|child_process|worker_threads)/);
    expect(source).not.toMatch(/\bfetch\s*\(|WebSocket|XMLHttpRequest/);
    expect(source).toMatch(/node:fs/);
  });
});
