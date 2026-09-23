// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixturePilotQuality } from '../../../tests/fixtures/pilot-quality';
import { fixtureInspection } from '../../../tests/fixtures/mint-inspector';
import { PilotQualityView } from './PilotQuality';
import { InspectionView } from './MintInspector';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe('pilot quality view', () => {
  it('separates pilot counts, processing layers, mint scope and unavailable evidence', () => {
    const q = fixturePilotQuality(); q.manifest.counts.quarantined = null;
    render(<PilotQualityView quality={q} mintCounts={{ transactions: '15', silver_facts: '5', balance_observations: '58' }} />);
    const stats = screen.getByLabelText('Geverifieerde pilottellingen');
    for (const n of ['3', '3224', '223', '7']) expect(within(stats).getByText(n)).toBeTruthy();
    expect(screen.getByText(/15 packages, 5 feiten en 58 balansobservaties/)).toBeTruthy();
    expect(screen.getByText('QUARANTINED · Bronze').nextElementSibling?.textContent).toBe('UNAVAILABLE');
    expect(screen.getByText('Totaal afgewezen Pump-instructies').nextElementSibling?.textContent).toBe('UNAVAILABLE');
    expect(screen.getByText('RESEARCH_SAMPLING')).toBeTruthy(); expect(screen.getByText('Research Ready: false')).toBeTruthy();
    expect(screen.getAllByText(/18446744073709551615/).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'pilot-manifest.json' }).getAttribute('href')).toBe('/evidence/pilot-manifest.json');
  });
  it('preserves package position while switching views and never fills a failed pilot read with zeros', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('fictional private error')));
    render(<InspectionView inspection={fixtureInspection()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Volgende →' }));
    fireEvent.click(screen.getByRole('button', { name: 'Datakwaliteit pilot' }));
    expect(await screen.findByText(/UNAVAILABLE · Geen geldig geregistreerd/)).toBeTruthy();
    expect(screen.queryByLabelText('Geverifieerde pilottellingen')).toBeNull();
    expect(document.body.textContent).not.toContain('fictional private error');
    fireEvent.click(screen.getByRole('button', { name: 'Mintdossier' }));
    expect(screen.getByText('2 / 3')).toBeTruthy();
  });
});
