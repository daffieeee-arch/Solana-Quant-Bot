// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ResearchCockpit } from './ResearchCockpit';

const summary = {
  schemaVersion: 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1',
  sourceClass: 'SYNTHETIC_FIXTURE_ONLY',
  runId: 'run-71f6c3d81f6d49dbf20af4bb17322a0d',
  observedAt: '2026-08-20T20:00:00.000Z',
  evidenceClass: 'SYNTHETIC',
  realData: false,
  acceptedSilver: false,
  researchReady: false,
  activationVerdict: 'HOLD_UNPROVEN_ACTIVATION',
  eligibility: {
    pilotEligible: false,
    transportPilot: { contractReady: true, inputMode: 'SYNTHETIC_FIXTURE_ONLY', preflightStatus: 'NOT_RUN', eligible: false, executionAuthorized: false },
    acceptedSilver: { eligible: false, activationVerdict: 'HOLD_UNPROVEN_ACTIVATION', provenRegistryEntries: 0, totalRegistryEntries: 10 },
    research: { approved: false, researchReady: false, strategyInputEligible: false, profitabilityEvidence: false },
  },
  progress: { requestedSlots: 3, reconciledSlots: 3, skippedSlots: 1, provisionalSlots: 1, resolvedSlots: 1, currentSlot: 422506002, lastCompletedSlot: 422506002, coveragePercent: 100, deterministicRerun: 'MATCH' },
  dataflow: { callbacks: 10, blocks: 3, transactions: 5, topLevelInstructions: 4, innerInstructions: 2, pumpCandidates: 5, failedPumpTransactions: 1, unknownDiscriminators: 1, quarantines: 2, exactRetries: 1, duplicateConflicts: 0 },
};
const events = { schemaVersion: 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1', cursor: 0, limit: 50, nextCursor: null, total: 1, rows: [{ schemaVersion: 'PHASE8A_EVENT_ROW_1', slot: 422506000, transactionIndex: 0, signature: 'LongSyntheticSignature111111111111111111111111111111111111111111111111111111111', instructionLocation: 'inner', parentInstructionIndex: 1, instructionIndex: 0, observedDiscriminator: '66063d1201daebea', structuralVariant: 'buy', executionStatus: 'succeeded', evidenceBadge: 'SHADOW_STRUCTURAL_OBSERVATION', quarantineReason: null, rawDetail: { source: 'SYNTHETIC', acceptedSilver: false, strategyInput: false } }] };
const quarantines = { schemaVersion: 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1', cursor: 0, limit: 50, nextCursor: null, total: 1, rows: [{ schemaVersion: 'PHASE8A_QUARANTINE_ROW_1', slot: 422506001, transactionIndex: 1, reason: 'UNKNOWN_DISCRIMINATOR', evidenceBadge: 'QUARANTINED' }] };
const provenance = { schemaVersion: 'PHASE8A_RESEARCH_PROVENANCE_RESPONSE_1', provenance: { schemaVersion: 'PHASE8A_PROVENANCE_1', sourceManifestSha256: '1'.repeat(64), configSha256: '2'.repeat(64), schemaSha256: '3'.repeat(64), reducerGitSha: '4'.repeat(40), inputSha256: '5'.repeat(64), aggregateOutputSha256: '6'.repeat(64), rerunSha256: '6'.repeat(64), approvalStatus: 'CANDIDATE_UNAPPROVED', completeness: 'FIXTURE_COMPLETE', uncertainty: 'UNPROVEN_ACTIVATION' } };
const metrics = { schemaVersion: 'PHASE8A_RESEARCH_METRICS_RESPONSE_1', metrics: { bytesRead: 18000, bytesWritten: 42000, outputBytes: 42000, queueDepth: 2, peakRssBytes: 24000000, stageDurationsMs: { validate: 2, reduce: 5, publish: 3 }, walClean: 1, checkpointPublished: 1, quarantineByReason: { UNKNOWN_DISCRIMINATOR: 1, FAILED_TRANSACTION: 1 } } };

function mockResearchFetch(overrides: Record<string, unknown> = {}) {
  const bodies: Record<string, unknown> = { summary, events, quarantines, provenance, metrics, ...overrides };
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const key = url.includes('/events') ? 'events' : url.includes('/quarantines') ? 'quarantines' : url.includes('/provenance') ? 'provenance' : url.includes('/metrics') ? 'metrics' : 'summary';
    const body = bodies[key];
    return Promise.resolve({ ok: body !== null, status: body === null ? 404 : 200, json: async () => body });
  }));
}

describe('ResearchCockpit', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it('renders fixture-only HOLD evidence and every false eligibility gate without trading claims', async () => {
    mockResearchFetch();
    render(<ResearchCockpit />);
    await waitFor(() => expect(screen.getByRole('heading', { name: 'RESEARCH // PILOT A' })).toBeInTheDocument());
    expect(screen.getAllByText('SYNTHETIC_FIXTURE_ONLY').length).toBeGreaterThan(0);
    expect(screen.getByText('HOLD_UNPROVEN_ACTIVATION')).toBeInTheDocument();
    expect(screen.getByText('TRANSPORT ELIGIBLE · FALSE')).toBeInTheDocument();
    expect(screen.getByText('EXECUTION AUTHORIZED · FALSE')).toBeInTheDocument();
    expect(screen.getByText('ACCEPTED SILVER · FALSE')).toBeInTheDocument();
    expect(screen.getByText('RESEARCH READY · FALSE')).toBeInTheDocument();
    expect(screen.getByText('PREFLIGHT · NOT RUN')).toBeInTheDocument();
    expect(screen.getByText('DUPLICATE CONFLICTS')).toBeInTheDocument();
    expect(screen.getByText('UNKNOWN DISCRIMINATORS')).toBeInTheDocument();
    expect(screen.getByText('SHADOW STRUCTURAL OBSERVATION')).toBeInTheDocument();
    expect(screen.getByText('Observed instruction')).toBeInTheDocument();
    expect(screen.queryByText(/executed trade|profitable trade|realized p&l|liquidity|\bfill\b/i)).not.toBeInTheDocument();
  });

  it('shows quarantine, provenance, long identifiers and resource evidence', async () => {
    mockResearchFetch();
    render(<ResearchCockpit />);
    expect(await screen.findByText('UNKNOWN DISCRIMINATOR')).toBeInTheDocument();
    expect(screen.getByText('QUARANTINED')).toBeInTheDocument();
    expect(screen.getByText('PROVENANCE')).toBeInTheDocument();
    expect(screen.getAllByText('6'.repeat(64))).toHaveLength(2);
    for (const hash of screen.getAllByText('6'.repeat(64))) expect(hash).toHaveClass('hash-value');
    expect(screen.getByText('WAL · CLEAN')).toBeInTheDocument();
    expect(screen.getByText('CHECKPOINT · PUBLISHED')).toBeInTheDocument();
  });

  it('renders an explicit unavailable state instead of zero-valued synthetic data', async () => {
    mockResearchFetch({ summary: null });
    render(<ResearchCockpit />);
    expect(await screen.findByText('RESEARCH OUTPUT UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByText(/No fixture replay provider is configured/i)).toBeInTheDocument();
    expect(screen.queryByText('TRANSPORT ELIGIBLE · TRUE')).not.toBeInTheDocument();
  });

  it('defines responsive, light-mode, focus and long-hash containment', () => {
    const styles = readFileSync('frontend/src/research/research-cockpit.css', 'utf8');
    expect(styles).toContain('@media (max-width: 700px)');
    expect(styles).toContain('body.light .research-cockpit');
    expect(styles).toContain('.hash-value');
    expect(styles).toContain('overflow-wrap: anywhere');
    expect(styles).toContain(':focus-visible');
  });

  it('requests only the five bounded read-only research APIs', async () => {
    mockResearchFetch();
    render(<ResearchCockpit />);
    await screen.findByRole('heading', { name: 'RESEARCH // PILOT A' });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(5));
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url)).sort()).toEqual([
      '/api/research/pilot-a/events?cursor=0&limit=50',
      '/api/research/pilot-a/metrics',
      '/api/research/pilot-a/provenance',
      '/api/research/pilot-a/quarantines?cursor=0&limit=50',
      '/api/research/pilot-a/summary',
    ]);
  });
});
