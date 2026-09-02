import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  PHASE8A_ALLOWED_METRIC_LABELS,
  buildPhase8AObservability,
  renderPhase8APrometheus,
  safeBuildPhase8AObservability,
  validatePhase8AMetricLabels,
} from '../src/research/phase8a-observability.js';
import { phase8aCockpitSnapshot } from './fixtures/phase8a/research-output.js';

describe('Phase 8A pure observability adapter', () => {
  it('builds deterministic bounded JSON and Prometheus snapshots without changing canonical input', () => {
    const before = JSON.stringify(phase8aCockpitSnapshot);
    const first = buildPhase8AObservability(phase8aCockpitSnapshot);
    const second = buildPhase8AObservability(structuredClone(phase8aCockpitSnapshot));
    expect(second).toEqual(first);
    expect(JSON.stringify(phase8aCockpitSnapshot)).toBe(before);
    expect(first).toMatchObject({
      schemaVersion: 'PHASE8A_METRICS_SNAPSHOT_1',
      source: 'synthetic_fixture',
      runMode: 'fixture_replay',
      transportEligible: 0,
      acceptedSilverEligible: 0,
      deterministicRerunMatch: 1,
    });
    const text = renderPhase8APrometheus(first);
    expect(text).toContain('phase8a_requested_slots{');
    expect(text).toContain('phase8a_transport_eligible{');
    expect(text).not.toContain(phase8aCockpitSnapshot.runId);
    expect(text).not.toContain(phase8aCockpitSnapshot.events[0].signature);
    expect(renderPhase8APrometheus(second)).toBe(text);
  });

  it('closes metric labels and rejects high-cardinality names or unknown values', () => {
    expect(PHASE8A_ALLOWED_METRIC_LABELS).toEqual([
      'stage', 'result', 'quarantine_reason', 'source', 'schema_version', 'run_mode', 'evidence_class',
    ]);
    expect(validatePhase8AMetricLabels({ stage: 'bronze', result: 'success' })).toEqual([]);
    expect(validatePhase8AMetricLabels({ signature: 'abc' })).toContain('forbidden_label:signature');
    expect(validatePhase8AMetricLabels({ slot: '422506000' })).toContain('forbidden_label:slot');
    expect(validatePhase8AMetricLabels({ stage: 'arbitrary' })).toContain('unknown_label_value:stage');
    expect(validatePhase8AMetricLabels({ quarantine_reason: 'free text' })).toContain('unknown_label_value:quarantine_reason');
  });

  it('keeps runner verdict independent when observability fails', () => {
    const canonicalVerdict = { status: 'FIXTURE_REPLAY_COMPLETE', canonicalHash: 'a'.repeat(64) } as const;
    const result = safeBuildPhase8AObservability({ ...phase8aCockpitSnapshot, resources: null } as never);
    expect(result).toEqual({ schemaVersion: 'PHASE8A_OBSERVABILITY_RESULT_1', status: 'UNAVAILABLE', snapshot: null });
    expect(canonicalVerdict).toEqual({ status: 'FIXTURE_REPLAY_COMPLETE', canonicalHash: 'a'.repeat(64) });
  });

  it('ships an importable datasource-neutral Grafana contract without high-cardinality queries', () => {
    const dashboard = JSON.parse(readFileSync('observability/grafana/pilot-a-event-transport-data-quality.json', 'utf8')) as any;
    expect(dashboard.schemaVersion).toBe(42);
    expect(dashboard.xPhase8aContract).toBe('PHASE8A_GRAFANA_DASHBOARD_CONTRACT_1');
    expect(dashboard.title).toBe('Pilot A — Event Transport & Data Quality');
    expect(JSON.stringify(dashboard)).not.toMatch(/datasourceUid|"uid"\s*:\s*"[^$]/i);
    expect(JSON.stringify(dashboard)).not.toMatch(/mint|signature|wallet|pubkey|transaction_id|run_id/i);
    const observationMode = dashboard.templating.list.find((row: any) => row.name === 'observation_mode');
    expect(observationMode?.current?.value).toBe('STATIC_FIXTURE_SNAPSHOT');
    const titles = dashboard.panels.map((panel: any) => panel.title);
    for (const panel of dashboard.panels) {
      expect(panel.datasource).toEqual({ type: 'prometheus', uid: '${DS_PROMETHEUS}' });
      for (const target of panel.targets) expect(target.datasource).toEqual({ type: 'prometheus', uid: '${DS_PROMETHEUS}' });
      if (/\b(?:rate|irate|increase|delta)\s*\(/i.test(JSON.stringify(panel.targets ?? []))) {
        expect(panel.xObservationMode).toBe('BOUNDED_REPLAY_STREAM');
        expect(panel.xVisibleWhen).toBe('BOUNDED_REPLAY_STREAM');
      }
    }
    expect(titles).toEqual(expect.arrayContaining([
      'Requested vs reconciled slots', '[REPLAY ONLY] Slots / sec', '[REPLAY ONLY] Transactions / sec', '[REPLAY ONLY] Pump candidates / sec',
      '[REPLAY ONLY] Unknown & quarantine rate', 'Quarantine reasons', 'Exact retries & conflicts', 'Bytes read / written',
      'Queue depth', 'Peak RSS', 'WAL & checkpoint results', 'Deterministic rerun',
      'Transport eligibility', 'Accepted Silver eligibility',
    ]));
  });
});
