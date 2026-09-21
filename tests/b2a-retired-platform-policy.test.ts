import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';
import { validatePackageScripts, validateTrackedRepositoryPaths } from '../scripts/ci-repository-policy.mjs';
import { validateTrackedWorkflowPaths } from '../scripts/lib/workflow-policy.mjs';

const MANIFEST_PATH = 'roadmap/b2a-invariant-salvage-manifest.json';
const RESOLVED_REMOVAL_DISPOSITIONS = new Set(['MIGRATED', 'DUPLICATE_PROVEN', 'REQUIREMENT_RETIRED']);

function trackedPaths(): string[] {
  return execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean).sort();
}

function patternMatches(pathPattern: string, path: string): boolean {
  return pathPattern.endsWith('/**')
    ? path === pathPattern.slice(0, -3) || path.startsWith(pathPattern.slice(0, -2))
    : pathPattern.includes('*')
      ? new RegExp(`^${pathPattern.split('*').map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*')}$`).test(path)
      : path === pathPattern;
}

describe('B2A retired-platform policy', () => {
  it('retains only reviewed validation, roadmap and security workflows', () => {
    const tracked = trackedPaths();
    expect(validateTrackedWorkflowPaths(tracked)).toEqual([]);
    expect(tracked.filter((path) => path.startsWith('.github/workflows/'))).toEqual([
      '.github/workflows/ci.yml',
      '.github/workflows/codeql.yml',
      '.github/workflows/dependency-review.yml',
      '.github/workflows/roadmap-sync.yml',
    ]);
  });

  it('keeps every manifest removal resolved and absent from the tracked tree', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8')) as any;
    const tracked = trackedPaths();
    expect(manifest.schemaVersion).toBe('B2A_INVARIANT_SALVAGE_MANIFEST_1');
    expect(manifest.rollback).toMatchObject({
      tag: 'v1-paper-platform-final',
      peeledCommit: 'f870621f5df76b935ce828fa9205fb9ff7504f67',
    });
    for (const group of [...manifest.removalGroups, ...manifest.testClassifications]) {
      expect(group.subsystem, JSON.stringify(group.paths)).toBeTruthy();
      expect(group.reachabilityBeforeRemoval, JSON.stringify(group.paths)).toBeTruthy();
      expect(group.uniqueInvariantOrEvidence, JSON.stringify(group.paths)).toBeDefined();
      expect(Array.isArray(group.destination), JSON.stringify(group.paths)).toBe(true);
      expect(RESOLVED_REMOVAL_DISPOSITIONS.has(group.disposition), JSON.stringify(group.paths)).toBe(true);
      expect(group.reviewEvidence, JSON.stringify(group.paths)).toBeTruthy();
      expect(group.rollbackTag, JSON.stringify(group.paths)).toBe('v1-paper-platform-final');
      for (const pathPattern of group.paths) {
        expect(tracked.filter((path) => patternMatches(pathPattern, path)), pathPattern).toEqual([]);
      }
    }
  });

  it('keeps every explicit retention represented in the tracked tree', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8')) as any;
    const tracked = trackedPaths();
    for (const group of manifest.retainedGroups) {
      expect(group.disposition).toBe('RETAINED');
      for (const pathPattern of group.paths) {
        expect(tracked.some((path) => patternMatches(pathPattern, path)), pathPattern).toBe(true);
      }
    }
  });

  it('has no package launch path to the frozen scanner and no in-tree legacy archive', () => {
    const tracked = trackedPaths();
    const pkg = JSON.parse(readFileSync('package.json', 'utf8')) as { scripts: Record<string, string> };
    expect(validatePackageScripts(pkg.scripts)).toEqual([]);
    expect(validateTrackedRepositoryPaths(tracked)).toEqual([]);
    expect(existsSync('legacy')).toBe(false);
  });

  it('keeps the optional frozen frontend preview on loopback', () => {
    const config = readFileSync('frontend/vite.config.ts', 'utf8');
    expect(config).toContain("host: '127.0.0.1'");
    expect(config).toContain("target: 'http://127.0.0.1:3000'");
    expect(config).not.toMatch(/192\.168\.|100\.79\.|0\.0\.0\.0/);
  });
});
