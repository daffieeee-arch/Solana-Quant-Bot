// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixtureInspection } from '../../../tests/fixtures/mint-inspector';
import { InspectionView, MintInspector, StateNotice, type ViewState } from './MintInspector';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe('mint inspection view', () => {
  it('navigates complete atomic packages by buttons and keyboard without changing data', () => {
    const data = fixtureInspection(), before = JSON.stringify(data); render(<InspectionView inspection={data} />);
    const region = screen.getByRole('region', { name: 'Mintpackages in chainvolgorde' });
    const selected = () => screen.getByRole('article', { name: 'Geselecteerd atomair package' });
    expect(within(selected()).getByText(data.timeline.transactions[0].package_id)).toBeTruthy();
    expect(screen.getByRole('button', { name: '← Vorige' }).hasAttribute('disabled')).toBe(true);
    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(within(selected()).getByText(data.timeline.transactions[1].package_id)).toBeTruthy();
    expect(within(selected()).getByText('Geen toegelaten mintfeit in dit package. Dit bewijst geen nulactiviteit.')).toBeTruthy();
    fireEvent.keyDown(region, { key: 'End' });
    expect(within(selected()).getByText('Mislukte transactie · geen Silver-feiten')).toBeTruthy();
    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(within(selected()).getByText(data.timeline.transactions[2].package_id)).toBeTruthy();
    fireEvent.keyDown(region, { key: 'Home' });
    fireEvent.click(screen.getByRole('button', { name: 'Volgende →' }));
    fireEvent.click(screen.getByRole('button', { name: '← Vorige' }));
    expect(within(selected()).getByText(data.timeline.transactions[0].package_id)).toBeTruthy();
    expect(JSON.stringify(data)).toBe(before);
  });
  it('renders exact integers, nulls, separate classes and provenance using escaped text', () => {
    const data = fixtureInspection(); data.timeline.transactions[0].signatures = ['<img src=x onerror=alert(1)>'];
    render(<InspectionView inspection={data} />);
    expect(screen.getAllByText('18446744073709551615').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Niet ingevuld (null)').length).toBeGreaterThan(0);
    expect(within(screen.getByRole('article', { name: 'Geselecteerd atomair package' })).getByText('Pilot · RESEARCH_SAMPLING')).toBeTruthy();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy(); expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('Research Ready: false')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'timeline.json' }).getAttribute('href')).toBe('/evidence/timeline.json');
  });
  it.each<ViewState>(['READY', 'STALE', 'GAP', 'REPLAYING', 'UNAVAILABLE', 'UNPROVEN', 'QUARANTINED'])('keeps the %s state explicit (display fixture)', state => {
    render(<StateNotice state={state} />); expect(screen.getByRole('status').textContent).toContain(state);
  });
  it('shows unavailability without zero counts after a failed read', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('fictional internal path')));
    render(<MintInspector />);
    expect(await screen.findByText('UNAVAILABLE')).toBeTruthy();
    expect(screen.queryByLabelText('Geverifieerde selectietellingen')).toBeNull();
    expect(document.body.textContent).not.toContain('fictional internal path');
  });
});
