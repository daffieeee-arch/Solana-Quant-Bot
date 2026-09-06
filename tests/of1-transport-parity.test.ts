import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const manifest = JSON.parse(readFileSync('schemas/acquisition/of1/transport-parity.json', 'utf8'));
const oldManifest = JSON.parse(readFileSync(manifest.predecessor_manifest, 'utf8'));

describe('OF1 transport invariant salvage', () => {
  it('binds exactly four reviewed paths and all 26 original cases without rewriting the earlier salvage record', () => {
    expect(manifest.schema).toBe('OF1_B4_TRANSPORT_PARITY_1');
    expect(manifest.baseline_commit).toBe('afc5a80c6f12a38b6e699a2e72384fb8b1124f66');
    expect(manifest.evidence).toBe('Fixture');
    expect(manifest.removed_paths).toHaveLength(4);
    expect(new Set(manifest.removed_paths.map((entry: { path: string }) => entry.path)).size).toBe(4);
    for (const entry of manifest.removed_paths) {
      expect(oldManifest.retained_files).toContainEqual({ path: entry.path, sha256: entry.sha256 });
      expect(entry.disposition).toBe('REPLACED_PARITY_VERIFIED');
      expect(existsSync(entry.path), entry.path).toBe(false);
    }
    expect(manifest.old_test_cases).toHaveLength(26);
    const unique = new Set(manifest.old_test_cases.map((test: { file: string; name: string }) => `${test.file}:${test.name}`));
    expect(unique.size).toBe(26);
    expect(manifest.old_test_cases.filter((test: { file: string }) => test.file === 'tests/b4a-offline-range-recorder.test.ts')).toHaveLength(10);
    expect(manifest.old_test_cases.filter((test: { file: string }) => test.file === 'tests/b4-offline-remainder.test.ts')).toHaveLength(16);
    expect(manifest.old_test_cases.every((test: { reason: string; replacement: unknown[] }) => test.reason.length > 0 && test.replacement.length > 0)).toBe(true);
  });

  it('names concrete executable replacement tests rather than unresolved parity claims', () => {
    const targets = [
      ...manifest.old_test_cases.flatMap((test: { replacement: string[][] }) => test.replacement),
      ...manifest.new_regressions,
    ];
    for (const [group, name] of targets) {
      const path = manifest.replacement_files[group];
      expect(typeof path, `${group}/${name}`).toBe('string');
      const source = readFileSync(path, 'utf8');
      if (path.endsWith('.rs')) {
        expect(source, `${path}:${name}`).toContain(`fn ${name}(`);
        expect(source, `${path}:${name}`).toMatch(new RegExp(`#\\[test\\]\\s+(?:#\\[[^\\n]+\\]\\s+)*fn ${name}\\(`));
      } else {
        expect(source, `${path}:${name}`).toContain(`it('${name}'`);
      }
    }
    for (const path of manifest.retained_out_of_scope) expect(existsSync(path), path).toBe(true);
  });

  it('retires invalid whole-CAR, HTTP200 and caller-coverage assumptions without claiming authentic evidence', () => {
    expect(manifest.old_test_cases.filter((test: { classification: string }) => test.classification === 'INVARIANT_WITH_INVALID_EXPECTATION')).toHaveLength(5);
    expect(manifest.not_claimed).toEqual([
      'authentic source bytes', 'historical activation', 'root-to-slot membership',
      'verified CID', 'Research Ready', 'complete B4', 'live lease authorization',
    ]);
    const scripts = JSON.stringify(JSON.parse(readFileSync('package.json', 'utf8')).scripts);
    expect(scripts).not.toMatch(/b4a-offline-range-recorder|b4-offline-remainder/);
    const visit = (directory: string) => {
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) visit(path);
        else if (/\.(?:mjs|[jt]sx?)$/.test(path)) {
          const source = readFileSync(path, 'utf8');
          expect(source, path).not.toMatch(/(?:from\s*|import\s*\(|require\s*\()\s*['"][^'"]*\/(?:b4a-offline-range-recorder|b4-offline-remainder)\.mjs['"]/);
        }
      }
    };
    for (const directory of ['scripts', 'src', 'tests']) visit(directory);
  });

  it('keeps the inherited draft unapproved and offline', () => {
    const plan = JSON.parse(readFileSync('docs/research/B4_OFFLINE_REMAINDER_DRAFT_PLAN.json', 'utf8'));
    expect(plan.approved).toBe(false);
    expect(plan.networkEnabled).toBe(false);
    expect(plan.host).toBeNull();
    expect(plan.slot_range).toBeNull();
    expect(plan.purpose).toBe('ENGINEERING_VALIDATION_ONLY');
  });
});
