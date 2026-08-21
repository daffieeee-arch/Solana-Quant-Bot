import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

describe('cockpit-only build contract', () => {
  it('exposes a separate production start and frontend build', () => {
    const pkg = JSON.parse(readFileSync('package.json', 'utf8')) as any;
    expect(pkg.scripts['start:cockpit']).toBe('node dist/cockpit-main.js');
    expect(pkg.scripts['build:cockpit']).toBe('vite build --config frontend/vite.cockpit.config.ts');
    expect(pkg.scripts['verify:cockpit-runtime']).toBe('node scripts/assert-cockpit-runtime-inert.mjs');
    expect(pkg.scripts.build.indexOf('npm run build:cockpit')).toBeLessThan(pkg.scripts.build.indexOf('npm run verify:cockpit-runtime'));
    expect(readFileSync('scripts/assert-cockpit-runtime-inert.mjs', 'utf8')).toContain('writeCockpitOutboundDenyFilter');
    expect(pkg.scripts.build).toContain('npm run build:cockpit');
  });

  it('builds an isolated Research Cockpit entry without the Paper application', () => {
    const serverSource = readFileSync('src/cockpit-server.ts', 'utf8');
    const providerSource = readFileSync('src/research/phase8a-research-provider.ts', 'utf8');
    expect(serverSource).not.toContain("from './dashboard.js'");
    expect(providerSource).not.toContain("from '../dashboard.js'");
    const html = readFileSync('frontend/cockpit.html', 'utf8');
    const entry = readFileSync('frontend/src/cockpit-entry.tsx', 'utf8');
    const config = readFileSync('frontend/vite.cockpit.config.ts', 'utf8');
    expect(html).toContain('/src/cockpit-entry.tsx');
    expect(entry).toContain('ResearchCockpit');
    expect(entry).not.toMatch(/from ['"].\/App/);
    expect(entry).not.toContain('Paper Monitor');
    expect(config).toContain("outDir: 'cockpit-dist'");
    expect(config).toContain('cockpit.html');
  });
});
