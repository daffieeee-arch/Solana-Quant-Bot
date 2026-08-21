import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const CURRENT_DOCS = ['README.md', 'docs/ARCHITECTURE.md', 'docs/CURRENT_STATE.md', 'docs/HANDOFF.md', 'docs/KNOWN_ISSUES.md', 'docs/PUMP_OFFLINE_RESEARCH.md', 'docs/PHASE8A_BRONZE_RUNNER_RESEARCH_COCKPIT.md'];

describe('Phase 8C current documentation consistency', () => {
  it('records merged Phase 8A and unapplied Phase 8C without authorization drift', () => {
    const joined = CURRENT_DOCS.map((path) => `${path}\n${readFileSync(path, 'utf8')}`).join('\n');
    expect(joined).toContain('404019d3562afd3c10c1f65da141ab4e3f5ba1fc');
    expect(joined).toContain('Phase 8C');
    expect(joined).toContain('cockpit-only');
    expect(joined).toContain('LEGACY_FORENSIC_V1');
    expect(joined).toContain('HOLD_UNPROVEN_ACTIVATION');
    expect(joined).toContain('SYNTHETIC_FIXTURE_ONLY');
    expect(joined).toContain('researchReady: false');
    expect(joined).toMatch(/not deployed|NOT DEPLOYED|unapplied/i);
  });

  it('keeps every named current document linked to the Phase 8C source of truth', () => {
    for (const path of CURRENT_DOCS) {
      const text = readFileSync(path, 'utf8');
      expect(text, path).toMatch(/PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE|Phase 8C/);
    }
  });
});
