import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseWorkflowYaml } from '../scripts/lib/strict-yaml.mjs';
import { validateSecurityWorkflowConfiguration } from '../scripts/lib/security-workflow-policy.mjs';

const paths = ['.github/workflows/codeql.yml', '.github/workflows/dependency-review.yml', '.github/workflows/dependency-audit.yml'];
const check = (path: string, text: string) => validateSecurityWorkflowConfiguration(path, text);

describe('security workflow privilege and coverage boundaries', () => {
  it.each(paths)('accepts the reviewed workflow %s', (path) => {
    expect(check(path, readFileSync(path, 'utf8'))).toEqual([]);
  });

  it.each(paths)('rejects privilege, execution and failure bypasses in %s', (path) => {
    const original = readFileSync(path, 'utf8');
    const mutations = [
      original.replace('contents: read', 'contents: write'),
      original.replace('pull_request:', 'pull_request_target:'),
      original.replace('runs-on: ubuntu-24.04', 'runs-on: self-hosted'),
      original.replace('persist-credentials: false', 'persist-credentials: true'),
      original.replace('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1', 'actions/checkout@main'),
      original.replace('    steps:', '    continue-on-error: true\n    steps:'),
      original.replace('    steps:', '    if: false\n    steps:'),
      original + '\n      - run: node src/main.ts\n',
      original.replace('    steps:', '    env:\n      PROJECT_TOKEN: ${{ secrets.PROJECT_TOKEN }}\n    steps:'),
      original.replace('contents: read', 'contents: read\n  contents: write'),
    ];
    for (const mutation of mutations) {
      expect(mutation).not.toBe(original);
      expect(check(path, mutation).length).toBeGreaterThan(0);
    }
  });

  it('confines the security-events write permission to the CodeQL job', () => {
    const original = readFileSync(paths[0], 'utf8');
    expect(check(paths[0], original.replace('security-events: write', 'packages: write')).length).toBeGreaterThan(0);
    expect(check(paths[0], original.replace('security-events: write', 'security-events: write\n      pull-requests: write')).length).toBeGreaterThan(0);
    const dependency = parseWorkflowYaml(readFileSync(paths[1], 'utf8')) as any;
    expect(dependency.permissions).toEqual({ contents: 'read' });
    expect(dependency.jobs.review.permissions).toBeUndefined();
  });

  it('rejects lost language coverage and unbounded or application-building scans', () => {
    const original = readFileSync(paths[0], 'utf8');
    for (const mutation of [
      original.replace(', rust]', ']'),
      original.replace('timeout-minutes: 30', 'timeout-minutes: 360'),
      original.replace('build-mode: none', 'build-mode: autobuild'),
      original.replace('max-parallel: 4', 'max-parallel: 8'),
      original.replace("if: matrix.language != 'rust' || github.event_name != 'pull_request'", "if: matrix.language != 'rust'"),
      original.replace("'43 5 * * 1'", "'* * * * *'"),
    ]) expect(check(paths[0], mutation).length).toBeGreaterThan(0);
  });

  it('rejects weakened vulnerability review, ignored scopes and hidden advisories', () => {
    const original = readFileSync(paths[1], 'utf8');
    for (const mutation of [
      original.replace('fail-on-severity: moderate', 'fail-on-severity: critical'),
      original.replace('runtime, development, unknown', 'runtime'),
      original.replace('warn-only: false', 'warn-only: true'),
      original.replace('vulnerability-check: true', 'vulnerability-check: false'),
      original.replace('comment-summary-in-pr: never', 'comment-summary-in-pr: always'),
      original.replace('GHSA-cq8v-f236-94qc', 'GHSA-cq8v-f236-94qc, GHSA-example-test-only'),
      original.replace(/\n *allow-ghsas:[^\n]*/, '\n          allow-ghsas: GHSA-example-test-only'),
    ]) expect(check(paths[1], mutation).length).toBeGreaterThan(0);
  });

  it('keeps the dependency audit read-only, pinned, scheduled and failing', () => {
    const original = readFileSync(paths[2], 'utf8');
    const audit = parseWorkflowYaml(original) as any;
    expect(audit.permissions).toEqual({ contents: 'read' });
    for (const job of Object.values(audit.jobs) as any[]) expect(job.permissions).toBeUndefined();
    for (const mutation of [
      original.replace(' lab/recorder/Cargo.lock', ''),
      original.replace(' lab/recorder/Cargo.lock', ' --optional lab/recorder/Cargo.lock'),
      original.replace(' --version 0.22.2 --locked', ''),
      original.replace('pip-audit==2.10.1', 'pip-audit'),
      original.replace('npm lab/ui/web', 'npm lab/ui/web || true'),
      original.replace(' rust/of1-range-recorder/Cargo.lock', ''),
      original.replace('npm . --include-dev', 'npm .'),
      original.replace('      - package-lock.json\n', ''),
      original.replace('      - rust/*/Cargo.lock\n', ''),
      original.replace('      - lab/ui/web/package-lock.json\n', ''),
      original.replace("'29 6 * * 1'", "'29 6 1 1 *'"),
      original.replace('  workflow_dispatch: {}\n', ''),
      original.replace('      - name: Test the audit script\n        run: python3 lab/security/tests/test_dependency_audit.py\n', ''),
    ]) {
      expect(mutation).not.toBe(original);
      expect(check(paths[2], mutation).length).toBeGreaterThan(0);
    }
  });

  it('does not authorize a security workflow under an arbitrary filename', () => {
    expect(check('.github/workflows/deploy.yml', readFileSync(paths[0], 'utf8'))).toEqual([
      'unapproved security workflow: .github/workflows/deploy.yml',
    ]);
  });
});
