import { isDeepStrictEqual } from 'node:util';
import { parseWorkflowYaml } from './strict-yaml.mjs';

function own(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function isMap(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function normalized(value) {
  return String(value).trim().toLowerCase();
}

function walk(value, path, visitor) {
  visitor(value, path);
  if (Array.isArray(value)) {
    value.forEach((entry, index) => walk(entry, [...path, index], visitor));
  } else if (isMap(value)) {
    for (const [key, entry] of Object.entries(value)) walk(entry, [...path, key], visitor);
  }
}

const CANONICAL_STEPS = [
  {
    name: 'Check out repository',
    uses: 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
    with: { 'fetch-depth': 0, 'persist-credentials': false },
  },
  {
    name: 'Set up Node.js',
    uses: 'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
    with: { 'node-version': '22.23.2', cache: 'npm', 'cache-dependency-path': 'package-lock.json' },
  },
  {
    name: 'Install pinned Rust toolchain',
    run: 'rustup toolchain install 1.97.1 --profile minimal --component clippy,rustfmt',
  },
  {
    name: 'Validate Pump protocol dependencies before fetch',
    run: 'node scripts/assert-pump-protocol-v2-offline.mjs --static',
  },
  {
    name: 'Validate OF1 planner dependencies before fetch',
    run: 'node scripts/assert-of1-planner-offline.mjs --static',
  },
  {
    name: 'Restore scoped Rust build cache',
    uses: 'actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9',
    with: {
      path: '~/.cargo/registry/index\n~/.cargo/registry/cache\n~/.cargo/registry/src\nrust/of1-range-recorder/target\nrust/pump-protocol-v2/target\nrust/old-faithful-pump-reducer/target',
      key: "rust-v1-${{ runner.os }}-${{ runner.arch }}-1.97.1-${{ hashFiles('rust/**/Cargo.lock', 'rust/**/Cargo.toml') }}-${{ github.sha }}",
      'restore-keys': "rust-v1-${{ runner.os }}-${{ runner.arch }}-1.97.1-${{ hashFiles('rust/**/Cargo.lock', 'rust/**/Cargo.toml') }}-",
    },
  },
  {
    name: 'Fetch locked Pump protocol dependencies',
    run: 'cargo +1.97.1 fetch --manifest-path rust/pump-protocol-v2/Cargo.toml --locked',
  },
  {
    name: 'Fetch locked OF1 planner dependencies',
    run: 'cargo +1.97.1 fetch --manifest-path rust/of1-range-recorder/Cargo.toml --locked',
  },
  { name: 'Install locked dependencies', run: 'npm ci' },
  { name: 'Enforce repository and zero-cost policy', run: 'npm run ci:policy' },
  { name: 'Enforce offline research citation gate', run: 'npm run ci:research-citations' },
  {
    name: 'Run policy bypass and critical zero-cost/Pump tests',
    run: 'npx --no-install vitest run tests/ci-policy.test.ts tests/ci-research-citations.test.ts tests/pump-protocol-v2-policy.test.ts tests/pump-silver-event.test.ts tests/zero-cost.test.ts tests/pump-replay.test.ts tests/pump-vertical-slice.test.ts tests/lifecycle-tp-sl.test.ts',
  },
  { name: 'Run complete test suite', run: 'npm test' },
  { name: 'Type-check', run: 'npx --no-install tsc --noEmit' },
  { name: 'Build backend and frontend', run: 'npm run build' },
  {
    name: 'Check Rust reducer formatting',
    run: 'cargo +1.97.1 fmt --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --all -- --check',
  },
  {
    name: 'Check Linux namespace-lock formatting',
    run: 'cargo +1.97.1 fmt --manifest-path rust/linux-kernel-namespace-lock/Cargo.toml -- --check',
  },
  {
    name: 'Check Jetstreamer callback snapshot formatting',
    run: 'cargo +1.97.1 fmt --manifest-path rust/jetstreamer-v0-7-callback-types/Cargo.toml -- --check',
  },
  {
    name: 'Check Solana runtime snapshot formatting',
    run: 'cargo +1.97.1 fmt --manifest-path rust/solana-runtime-v3.1.12-bank-types/Cargo.toml -- --check',
  },
  {
    name: 'Check Pump protocol v2 formatting',
    run: 'cargo +1.97.1 fmt --manifest-path rust/pump-protocol-v2/Cargo.toml --all -- --check',
  },
  {
    name: 'Verify Pump protocol v2 isolated graph, clippy, tests and evidence',
    run: 'node scripts/assert-pump-protocol-v2-offline.mjs --all',
  },
  {
    name: 'Verify OF1 planner isolated graph, formatting, tests and evidence',
    run: 'node scripts/assert-of1-planner-offline.mjs --all',
  },
  {
    name: 'Lint Rust reducer',
    run: 'cargo +1.97.1 clippy --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets -- -D warnings',
  },
  {
    name: 'Test Rust reducer',
    run: 'cargo +1.97.1 test --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets',
  },
  {
    name: 'Build Rust reducer',
    run: 'cargo +1.97.1 build --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked',
  },
  {
    name: 'Check committed pull-request patch integrity',
    if: "github.event_name == 'pull_request'",
    run: 'git diff --check "${{ github.event.pull_request.base.sha }}...${{ github.event.pull_request.head.sha }}"',
  },
  {
    name: 'Check committed push integrity',
    if: "github.event_name != 'pull_request'",
    run: 'git show --check --format= HEAD',
  },
  {
    name: 'Verify tracked files were not modified by checks',
    run: 'test -z "$(git status --porcelain --untracked-files=no)"',
  },
];

const CANONICAL_WORKFLOW = {
  name: 'CI',
  on: {
    push: {
      branches: ['main'],
    },
    pull_request: { branches: ['main'] },
    workflow_call: {},
    workflow_dispatch: {},
  },
  permissions: { contents: 'read' },
  concurrency: {
    group: 'ci-${{ github.workflow }}-${{ github.ref }}',
    'cancel-in-progress': true,
  },
  env: {
    CI: 'true',
    MODE: 'paper',
    TRITON_LIVE_ENABLED: 'false',
    ENTRY_SHADOW_MODE: 'true',
  },
  jobs: {
    quality: {
      name: 'tests-build-zero-cost',
      'runs-on': 'ubuntu-24.04',
      'timeout-minutes': 35,
      steps: CANONICAL_STEPS,
    },
  },
};

export function validateTrackedWorkflowPaths(trackedPaths) {
  const workflowPaths = trackedPaths
    .filter((path) => /^\.github\/workflows\/[^/]+\.ya?ml$/i.test(path))
    .sort();
  const expected = [
    '.github/workflows/ci.yml',
    '.github/workflows/roadmap-sync.yml',
  ];
  return isDeepStrictEqual(workflowPaths, expected)
    ? []
    : [`tracked workflow file set must be exactly ${expected.join(', ')}; found ${workflowPaths.join(', ') || 'none'}`];
}

/** Validate the exact validation-only workflow structure, not text substrings. */
export function validateWorkflowConfiguration(workflow) {
  let root;
  try {
    root = parseWorkflowYaml(workflow);
  } catch (error) {
    return [`workflow YAML validation failed: ${error instanceof Error ? error.message : String(error)}`];
  }

  const errors = [];
  const permissions = root.permissions;
  if (!isMap(permissions) || Object.keys(permissions).length !== 1 || normalized(permissions.contents) !== 'read') {
    errors.push('top-level permissions must be exactly { contents: read }');
  }

  const requiredEnv = CANONICAL_WORKFLOW.env;
  if (!isMap(root.env)) {
    errors.push('workflow env must be a mapping');
  } else {
    for (const [key, expected] of Object.entries(requiredEnv)) {
      if (!own(root.env, key) || normalized(root.env[key]) !== expected) {
        errors.push(`top-level env ${key} must equal ${expected}`);
      }
    }
    if (!isDeepStrictEqual(root.env, requiredEnv)) {
      errors.push('top-level env must contain exactly the canonical zero-cost keys and values');
    }
  }

  const safetyKeys = new Set(Object.keys(requiredEnv));
  walk(root, [], (value, path) => {
    if (!isMap(value)) return;
    for (const key of Object.keys(value)) {
      const keyPath = [...path, key];
      if (key === 'permissions' && path.length > 0) {
        errors.push(`nested permissions override is forbidden at ${keyPath.join('.')}`);
      }
      if (safetyKeys.has(key) && !(path.length === 1 && path[0] === 'env')) {
        errors.push(`safety environment key ${key} may only appear in top-level env (found at ${keyPath.join('.')})`);
      }
    }
  });

  if (!isDeepStrictEqual(root.on, CANONICAL_WORKFLOW.on)) {
    errors.push('workflow triggers must match the canonical push, pull_request, and manual trigger set exactly');
  }

  if (!isMap(root.jobs) || Object.keys(root.jobs).length === 0) {
    errors.push('workflow jobs must be a non-empty mapping');
  }
  if (!isMap(root.jobs) || !isDeepStrictEqual(Object.keys(root.jobs), ['quality'])) {
    errors.push('workflow must define exactly one job named quality');
  }

  let checkoutCount = 0;
  if (isMap(root.jobs)) {
    for (const [jobName, job] of Object.entries(root.jobs)) {
      if (!isMap(job)) {
        errors.push(`job ${jobName} must be a mapping`);
        continue;
      }
      if (own(job, 'uses')) errors.push(`reusable workflow jobs are forbidden: ${jobName}`);
      if (own(job, 'secrets')) errors.push(`job secrets are forbidden: ${jobName}`);
      if (own(job, 'permissions')) errors.push(`job-level permissions overrides are forbidden: ${jobName}`);
      if (own(job, 'container')) errors.push(`job containers are forbidden: ${jobName}`);
      if (own(job, 'services')) errors.push(`job services are forbidden: ${jobName}`);
      if (job['runs-on'] !== 'ubuntu-24.04') {
        errors.push(`job ${jobName} runner must be exactly ubuntu-24.04`);
      }
      if (own(job, 'env') && !isMap(job.env)) errors.push(`job ${jobName} env must be a mapping`);
      if (!Array.isArray(job.steps)) {
        errors.push(`job ${jobName} steps must be a sequence`);
        continue;
      }
      if (job.steps.length !== CANONICAL_STEPS.length) {
        errors.push(`job ${jobName} must contain exactly ${CANONICAL_STEPS.length} canonical steps`);
      }

      job.steps.forEach((step, stepIndex) => {
        if (!isMap(step)) {
          errors.push(`job ${jobName} step ${stepIndex + 1} must be a mapping`);
          return;
        }
        const expectedStep = CANONICAL_STEPS[stepIndex];
        if (own(step, 'env') && !isMap(step.env)) {
          errors.push(`job ${jobName} step ${stepIndex + 1} env must be a mapping`);
        }
        if (own(step, 'uses')) {
          const action = String(step.uses);
          const allowed = new Set([
            'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
            'actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9',
          ]);
          if (!allowed.has(action)) {
            errors.push(`unapproved action ${JSON.stringify(action)} in job ${jobName} step ${stepIndex + 1}`);
          }
          if (action.startsWith('actions/checkout@')) {
            checkoutCount += 1;
            if (!isMap(step.with) || !own(step.with, 'persist-credentials') || normalized(step.with['persist-credentials']) !== 'false') {
              errors.push(`every checkout step must explicitly set persist-credentials: false (job ${jobName} step ${stepIndex + 1})`);
            }
          }
          if (expectedStep?.uses && !isDeepStrictEqual(step, expectedStep)) {
            errors.push(`action input or canonical step mismatch in job ${jobName} step ${stepIndex + 1}`);
          }
        }
        if (own(step, 'run')) {
          const command = String(step.run);
          if (/\b(?:TRITON_LIVE_ENABLED|ENTRY_SHADOW_MODE)\b/i.test(command)) {
            errors.push(`run step may not reference live/shadow safety flags in job ${jobName} step ${stepIndex + 1}`);
          }
          if (/\bGITHUB_ENV\b/i.test(command) || /\bgithub\s*(?:\.\s*env\b|\[\s*['"]env['"]\s*\])/i.test(command)) {
            errors.push(`run step may not write the workflow environment in job ${jobName} step ${stepIndex + 1}`);
          }
          for (const pattern of [
            /\bdocker\b.*(?:\bpush\b|--push\b)/i,
            /\bkubectl\b/i,
            /(?:^|[/\s])ssh(?:\s|$)/i,
            /(?:^|[/\s])scp(?:\s|$)/i,
            /\bcurl\b/i,
            /\bwget\b/i,
            /\brsync\b/i,
          ]) {
            if (pattern.test(command)) {
              errors.push(`deployment command is forbidden in job ${jobName} step ${stepIndex + 1}: ${pattern}`);
            }
          }
          if (!expectedStep?.run || command !== expectedStep.run) {
            errors.push(`unapproved run command in job ${jobName} step ${stepIndex + 1}`);
          }
        }
        if (expectedStep && !isDeepStrictEqual(step, expectedStep)) {
          errors.push(`job ${jobName} step ${stepIndex + 1} must match the canonical workflow exactly`);
        }
      });
    }
  }
  if (checkoutCount !== 1) {
    errors.push(`workflow must contain exactly one actions/checkout step; found ${checkoutCount}`);
  }

  walk(root, [], (value, path) => {
    if (typeof value !== 'string') return;
    if (/\bsecrets\b/i.test(value)) {
      errors.push(`repository or production secret context is forbidden at ${path.join('.')}`);
    }
    if (/\bGITHUB_ENV\b/i.test(value) || /\bgithub\s*(?:\.\s*env\b|\[\s*['"]env['"]\s*\])/i.test(value)) {
      errors.push(`workflow environment command file reference is forbidden at ${path.join('.')}`);
    }
  });

  if (!isDeepStrictEqual(root, CANONICAL_WORKFLOW)) {
    errors.push('workflow must match the canonical validation-only structure exactly');
  }

  return [...new Set(errors)];
}
