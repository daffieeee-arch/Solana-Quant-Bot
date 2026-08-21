import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import YAML from 'yaml';

const ROOT = 'observability/grafana/dashboards';
const EXPECTED = [
  ['srp-00-command-center', '00 — Research Command Center'],
  ['srp-10-pipeline-safety', '10 — Ingest, Network & Pipeline Safety'],
  ['srp-20-provenance-quality', '20 — Provenance, Coverage & Data Quality'],
  ['srp-30-pump-microstructure', '30 — Pump Market Microstructure'],
  ['srp-40-lifecycle-cohorts', '40 — Token Lifecycle & Cohort Analysis'],
  ['srp-50-execution-capacity', '50 — Execution, Liquidity & Capacity'],
  ['srp-60-strategy-oos', '60 — Strategy Lab & OOS Validation'],
  ['srp-70-paper-operations', '70 — Live Paper Trading Operations'],
  ['srp-90-platform-ci', '90 — Platform, ClickHouse & CI'],
  ['srp-99-legacy-forensic', '99 — Legacy / Forensic V1'],
] as const;
const HEADER_FIELDS = ['source class', 'dataset id', 'run id', 'schema version', 'evidence class', 'measurement basis', 'approval status', 'activation verdict', 'transport eligibility', 'execution authorization', 'accepted silver eligibility', 'researchready', 'strategyinputeligible', 'profitability evidence', 'pilot eligible', 'completeness', 'coverage', 'uncertainty'];
const DATASOURCE_POLICY: Record<string, string[]> = {
  'srp-00-command-center': ['DS_PROMETHEUS', 'DS_CLICKHOUSE_OPS', 'DS_GITHUB'],
  'srp-10-pipeline-safety': ['DS_PROMETHEUS'],
  'srp-20-provenance-quality': ['DS_PROMETHEUS', 'DS_CLICKHOUSE_BRONZE', 'DS_CLICKHOUSE_SILVER', 'DS_CLICKHOUSE_OPS'],
  'srp-30-pump-microstructure': ['DS_CLICKHOUSE_SILVER'],
  'srp-40-lifecycle-cohorts': ['DS_CLICKHOUSE_SILVER', 'DS_CLICKHOUSE_GOLD'],
  'srp-50-execution-capacity': ['DS_CLICKHOUSE_SILVER', 'DS_CLICKHOUSE_GOLD'],
  'srp-60-strategy-oos': ['DS_CLICKHOUSE_GOLD'],
  'srp-70-paper-operations': ['DS_PROMETHEUS', 'DS_CLICKHOUSE_OPS'],
  'srp-90-platform-ci': ['DS_PROMETHEUS', 'DS_CLICKHOUSE_OPS', 'DS_GITHUB'],
  'srp-99-legacy-forensic': ['DS_CLICKHOUSE_FORENSIC_V1'],
};

function dashboards() {
  return readdirSync(ROOT).filter((name) => name.endsWith('.json')).sort().map((name) => ({ name, value: JSON.parse(readFileSync(`${ROOT}/${name}`, 'utf8')) as any }));
}

describe('Solana Research Platform Grafana-as-Code suite', () => {
  it('ships all ten stable unique dashboards with the mandatory evidence header', () => {
    const rows = dashboards();
    expect(rows).toHaveLength(10);
    expect(rows.map(({ value }) => [value.uid, value.title])).toEqual(EXPECTED);
    expect(new Set(rows.map(({ value }) => value.uid)).size).toBe(10);
    for (const { value } of rows) {
      expect(value.schemaVersion).toBe(42);
      expect(value.editable).toBe(false);
      expect(value.tags).toContain('solana-research-platform');
      expect(value.xPhase8cContract).toBe('PHASE8C_GRAFANA_DASHBOARD_1');
      const header = value.panels.find((panel: any) => panel.xContract === 'RESEARCH_STATUS_HEADER_1');
      expect(header?.type).toBe('text');
      const content = String(header?.options?.content ?? '').toLowerCase();
      for (const field of HEADER_FIELDS) expect(content).toContain(field);
      expect(content).toContain('unavailable');
    }
  });

  it('uses datasource variables only and never defaults new research queries to forensic_v1', () => {
    for (const { value } of dashboards()) {
      const text = JSON.stringify(value);
      expect(text).not.toContain('bfuyorvcdhb7ka');
      expect(text).not.toMatch(/"uid"\s*:\s*"(?:prometheus|clickhouse|github)-[^$]/i);
      expect(value.templating.list.some((row: any) => row.name === 'observation_mode')).toBe(true);
      expect(value.__inputs.map((row: any) => row.name).sort()).toEqual([...DATASOURCE_POLICY[value.uid]].sort());
      const configuredDatasources = value.templating.list.map((row: any) => row.name).filter((name: string) => name.startsWith('DS_'));
      expect(configuredDatasources.sort()).toEqual([...DATASOURCE_POLICY[value.uid]].sort());
      expect(configuredDatasources).not.toContain('DS_CLICKHOUSE');
      const mode = value.templating.list.find((row: any) => row.name === 'observation_mode');
      expect(mode.current.value).toBe('STATIC_FIXTURE_SNAPSHOT');
      if (value.uid !== 'srp-99-legacy-forensic') {
        expect(text).not.toContain('solana_forensic_v1');
        expect(JSON.stringify(value.panels)).not.toContain('DS_CLICKHOUSE_FORENSIC_V1');
      }
    }
  });

  it('makes unavailable layers explicit and gates every rate panel to bounded replay mode', () => {
    const byUid = new Map(dashboards().map(({ value }) => [value.uid, value]));
    expect(JSON.stringify(byUid.get('srp-50-execution-capacity'))).toContain('UNAVAILABLE — HISTORICAL ACCOUNT STATE AND EXECUTION TELEMETRY NOT PROVEN');
    expect(JSON.stringify(byUid.get('srp-60-strategy-oos'))).toContain('UNAVAILABLE — GOLD DATASET, LABELS AND FINAL OOS SPLIT NOT APPROVED');
    expect(JSON.stringify(byUid.get('srp-70-paper-operations'))).toContain('UNAVAILABLE — NEW LIVE PAPER PIPELINE NOT AUTHORIZED');
    for (const { value } of dashboards()) {
      for (const panel of value.panels) {
        const query = JSON.stringify(panel.targets ?? []);
        if (/\b(?:rate|irate|increase|delta)\s*\(/i.test(query)) {
          expect(panel.xObservationMode).toBe('BOUNDED_REPLAY_STREAM');
          expect(panel.xVisibleWhen).toBe('BOUNDED_REPLAY_STREAM');
        }
      }
    }
  });

  it('keeps Prometheus labels low-cardinality and provisioning immutable', () => {
    const text = dashboards().map(({ value }) => JSON.stringify(value)).join('\n');
    expect(text).not.toMatch(/sum by \([^)]*(?:mint|signature|wallet|account|slot|transaction|event|run_id|error)/i);
    const provisioning = YAML.parse(readFileSync('observability/grafana/provisioning/dashboards.yaml', 'utf8'));
    expect(provisioning.apiVersion).toBe(1);
    expect(provisioning.providers).toHaveLength(1);
    expect(provisioning.providers[0]).toMatchObject({ folder: 'Solana Research Platform', folderUid: 'srp-research-platform', allowUiUpdates: false });
  });

  it('rebinds the Phase-8A dashboard to static totals by default and bounded replay rates only', () => {
    const dashboard = JSON.parse(readFileSync('observability/grafana/pilot-a-event-transport-data-quality.json', 'utf8')) as any;
    const mode = dashboard.templating.list.find((row: any) => row.name === 'observation_mode');
    expect(mode.current.value).toBe('STATIC_FIXTURE_SNAPSHOT');
    for (const panel of dashboard.panels) {
      const query = JSON.stringify(panel.targets ?? []);
      if (/\b(?:rate|irate|increase|delta)\s*\(/i.test(query)) {
        expect(panel.xObservationMode).toBe('BOUNDED_REPLAY_STREAM');
        expect(panel.xVisibleWhen).toBe('BOUNDED_REPLAY_STREAM');
      }
    }
  });
});
