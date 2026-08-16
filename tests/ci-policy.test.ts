import { describe, expect, it } from 'vitest';
import * as policyModule from '../scripts/ci-repository-policy.mjs';

const { parseWorkflowYaml, validateWorkflowConfiguration } = policyModule;
const workflowFileSetErrors = (paths: string[]) => {
  const validator = (policyModule as unknown as {
    validateTrackedWorkflowPaths?: (trackedPaths: string[]) => string[];
  }).validateTrackedWorkflowPaths;
  expect(validator).toBeTypeOf('function');
  return validator?.(paths).join('\n') ?? 'tracked workflow validator missing';
};

const SAFE_WORKFLOW = `name: CI

on:
  push:
    branches:
      - main
      - 'chore/**'
      - 'feature/**'
      - 'phase2/**'
      - 'ci/**'
      - 'cursor/**'
  pull_request:
    branches:
      - main
  workflow_dispatch: {}

permissions:
  contents: read

concurrency:
  group: ci-\${{ github.workflow }}-\${{ github.ref }}
  cancel-in-progress: true

env:
  CI: 'true'
  MODE: paper
  TRITON_LIVE_ENABLED: 'false'
  ENTRY_SHADOW_MODE: 'true'

jobs:
  quality:
    name: tests-build-zero-cost
    runs-on: ubuntu-24.04
    timeout-minutes: 25
    steps:
      - name: Check out repository
        uses: actions/checkout@v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Set up Node.js
        uses: actions/setup-node@v7.0.0
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: package-lock.json
      - name: Install locked dependencies
        run: npm ci
      - name: Enforce repository and zero-cost policy
        run: npm run ci:policy
      - name: Run policy bypass and critical zero-cost/Pump tests
        run: npx vitest run tests/ci-policy.test.ts tests/zero-cost.test.ts tests/pump-replay.test.ts tests/pump-vertical-slice.test.ts tests/lifecycle-tp-sl.test.ts
      - name: Run complete test suite
        run: npm test
      - name: Type-check
        run: npx tsc --noEmit
      - name: Build backend and frontend
        run: npm run build
      - name: Check committed pull-request patch integrity
        if: github.event_name == 'pull_request'
        run: git diff --check "\${{ github.event.pull_request.base.sha }}...\${{ github.event.pull_request.head.sha }}"
      - name: Check committed push integrity
        if: github.event_name != 'pull_request'
        run: git show --check --format= HEAD
      - name: Verify tracked files were not modified by checks
        run: test -z "$(git status --porcelain --untracked-files=no)"
`;

const errors = (workflow: string) => validateWorkflowConfiguration(workflow).join('\n');
const addStep = (body: string) => SAFE_WORKFLOW.replace(
  '      - name: Verify tracked files were not modified by checks',
  `${body}\n      - name: Verify tracked files were not modified by checks`,
);

describe('semantic CI workflow policy', () => {
  it('accepts the canonical read-only zero-cost workflow', () => {
    expect(validateWorkflowConfiguration(SAFE_WORKFLOW)).toEqual([]);
    expect(parseWorkflowYaml(SAFE_WORKFLOW).jobs.quality.steps).toHaveLength(11);
  });

  it('rejects the original comment bypass and quoted/unquoted top-level live values', () => {
    const commentBypass = SAFE_WORKFLOW.replace(
      "  TRITON_LIVE_ENABLED: 'false'",
      "  # TRITON_LIVE_ENABLED: 'false'\n  TRITON_LIVE_ENABLED: 'true'",
    );
    expect(errors(commentBypass)).toMatch(/TRITON_LIVE_ENABLED.*false/i);
    for (const value of ['true', "'true'", '"true"']) {
      expect(errors(SAFE_WORKFLOW.replace("'false'", value))).toMatch(/TRITON_LIVE_ENABLED.*false/i);
    }
  });

  it('rejects duplicate block and inline mapping keys', () => {
    const duplicateBlock = SAFE_WORKFLOW.replace(
      "  TRITON_LIVE_ENABLED: 'false'",
      "  TRITON_LIVE_ENABLED: 'false'\n  TRITON_LIVE_ENABLED: false",
    );
    expect(errors(duplicateBlock)).toMatch(/duplicate mapping key/i);

    const duplicateInline = SAFE_WORKFLOW.replace(
      '  quality:',
      "  quality:\n    env: { TRITON_LIVE_ENABLED: false, 'TRITON_LIVE_ENABLED': true }",
    );
    expect(errors(duplicateInline)).toMatch(/duplicate mapping key/i);
  });

  it('rejects a job-level inline live override', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '  quality:',
      '  quality:\n    env: { TRITON_LIVE_ENABLED: true }',
    );
    expect(errors(unsafe)).toMatch(/safety environment key TRITON_LIVE_ENABLED/i);
  });

  it('rejects a step-level inline live override', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '      - name: Install locked dependencies',
      '      - name: Install locked dependencies\n        env: { TRITON_LIVE_ENABLED: true }',
    );
    expect(errors(unsafe)).toMatch(/safety environment key TRITON_LIVE_ENABLED/i);
  });

  it('normalizes quoted keys and rejects the same live override', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '  quality:',
      "  quality:\n    env:\n      'TRITON_LIVE_ENABLED': 'true'",
    );
    expect(errors(unsafe)).toMatch(/safety environment key TRITON_LIVE_ENABLED/i);
  });

  it('rejects job-level write-all permissions', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '  quality:',
      '  quality:\n    permissions: write-all',
    );
    expect(errors(unsafe)).toMatch(/permissions override/i);
  });

  it('rejects job-level inline permission maps even when top-level remains read-only', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '  quality:',
      '  quality:\n    permissions: { contents: read, id-token: write }',
    );
    expect(errors(unsafe)).toMatch(/permissions override/i);
  });

  it('checks every checkout and rejects a second checkout with default persistence', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '      - name: Install locked dependencies',
      '      - uses: actions/checkout@v7.0.1\n      - name: Install locked dependencies',
    );
    expect(errors(unsafe)).toMatch(/every checkout step.*persist-credentials: false/i);
    expect(errors(unsafe)).toMatch(/exactly one actions\/checkout step/i);
  });

  it('rejects a second checkout even when both disable credential persistence', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '      - name: Install locked dependencies',
      '      - uses: actions/checkout@v7.0.1\n        with: { persist-credentials: false }\n      - name: Install locked dependencies',
    );
    expect(errors(unsafe)).toMatch(/exactly one actions\/checkout step/i);
  });

  it('rejects safety overrides hidden in another nested map such as container.env', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '    runs-on: ubuntu-24.04',
      '    runs-on: ubuntu-24.04\n    container: { image: node:22, env: { TRITON_LIVE_ENABLED: true } }',
    );
    expect(errors(unsafe)).toMatch(/safety environment key TRITON_LIVE_ENABLED/i);
  });

  it('fails closed on unsupported YAML features instead of approximating them', () => {
    const unsafe = SAFE_WORKFLOW.replace('        run: npm test', '        run: |\n          npm test');
    expect(errors(unsafe)).toMatch(/block scalars are not supported/i);
  });

  it('preserves attached hash characters and rejects every block-scalar header variant', () => {
    const attachedHash = SAFE_WORKFLOW.replace(
      '        run: npm ci',
      '        run: echo harmless#; /usr/bin/ssh deploy@example.invalid',
    );
    expect(errors(attachedHash)).toMatch(/unapproved run command|deployment command/i);

    for (const header of ['|2', '>2', '|2-', '>+2']) {
      const blockScalar = SAFE_WORKFLOW.replace('        run: npm ci', `        run: ${header}`);
      expect(errors(blockScalar)).toMatch(/block scalars are not supported|canonical workflow/i);
    }
  });

  it('rejects index and whole-context secret expressions', () => {
    const indexedSecret = SAFE_WORKFLOW.replace(
      '          persist-credentials: false',
      "          persist-credentials: false\n          token: ${{ secrets['DEPLOY_TOKEN'] }}",
    );
    const wholeContext = addStep(
      '      - name: expose secrets\n        env:\n          ALL_SECRETS: ${{ toJSON(secrets) }}\n        run: npm test',
    );
    expect(errors(indexedSecret)).toMatch(/secret/i);
    expect(errors(wholeContext)).toMatch(/secret/i);
  });

  it('rejects github.env mutation and expression-based live unlocks', () => {
    const githubEnv = addStep(
      '      - name: mutate environment\n        run: echo "ENTRY_SHADOW_MODE=false" >> "${{ github.env }}"',
    );
    const liveExpression = addStep(
      "      - name: enable live\n        run: TRITON_LIVE_ENABLED=${{ 'true' }} node dist/main.js",
    );
    expect(errors(githubEnv)).toMatch(/environment|canonical workflow/i);
    expect(errors(liveExpression)).toMatch(/live Triton|canonical workflow/i);
  });

  it('rejects non-canonical jobs, runners, containers, and services', () => {
    const variants = [
      SAFE_WORKFLOW.replace('    runs-on: ubuntu-24.04', '    runs-on: self-hosted'),
      SAFE_WORKFLOW.replace(
        '    runs-on: ubuntu-24.04',
        '    runs-on: ubuntu-24.04\n    container: attacker.example/ci:latest',
      ),
      SAFE_WORKFLOW.replace(
        '    runs-on: ubuntu-24.04',
        '    runs-on: ubuntu-24.04\n    services:\n      control:\n        image: attacker.example/control:latest',
      ),
      `${SAFE_WORKFLOW}\n  mutate:\n    runs-on: self-hosted\n    steps:\n      - run: curl -X POST https://internal.invalid/mutate\n`,
    ];
    for (const variant of variants) {
      expect(errors(variant)).toMatch(/canonical|runner|container|services|exactly one job/i);
    }
  });

  it('rejects arbitrary run commands and action input drift', () => {
    const variants = [
      addStep('      - name: deploy\n        run: /usr/bin/ssh deploy@example.invalid'),
      addStep('      - name: deploy\n        run: docker buildx build --push .'),
      SAFE_WORKFLOW.replace("          node-version: '22'", "          node-version: '23'"),
    ];
    for (const variant of variants) {
      expect(errors(variant)).toMatch(/unapproved run command|canonical workflow|action input/i);
    }
  });

  it('rejects trigger drift and any second tracked workflow', () => {
    const pullRequestTarget = SAFE_WORKFLOW.replace('  pull_request:', '  pull_request_target:');
    expect(errors(pullRequestTarget)).toMatch(/trigger|canonical workflow/i);
    expect(workflowFileSetErrors(['.github/workflows/ci.yml'])).toBe('');
    expect(workflowFileSetErrors([
      '.github/workflows/ci.yml',
      '.github/workflows/deploy.yml',
    ])).toMatch(/workflow file set/i);
  });
});
