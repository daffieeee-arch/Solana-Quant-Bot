import { parseWorkflowYaml } from './strict-yaml.mjs';
import { own } from './strict-yaml-flow.mjs';

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

/** Validate effective workflow structure, not raw text substrings. */
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

  const requiredEnv = {
    CI: 'true',
    MODE: 'paper',
    TRITON_LIVE_ENABLED: 'false',
    ENTRY_SHADOW_MODE: 'true',
  };
  if (!isMap(root.env)) {
    errors.push('workflow env must be a mapping');
  } else {
    for (const [key, expected] of Object.entries(requiredEnv)) {
      if (!own(root.env, key) || normalized(root.env[key]) !== expected) {
        errors.push(`top-level env ${key} must equal ${expected}`);
      }
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

  if (!isMap(root.jobs) || Object.keys(root.jobs).length === 0) {
    errors.push('workflow jobs must be a non-empty mapping');
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
      if (own(job, 'env') && !isMap(job.env)) errors.push(`job ${jobName} env must be a mapping`);
      if (!Array.isArray(job.steps)) {
        errors.push(`job ${jobName} steps must be a sequence`);
        continue;
      }

      job.steps.forEach((step, stepIndex) => {
        if (!isMap(step)) {
          errors.push(`job ${jobName} step ${stepIndex + 1} must be a mapping`);
          return;
        }
        if (own(step, 'env') && !isMap(step.env)) {
          errors.push(`job ${jobName} step ${stepIndex + 1} env must be a mapping`);
        }
        if (own(step, 'uses')) {
          const action = String(step.uses);
          const allowed = new Set(['actions/checkout@v7.0.1', 'actions/setup-node@v7.0.0']);
          if (!allowed.has(action)) {
            errors.push(`unapproved action ${JSON.stringify(action)} in job ${jobName} step ${stepIndex + 1}`);
          }
          if (action.startsWith('actions/checkout@')) {
            checkoutCount += 1;
            if (!isMap(step.with) || !own(step.with, 'persist-credentials') || normalized(step.with['persist-credentials']) !== 'false') {
              errors.push(`every checkout step must explicitly set persist-credentials: false (job ${jobName} step ${stepIndex + 1})`);
            }
          }
        }
        if (own(step, 'run')) {
          const command = String(step.run);
          if (/\bTRITON_LIVE_ENABLED\s*=\s*["']?true\b/i.test(command)) {
            errors.push(`run step enables live Triton in job ${jobName} step ${stepIndex + 1}`);
          }
          if (/GITHUB_ENV/i.test(command)) {
            errors.push(`run step may not write workflow environment through GITHUB_ENV (job ${jobName} step ${stepIndex + 1})`);
          }
          for (const pattern of [/\bdocker\s+push\b/i, /\bkubectl\b/i, /(^|\s)ssh(\s|$)/i, /(^|\s)scp(\s|$)/i]) {
            if (pattern.test(command)) {
              errors.push(`deployment command is forbidden in job ${jobName} step ${stepIndex + 1}: ${pattern}`);
            }
          }
        }
      });
    }
  }
  if (checkoutCount !== 1) {
    errors.push(`workflow must contain exactly one actions/checkout step; found ${checkoutCount}`);
  }

  walk(root, [], (value, path) => {
    if (typeof value === 'string' && /\bsecrets\.[A-Za-z0-9_]+/i.test(value)) {
      errors.push(`repository or production secret reference is forbidden at ${path.join('.')}`);
    }
  });

  return [...new Set(errors)];
}
