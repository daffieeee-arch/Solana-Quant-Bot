import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const REQUIRED_FIELDS = ['dataset_id', 'run_id', 'source_class', 'schema_version', 'parser_version', 'registry_version', 'evidence_class', 'approval_status', 'research_ready', 'as_of_slot', 'available_at', 'coverage_status', 'content_hash'];

describe('Phase 8C ClickHouse domain architecture and operations runbook', () => {
  it('defines five unapplied separated domains with forensic excluded from defaults', () => {
    const plan = JSON.parse(readFileSync('observability/clickhouse/domain-plan.json', 'utf8')) as any;
    expect(plan.applied).toBe(false);
    expect(plan.ddlAuthorized).toBe(false);
    expect(plan.domains.map((row: any) => row.name)).toEqual(['solana_bronze', 'solana_silver', 'solana_gold', 'solana_ops', 'solana_forensic_v1']);
    const forensic = plan.domains.find((row: any) => row.name === 'solana_forensic_v1');
    expect(forensic).toMatchObject({ classification: 'LEGACY_FORENSIC_V1', dataUse: 'FORENSIC_ONLY', defaultResearchDatasource: false, strategyGroundTruth: false });
    for (const domain of plan.domains.filter((row: any) => row.name !== 'solana_forensic_v1')) {
      for (const field of REQUIRED_FIELDS) expect(domain.contextFields).toContain(field);
    }
    expect(JSON.stringify(plan)).not.toMatch(/CREATE\s+(?:DATABASE|TABLE)|OPTIMIZE|INSERT\s+INTO/i);
  });

  it('documents archive-first migration without implying ClickHouse or Grafana mutation', () => {
    const domain = readFileSync('docs/data/CLICKHOUSE_BRONZE_SILVER_GOLD_PLAN.md', 'utf8');
    const runbook = readFileSync('docs/operations/GRAFANA_LEGACY_ARCHIVE_AND_RESEARCH_PLATFORM_MIGRATION.md', 'utf8');
    for (const name of ['solana_bronze', 'solana_silver', 'solana_gold', 'solana_ops', 'solana_forensic_v1']) expect(domain).toContain(name);
    expect(domain).toContain('FORENSIC_ONLY');
    expect(domain).toContain('No DDL');
    expect(runbook).toContain('Grafana 13.2.0');
    expect(runbook).toContain('ClickHouse is STOPPED');
    expect(runbook).toContain('LEGACY_FORENSIC_V1');
    expect(runbook).toContain('snapshot/clone');
    expect(runbook).toContain('Prometheus and Loki are not installed');
    expect(runbook).toContain('GitHub datasource');
    expect(runbook).toContain('Elasticsearch datasource');
  });
});
