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
    concurrency: { group: 'codeql-${{ github.ref }}', 'cancel-in-progress': true },
    jobs: {
      analyze: {
        name: 'CodeQL (${{ matrix.language }})',
        'runs-on': 'ubuntu-24.04',
        'timeout-minutes': 30,
        permissions: { contents: 'read', 'security-events': 'write' },
        strategy: {
          'fail-fast': false,
          'max-parallel': 2,
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
            if: "matrix.language == 'rust'",
            run: 'rustup toolchain install 1.97.1 --profile minimal',
          },
          {
            name: 'Initialize CodeQL',
            uses: 'github/codeql-action/init@1c5b675653bb5c22dbe9b12b556ec555138e09fd',
            with: {
              languages: '${{ matrix.language }}',
              'build-mode': 'none',
              'dependency-caching': false,
            },
          },
          {
            name: 'Analyze and publish security results',
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
