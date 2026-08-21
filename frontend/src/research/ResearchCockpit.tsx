import { useEffect, useState } from 'react';
import './research-cockpit.css';

type Summary = {
  schemaVersion: 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1';
  sourceClass: 'SYNTHETIC_FIXTURE_ONLY';
  runId: string;
  observedAt: string;
  evidenceClass: 'SYNTHETIC';
  realData: false;
  acceptedSilver: false;
  researchReady: false;
  activationVerdict: 'HOLD_UNPROVEN_ACTIVATION';
  eligibility: {
    pilotEligible: false;
    transportPilot: { contractReady: true; inputMode: 'SYNTHETIC_FIXTURE_ONLY'; preflightStatus: 'NOT_RUN'; eligible: false; executionAuthorized: false };
    acceptedSilver: { eligible: false; activationVerdict: 'HOLD_UNPROVEN_ACTIVATION'; provenRegistryEntries: 0; totalRegistryEntries: 10 };
    research: { approved: false; researchReady: false; strategyInputEligible: false; profitabilityEvidence: false };
  };
  progress: { requestedSlots: number; reconciledSlots: number; skippedSlots: number; provisionalSlots: number; resolvedSlots: number; currentSlot: number; lastCompletedSlot: number; coveragePercent: number; deterministicRerun: 'MATCH' | 'MISMATCH' };
  dataflow: Record<string, number>;
};

type EventRow = { slot: number; transactionIndex: number; signature: string; instructionLocation: 'top_level' | 'inner'; parentInstructionIndex?: number; instructionIndex: number; observedDiscriminator: string; structuralVariant: string; executionStatus: 'succeeded' | 'failed'; evidenceBadge: 'SHADOW_STRUCTURAL_OBSERVATION' | 'QUARANTINED'; quarantineReason: string | null; rawDetail: Record<string, unknown> };
type QuarantineRow = { slot: number; transactionIndex: number; reason: string; evidenceBadge: 'QUARANTINED' };
type Provenance = { sourceManifestSha256: string; configSha256: string; schemaSha256: string; reducerGitSha: string; inputSha256: string; aggregateOutputSha256: string; rerunSha256: string; approvalStatus: 'CANDIDATE_UNAPPROVED'; completeness: string; uncertainty: 'UNPROVEN_ACTIVATION' };
type Metrics = { bytesRead: number; bytesWritten: number; outputBytes: number; queueDepth: number; peakRssBytes: number; stageDurationsMs: Record<string, number>; walClean: number; checkpointPublished: number; quarantineByReason: Record<string, number> };
type CockpitData = { summary: Summary; events: EventRow[]; quarantines: QuarantineRow[]; provenance: Provenance; metrics: Metrics };

const URLS = [
  '/api/research/pilot-a/summary',
  '/api/research/pilot-a/events?cursor=0&limit=50',
  '/api/research/pilot-a/quarantines?cursor=0&limit=50',
  '/api/research/pilot-a/provenance',
  '/api/research/pilot-a/metrics',
] as const;

async function loadJson(url: string): Promise<any> {
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  if (!response.ok) throw new Error('unavailable');
  return response.json();
}

function decode(responses: any[]): CockpitData {
  const [summary, events, quarantines, provenance, metrics] = responses;
  if (summary?.schemaVersion !== 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1'
    || summary.sourceClass !== 'SYNTHETIC_FIXTURE_ONLY'
    || summary.realData !== false || summary.acceptedSilver !== false || summary.researchReady !== false
    || summary.activationVerdict !== 'HOLD_UNPROVEN_ACTIVATION'
    || summary.eligibility?.transportPilot?.eligible !== false
    || summary.eligibility?.transportPilot?.executionAuthorized !== false
    || summary.eligibility?.transportPilot?.preflightStatus !== 'NOT_RUN'
    || summary.eligibility?.acceptedSilver?.eligible !== false
    || summary.eligibility?.research?.researchReady !== false
    || events?.schemaVersion !== 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1' || !Array.isArray(events.rows)
    || quarantines?.schemaVersion !== 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1' || !Array.isArray(quarantines.rows)
    || provenance?.schemaVersion !== 'PHASE8A_RESEARCH_PROVENANCE_RESPONSE_1'
    || metrics?.schemaVersion !== 'PHASE8A_RESEARCH_METRICS_RESPONSE_1') throw new Error('invalid');
  return { summary, events: events.rows.slice(0, 50), quarantines: quarantines.rows.slice(0, 50), provenance: provenance.provenance, metrics: metrics.metrics };
}

export function ResearchCockpit() {
  const [data, setData] = useState<CockpitData | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all(URLS.map(loadJson)).then((responses) => {
      if (active) { setData(decode(responses)); setUnavailable(false); }
    }).catch(() => { if (active) { setData(null); setUnavailable(true); } });
    return () => { active = false; };
  }, []);

  if (!data) return <main className="research-cockpit research-unavailable"><header><span className="research-kicker">READ ONLY · FIXTURE ONLY</span><h1>RESEARCH // PILOT A</h1></header>{unavailable ? <section role="status"><strong>RESEARCH OUTPUT UNAVAILABLE</strong><p>No fixture replay provider is configured. No zero-valued or inferred evidence is shown.</p></section> : <section role="status"><strong>LOADING FIXTURE EVIDENCE…</strong></section>}</main>;

  const { summary, events, quarantines, provenance, metrics } = data;
  const progress = summary.progress;
  const flow = summary.dataflow;
  return <main className="research-cockpit">
    <header className="research-header">
      <div><span className="research-kicker">EVENT / TRANSPORT EVIDENCE · READ ONLY</span><h1>RESEARCH // PILOT A</h1><p>Offline Bronze fixture replay. Structural evidence only; never strategy input.</p></div>
      <div className="research-badges"><Badge tone="synthetic">{summary.sourceClass}</Badge><Badge tone="hold">{summary.activationVerdict}</Badge></div>
    </header>

    <section className="eligibility-strip" aria-label="Research eligibility status">
      <Status label="SOURCE CLASS" value={summary.sourceClass} />
      <Status label="TRANSPORT CONTRACT" value={summary.eligibility.transportPilot.contractReady ? 'READY' : 'NOT READY'} />
      <Status label="TRANSPORT ELIGIBLE" value="FALSE" />
      <Status label="EXECUTION AUTHORIZED" value="FALSE" />
      <Status label="ACCEPTED SILVER" value="FALSE" />
      <Status label="RESEARCH READY" value="FALSE" />
      <Status label="PREFLIGHT" value="NOT RUN" />
    </section>

    <section className="research-grid progress-grid">
      <Panel title="PROGRESS / COVERAGE" eyebrow={`${progress.coveragePercent.toFixed(1)}% RECONCILED`}>
        <div className="research-metrics">
          <NumberCell label="REQUESTED SLOTS" value={progress.requestedSlots} />
          <NumberCell label="RECONCILED" value={progress.reconciledSlots} />
          <NumberCell label="SKIPPED / PROVISIONAL" value={`${progress.skippedSlots} / ${progress.provisionalSlots}`} />
          <NumberCell label="RESOLVED" value={progress.resolvedSlots} />
          <NumberCell label="CURRENT SLOT" value={progress.currentSlot} />
          <NumberCell label="LAST COMPLETED" value={progress.lastCompletedSlot} />
          <NumberCell label="DETERMINISTIC RERUN" value={progress.deterministicRerun} />
        </div>
      </Panel>
      <Panel title="DATAFLOW" eyebrow="BOUNDED COUNTERS">
        <div className="research-metrics dataflow-metrics">{Object.entries(flow).map(([label, value]) => <NumberCell key={label} label={humanize(label).toUpperCase()} value={value} />)}</div>
      </Panel>
    </section>

    <section className="research-grid evidence-grid">
      <Panel title="EVENT ACTIVITY" eyebrow={`${events.length} RETAINED ROWS`}>
        <div className="event-table" role="table" aria-label="Observed instruction evidence">
          {events.length ? events.map((row, index) => <article className="research-event-row" role="row" key={`${row.slot}-${row.transactionIndex}-${row.instructionIndex}-${index}`}>
            <div><span>Observed instruction</span><strong>{row.structuralVariant.toUpperCase()}</strong><small>{row.executionStatus.toUpperCase()} · {row.instructionLocation === 'inner' ? `INNER ${row.parentInstructionIndex}:${row.instructionIndex}` : `TOP ${row.instructionIndex}`}</small></div>
            <div><span>SLOT / TX</span><strong>{row.slot} / {row.transactionIndex}</strong><small className="hash-value">{row.signature}</small></div>
            <div><span>DISCRIMINATOR</span><strong className="hash-value">{row.observedDiscriminator}</strong><small>Structural candidate · not accepted Silver</small></div>
            <Badge tone={row.evidenceBadge === 'QUARANTINED' ? 'quarantine' : 'shadow'}>{humanize(row.evidenceBadge).toUpperCase()}</Badge>
          </article>) : <Empty label="No observed instruction rows in this fixture snapshot" />}
        </div>
      </Panel>
      <Panel title="QUARANTINED EVIDENCE" eyebrow={`${quarantines.length} ROWS`}>
        <div className="quarantine-feed">{quarantines.length ? quarantines.map((row, index) => <article key={`${row.slot}-${row.transactionIndex}-${index}`}><Badge tone="quarantine">QUARANTINED</Badge><strong>{humanize(row.reason).toUpperCase()}</strong><span>SLOT {row.slot} · TX {row.transactionIndex}</span></article>) : <Empty label="No quarantined evidence in this fixture snapshot" />}</div>
      </Panel>
    </section>

    <section className="research-grid provenance-grid">
      <Panel title="PROVENANCE" eyebrow={provenance.approvalStatus}>
        <div className="provenance-list">
          <Hash label="SOURCE MANIFEST" value={provenance.sourceManifestSha256} />
          <Hash label="CONFIG / SCHEMA" value={`${provenance.configSha256} / ${provenance.schemaSha256}`} />
          <Hash label="REDUCER GIT" value={provenance.reducerGitSha} />
          <Hash label="INPUT" value={provenance.inputSha256} />
          <Hash label="AGGREGATE OUTPUT" value={provenance.aggregateOutputSha256} />
          <Hash label="RERUN" value={provenance.rerunSha256} />
          <Hash label="COMPLETENESS / UNCERTAINTY" value={`${provenance.completeness} / ${provenance.uncertainty}`} />
        </div>
      </Panel>
      <Panel title="RESOURCE / OBSERVABILITY" eyebrow="SIDE CHANNEL ONLY">
        <div className="research-metrics">
          <NumberCell label="BYTES READ" value={metrics.bytesRead} />
          <NumberCell label="BYTES WRITTEN" value={metrics.bytesWritten} />
          <NumberCell label="OUTPUT BYTES" value={metrics.outputBytes} />
          <NumberCell label="QUEUE DEPTH" value={metrics.queueDepth} />
          <NumberCell label="PEAK RSS" value={formatBytes(metrics.peakRssBytes)} />
          <Status label="WAL" value={metrics.walClean ? 'CLEAN' : 'UNAVAILABLE'} />
          <Status label="CHECKPOINT" value={metrics.checkpointPublished ? 'PUBLISHED' : 'UNAVAILABLE'} />
        </div>
        <p className="research-note">Metrics cannot affect canonical bytes, ordering, hashes, acceptance, quarantine, or the fixture replay verdict.</p>
      </Panel>
    </section>
  </main>;
}

function Panel({ title, eyebrow, children }: { title: string; eyebrow: string; children: React.ReactNode }) { return <section className="research-panel"><header><span>{eyebrow}</span><h2>{title}</h2></header>{children}</section>; }
function Badge({ children, tone }: { children: React.ReactNode; tone: 'synthetic' | 'hold' | 'shadow' | 'quarantine' }) { return <span className={`evidence-badge ${tone}`}>{children}</span>; }
function Status({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{label === 'PREFLIGHT' ? `${label} · ${value}` : `${label} · ${value}`}</strong></div>; }
function NumberCell({ label, value }: { label: string; value: string | number }) { return <div><span>{label}</span><strong>{value}</strong></div>; }
function Hash({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong className="hash-value">{value}</strong></div>; }
function Empty({ label }: { label: string }) { return <div className="research-empty">— {label}</div>; }
function humanize(value: string) { return value.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replaceAll('_', ' '); }
function formatBytes(value: number) { return `${(value / (1024 * 1024)).toFixed(1)} MiB`; }
