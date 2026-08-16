import { describe, expect, it } from 'vitest';
import { parseWorkflowYaml, validateWorkflowConfiguration } from '../scripts/ci-repository-policy.mjs';

const SAFE_WORKFLOW = `
name: CI
on:
  pull_request:
    branches:
      - main
  workflow_dispatch: {}
permissions:
  contents: read
env:
  CI: 'true'
  MODE: paper
  TRITON_LIVE_ENABLED: 'false'
  ENTRY_SHADOW_MODE: 'true'
jobs:
  quality:
    runs-on: ubuntu-24.04
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
      - name: Run tests
        run: npm test
`;

const errors = (workflow: string) => validateWorkflowConfiguration(workflow).join('\n');

describe('semantic CI workflow policy', () => {
  it('accepts the canonical read-only zero-cost workflow', () => {
    expect(validateWorkflowConfiguration(SAFE_WORKFLOW)).toEqual([]);
    expect(parseWorkflowYaml(SAFE_WORKFLOW).jobs.quality.steps).toHaveLength(3);
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
      '      - name: Run tests',
      '      - name: Run tests\n        env: { TRITON_LIVE_ENABLED: true }',
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
      '      - name: Run tests',
      '      - uses: actions/checkout@v7.0.1\n      - name: Run tests',
    );
    expect(errors(unsafe)).toMatch(/every checkout step.*persist-credentials: false/i);
    expect(errors(unsafe)).toMatch(/exactly one actions\/checkout step/i);
  });

  it('rejects a second checkout even when both disable credential persistence', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '      - name: Run tests',
      '      - uses: actions/checkout@v7.0.1\n        with: { persist-credentials: false }\n      - name: Run tests',
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
});
