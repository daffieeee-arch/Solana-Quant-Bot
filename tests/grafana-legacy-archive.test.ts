import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const ROOT = 'observability/grafana/legacy-v1';
const UIDS = ['memecoin-contra', 'memecoin-contra-strategy'] as const;

function read(path: string) { return JSON.parse(readFileSync(path, 'utf8')) as any; }

describe('LEGACY_FORENSIC_V1 Grafana archive', () => {
  it('archives both known dashboards with immutable forensic nonclaims', () => {
    const manifest = read(`${ROOT}/legacy-v1-manifest.json`);
    expect(manifest.classification).toBe('LEGACY_FORENSIC_V1');
    expect(manifest.dashboards.map((row: any) => row.originalUid).sort()).toEqual([...UIDS].sort());
    for (const uid of UIDS) {
      const archive = read(`${ROOT}/${uid}.json`);
      expect(archive).toMatchObject({
        schemaVersion: 'PHASE8C_LEGACY_GRAFANA_ARCHIVE_1',
        classification: 'LEGACY_FORENSIC_V1',
        dataUse: 'FORENSIC_ONLY',
        researchStatus: 'NOT_RESEARCH_READY',
        strategyStatus: 'NOT_STRATEGY_EVIDENCE',
        profitabilityStatus: 'NOT_FOR_PROFITABILITY_CLAIMS',
        originalUid: uid,
        originalDatasourceBinding: { type: 'grafana-clickhouse-datasource', uid: 'bfuyorvcdhb7ka' },
      });
      expect(archive.warning).toMatch(/not.*active strategy quer/i);
      expect(archive.sourceResponseSha256).toMatch(/^[0-9a-f]{64}$/);
      expect(archive.exportSha256).toMatch(/^[0-9a-f]{64}$/);
      expect(archive.exportHashDomain).toBe('CANONICAL_EMBEDDED_DASHBOARD_JSON');
      expect(archive.sourceHashDomain).toBe('RAW_GRAFANA_GET_RESPONSE_BYTES');
      expect(archive.inspection).toEqual({ secretFindings: 0, piiFindings: 0 });
      expect(archive.dashboard.uid).toBe(uid);
      const text = JSON.stringify(archive);
      expect(text).not.toMatch(/password|authorization|cookie|api[_-]?key|github_pat_|ghp_|sk-/i);
    }
  });

  it('pins queries and table dependencies without placing legacy exports in the active dashboard suite', () => {
    const dependencies = read(`${ROOT}/query-and-table-dependencies.json`);
    expect(dependencies.classification).toBe('LEGACY_FORENSIC_V1');
    expect(dependencies.dashboards.map((row: any) => row.uid).sort()).toEqual([...UIDS].sort());
    expect(dependencies.tables).toEqual(expect.arrayContaining(['memecoin_swaps', 'live_bot_decisions', 'contra_expectancy', 'age_windows', 'vol_clusters', 'score_grid']));
    for (const dashboard of dependencies.dashboards) {
      const archive = read(`${ROOT}/${dashboard.uid}.json`);
      const expectedQueries = [
        ...archive.dashboard.panels.flatMap((panel: any) => (panel.targets ?? []).map((target: any) => target.rawSql).filter(Boolean)),
        ...(archive.dashboard.templating?.list ?? []).map((variable: any) => variable.query).filter((query: unknown) => typeof query === 'string'),
      ];
      expect(dashboard.queries).toHaveLength(expectedQueries.length);
      expect(dashboard.queries.map((row: any) => row.query).sort()).toEqual(expectedQueries.sort());
      for (const row of dashboard.queries) expect(row.querySha256).toMatch(/^[0-9a-f]{64}$/);
    }
    expect(readFileSync(`${ROOT}/README.md`, 'utf8')).toContain('NOT_STRATEGY_EVIDENCE');
    expect(() => readFileSync('observability/grafana/dashboards/memecoin-contra.json')).toThrow();
  });
});
