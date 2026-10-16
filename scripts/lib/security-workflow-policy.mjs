import { isDeepStrictEqual } from 'node:util';
import { parseWorkflowYaml } from './strict-yaml.mjs';

// Explicitly authorized 2026-09-21. Keep security upload rights separate from
// ordinary CI and Roadmap Sync; never accept arbitrary jobs or action inputs.
const checkout = {
  name: 'Check out repository',
  uses: 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
  with: { 'persist-credentials': false },
};
const workflows = {
  '.github/workflows/codeql.yml': {
    name: 'CodeQL',
    on: {
      push: { branches: ['main'] },
      pull_request: { branches: ['main'] },
      schedule: [{ cron: '43 5 * * 1' }],
      workflow_dispatch: {},
    },
    permissions: { contents: 'read' },
    // Main pushes are never cancelled (2026-10-13): cancelled runs uploaded empty Rust analyses.
    concurrency: {
      group: "codeql-${{ github.event_name == 'push' && github.sha || github.ref }}",
      'cancel-in-progress': "${{ github.event_name == 'pull_request' }}",
    },
    jobs: {
      analyze: {
        name: 'CodeQL (${{ matrix.language }})',
        'runs-on': 'ubuntu-24.04',
        'timeout-minutes': 30,
        permissions: { contents: 'read', 'security-events': 'write' },
        strategy: {
          'fail-fast': false,
          'max-parallel': 4,
          matrix: { language: ['actions', 'javascript-typescript', 'python', 'rust'] },
        },
        env: {
          MODE: 'paper',
          TRITON_LIVE_ENABLED: 'false',
          ENTRY_SHADOW_MODE: 'true',
          RUSTUP_TOOLCHAIN: '1.97.1',
        },
        steps: [
          checkout,
          {
            name: 'Prepare pinned Rust toolchain for extraction',
            if: "matrix.language == 'rust' && github.event_name != 'pull_request'",
            run: 'rustup toolchain install 1.97.1 --profile minimal',
          },
          {
            name: 'Initialize CodeQL',
            if: "matrix.language != 'rust' || github.event_name != 'pull_request'",
            uses: 'github/codeql-action/init@1c5b675653bb5c22dbe9b12b556ec555138e09fd',
            with: {
              languages: '${{ matrix.language }}',
              'build-mode': 'none',
              'dependency-caching': false,
            },
          },
          {
            name: 'Analyze and publish security results',
            if: "matrix.language != 'rust' || github.event_name != 'pull_request'",
            uses: 'github/codeql-action/analyze@1c5b675653bb5c22dbe9b12b556ec555138e09fd',
            with: { category: '/language:${{ matrix.language }}' },
          },
        ],
      },
    },
  },
  '.github/workflows/dependency-review.yml': {
    name: 'Dependency review',
    on: { pull_request: { branches: ['main'] } },
    permissions: { contents: 'read' },
    concurrency: { group: 'dependency-review-${{ github.ref }}', 'cancel-in-progress': true },
    jobs: {
      review: {
        name: 'dependency-review',
        'runs-on': 'ubuntu-24.04',
        'timeout-minutes': 5,
        steps: [
          checkout,
          {
            name: 'Review dependency changes',
            uses: 'actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294',
            with: {
              'fail-on-severity': 'moderate',
              'fail-on-scopes': 'runtime, development, unknown',
              'vulnerability-check': true,
              'license-check': false,
              // Owner-approved 2026-10-09 for lab/extractor: Agave-pinned crates, no signing.
              'allow-ghsas': 'GHSA-w5vr-6qhr-36cc, GHSA-x4gp-pqpj-f43q, GHSA-h97m-ww89-6jmq, GHSA-cq8v-f236-94qc',
              'warn-only': false,
              'comment-summary-in-pr': 'never',
              'show-openssf-scorecard': false,
              'retry-on-snapshot-warnings': true,
              'retry-on-snapshot-warnings-timeout': 60,
            },
          },
        ],
      },
    },
  },
  // Owner-approved 2026-10-12: weekly and on-change RustSec, PyPI and npm advisory audit of lab/
  // and the frozen V2 lockfiles (root package-lock, rust/*/Cargo.lock).
  // Read-only; fail rule and the ignore list with reasons live in lab/security/.
  '.github/workflows/dependency-audit.yml': {
    name: 'Dependency audit',
    on: {
      pull_request: {
        branches: ['main'],
        paths: [
          '.github/workflows/dependency-audit.yml',
          'lab/security/**',
          'lab/extractor/Cargo.lock',
          'lab/extractor/Cargo.toml',
          'lab/live/Cargo.lock',
          'lab/live/Cargo.toml',
          'lab/recorder/Cargo.lock',
          'lab/recorder/Cargo.toml',
          'lab/requirements-lab.txt',
          'lab/ui/web/package.json',
          'lab/ui/web/package-lock.json',
          'package.json',
          'package-lock.json',
          'rust/*/Cargo.lock',
          'rust/*/Cargo.toml',
        ],
      },
      schedule: [{ cron: '29 6 * * 1' }],
      workflow_dispatch: {},
    },
    permissions: { contents: 'read' },
    concurrency: { group: 'dependency-audit-${{ github.ref }}', 'cancel-in-progress': true },
    jobs: {
      cargo: {
        name: 'dependency-audit (cargo)',
        'runs-on': 'ubuntu-24.04',
        'timeout-minutes': 30,
        env: { RUSTUP_TOOLCHAIN: '1.97.1' },
        steps: [
          checkout,
          { name: 'Install pinned Rust toolchain', run: 'rustup toolchain install 1.97.1 --profile minimal' },
          { name: 'Install cargo-audit', run: 'cargo install cargo-audit --version 0.22.2 --locked' },
          {
            name: 'Audit lab and frozen V2 Rust lockfiles against RustSec',
            // One invocation, so an ignore entry is only reported stale when no lockfile uses it.
            run: 'python3 lab/security/dependency_audit.py cargo lab/extractor/Cargo.lock lab/live/Cargo.lock lab/recorder/Cargo.lock rust/of1-range-recorder/Cargo.lock rust/of1-bronze-decoder/Cargo.lock rust/of1-parquet-projection/Cargo.lock rust/old-faithful-pump-reducer/Cargo.lock rust/pump-protocol-v2/Cargo.lock',
          },
        ],
      },
      pip: {
        name: 'dependency-audit (pip)',
        'runs-on': 'ubuntu-24.04',
        'timeout-minutes': 10,
        steps: [
          checkout,
          { name: 'Test the audit script', run: 'python3 lab/security/tests/test_dependency_audit.py' },
          {
            name: 'Install pip-audit',
            run: 'python3 -m venv "$RUNNER_TEMP/pip-audit" && "$RUNNER_TEMP/pip-audit/bin/pip" install --disable-pip-version-check pip-audit==2.10.1',
          },
          {
            name: 'Audit the lab Python toolchain',
            run: 'python3 lab/security/dependency_audit.py pip lab/requirements-lab.txt --tool "$RUNNER_TEMP/pip-audit/bin/pip-audit"',
          },
        ],
      },
      npm: {
        name: 'dependency-audit (npm)',
        'runs-on': 'ubuntu-24.04',
        'timeout-minutes': 10,
        steps: [
          checkout,
          {
            name: 'Set up Node.js',
            uses: 'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
            with: { 'node-version': '22.23.2' },
          },
          { name: 'Audit lab/ui/web production dependencies', run: 'python3 lab/security/dependency_audit.py npm lab/ui/web' },
          { name: 'Audit root (V2) dependencies including dev tooling', run: 'python3 lab/security/dependency_audit.py npm . --include-dev' },
        ],
      },
    },
  },
};

export const SECURITY_WORKFLOW_PATHS = Object.freeze(Object.keys(workflows));

export function validateSecurityWorkflowConfiguration(path, text) {
  if (!Object.hasOwn(workflows, path)) return [`unapproved security workflow: ${path}`];
  let actual;
  try {
    actual = parseWorkflowYaml(text);
  } catch (error) {
    return [`${path}: invalid security workflow YAML: ${error.message}`];
  }
  return isDeepStrictEqual(actual, workflows[path])
    ? []
    : [`${path}: must match the reviewed security workflow exactly (triggers, permissions, actions, inputs and limits)`];
}
