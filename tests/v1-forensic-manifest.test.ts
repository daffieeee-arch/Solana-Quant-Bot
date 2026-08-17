import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';

const manifestPath = new URL('../docs/research/V1_FORENSIC_MANIFEST.json', import.meta.url);

type Part = {
  name: string;
  rows: number;
  countTxtSha256: string;
  checksumsTxtSha256: string;
};

type Manifest = {
  schemaVersion: string;
  datasetContract: string;
  status: string;
  observedAtSource: string;
  observationMode: string;
  writerState: string;
  payloadVerification: string;
  table: {
    uuid: string;
    ddlSha256: string;
    activePartCount: number;
    physicalRows: number;
    detachedEntries: string[];
    parts: Part[];
  };
  parserProvenance: {
    repositoryCommit: string;
    sourceSha256: string;
    binarySha256: string;
    provenanceDocumentSha256: string;
  };
  coverage: {
    claim: string;
    reconciliationStatus: string;
    provenanceDocumentClaimedCompleteEpochs: number[];
    supervisorDoneEpochs: number[];
    supervisorFailedEpochs: number[];
    supervisorUnrecordedEpochs: number[];
    supervisorStatusFileSha256: Record<string, string>;
  };
  forbiddenUses: string[];
};

const SHA256 = /^[0-9a-f]{64}$/;
const GIT_SHA = /^[0-9a-f]{40}$/;
const EXPECTED_MANIFEST_SHA256 = '684f603cd46ff045044a1a24f11ead9b539e129286766119a9a94b82d707b64c';

describe('legacy v1 forensic manifest', () => {
  it('pins the read-only physical snapshot as incomplete superseded evidence', async () => {
    const raw = await readFile(manifestPath, 'utf8');
    const manifest = JSON.parse(raw) as Manifest;

    expect(createHash('sha256').update(raw).digest('hex')).toBe(EXPECTED_MANIFEST_SHA256);
    expect(manifest.schemaVersion).toBe('V1_FORENSIC_MANIFEST_1');
    expect(manifest.datasetContract).toBe('TRANSACTION_NET_SWAP_V1');
    expect(manifest.status).toBe('SUPERSEDED_NOT_PUMP_OOS_EVIDENCE');
    expect(manifest.observedAtSource).toBe('OPERATOR_SUPPLIED_NOT_VERIFIED_BY_GENERATOR');
    expect(manifest.observationMode).toBe('READ_ONLY_FILESYSTEM_SNAPSHOT');
    expect(manifest.writerState).toBe('NOT_VERIFIED_BY_GENERATOR');
    expect(manifest.payloadVerification).toBe('METADATA_ONLY_NOT_REHASHED');
    expect(manifest.coverage.claim).toBe('INCOMPLETE');
    expect(manifest.coverage.reconciliationStatus).toBe('UNRESOLVED_CONFLICTING_PROVENANCE_AND_SUPERVISOR_STATE');
    expect(manifest.coverage.provenanceDocumentClaimedCompleteEpochs).toHaveLength(17);
    expect(manifest.coverage.supervisorDoneEpochs).toEqual([978, 981, 990]);
    expect(manifest.coverage.supervisorFailedEpochs).toHaveLength(11);
    expect(manifest.coverage.supervisorUnrecordedEpochs).toEqual([989]);
    expect(Object.keys(manifest.coverage.supervisorStatusFileSha256).sort()).toEqual(['A.status', 'B.status', 'C.status']);
    for (const hash of Object.values(manifest.coverage.supervisorStatusFileSha256)) expect(hash).toMatch(SHA256);
    expect(manifest.table.activePartCount).toBe(15);
    expect(manifest.table.uuid).toBe('17c42dd2-2249-496e-8558-c6f41bedd731');
    expect(manifest.table.ddlSha256).toBe('dde1157bbc706ba26c5af5409b6ef15fae4fb5559de80d643e96846c9d21cf74');
    expect(manifest.table.parts).toHaveLength(15);
    expect(manifest.table.physicalRows).toBe(562_915_792);
    expect(manifest.table.parts.reduce((rows, part) => rows + part.rows, 0)).toBe(manifest.table.physicalRows);
    expect(new Set(manifest.table.parts.map((part) => part.name)).size).toBe(manifest.table.parts.length);
    expect(manifest.table.detachedEntries).toEqual([]);
    for (const part of manifest.table.parts) {
      expect(part.countTxtSha256).toMatch(SHA256);
      expect(part.checksumsTxtSha256).toMatch(SHA256);
    }
    expect(manifest.parserProvenance.repositoryCommit).toMatch(GIT_SHA);
    expect(manifest.parserProvenance.sourceSha256).toMatch(SHA256);
    expect(manifest.parserProvenance.binarySha256).toMatch(SHA256);
    expect(manifest.parserProvenance.provenanceDocumentSha256).toMatch(SHA256);
    expect(manifest.forbiddenUses).toContain('Pump strategy optimization');
    expect(raw).not.toContain('"status": "READY"');
  });
});
