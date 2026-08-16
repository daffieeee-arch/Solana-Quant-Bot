import { describe, expect, it } from 'vitest';
import { activeYamlScalarValues, validateWorkflowConfiguration } from '../scripts/ci-repository-policy.mjs';

const SAFE_WORKFLOW = `
permissions:
  contents: read

env:
  CI: 'true'
  MODE: paper
  TRITON_LIVE_ENABLED: 'false'
  ENTRY_SHADOW_MODE: 'true'

steps:
  - uses: actions/checkout@v7.0.1
    with:
      persist-credentials: false
`;

describe('CI workflow policy parser', () => {
  it('accepts exactly one active, safe zero-cost configuration', () => {
    expect(activeYamlScalarValues(SAFE_WORKFLOW, 'TRITON_LIVE_ENABLED')).toEqual(['false']);
    expect(validateWorkflowConfiguration(SAFE_WORKFLOW)).toEqual([]);
  });

  it('rejects the comment-bypass pattern from the independent review', () => {
    const bypass = SAFE_WORKFLOW.replace(
      "  TRITON_LIVE_ENABLED: 'false'",
      "  # TRITON_LIVE_ENABLED: 'false'\n  TRITON_LIVE_ENABLED: 'true'",
    );
    expect(validateWorkflowConfiguration(bypass).join('\n')).toMatch(/TRITON_LIVE_ENABLED.*false/i);
  });

  it('rejects quoted and unquoted live unlocks', () => {
    for (const value of ['true', "'true'", '"true"']) {
      const unsafe = SAFE_WORKFLOW.replace("'false'", value);
      expect(validateWorkflowConfiguration(unsafe).join('\n')).toMatch(/TRITON_LIVE_ENABLED.*false/i);
    }
  });

  it('rejects duplicate active assignments even when both appear safe', () => {
    const duplicate = SAFE_WORKFLOW.replace(
      "  TRITON_LIVE_ENABLED: 'false'",
      "  TRITON_LIVE_ENABLED: 'false'\n  TRITON_LIVE_ENABLED: false",
    );
    expect(validateWorkflowConfiguration(duplicate).join('\n')).toMatch(/exactly one active TRITON_LIVE_ENABLED/i);
  });
});
