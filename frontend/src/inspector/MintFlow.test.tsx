// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixtureMintFlow } from '../../../tests/fixtures/mint-flow';
import { InspectionView, MintInspector } from './MintInspector';
import { MintFlowView } from './MintFlow';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const region = () => screen.getByRole('region', { name: 'Brongebonden volume en flow' });
const full = () => within(region()).getByRole('table', { name: 'Volledig dossier · onafhankelijk van replaypositie' });
const prefix = () => within(region()).getByRole('table', { name: /^Tot en met geselecteerd/ });

describe('source-bound Python mint flow presentation', () => {
  it('separates full totals from atomic prefixes, source classes, hashes and unknown quote units', () => {
    const { inspection, flow } = fixtureMintFlow(), before = JSON.stringify(flow);
    render(<InspectionView inspection={inspection} flow={flow} />);
    const all = full().querySelector('[data-flow-group="ALL"]')!, first = prefix().querySelector('[data-flow-group="ALL"]')!;
    expect(all.textContent).toContain('11'); expect(all.textContent).toContain('21');
    expect(first.textContent).toContain('-3'); expect(first.textContent).toContain('1 / 1');
    expect(within(full()).getByText('Pilot · RESEARCH_SAMPLING')).toBeTruthy();
    expect(within(full()).getByText('Context · ENGINEERING_VALIDATION_ONLY')).toBeTruthy();
    expect(within(region()).getAllByText('UNKNOWN').length).toBeGreaterThan(0);
    const table = within(region()).getByRole('table', { name: 'Exacte cumulatieve flow en bronfeiten' });
    const firstRow = table.querySelectorAll('tbody tr')[0];
    for (const h of flow.report.packages[0].fact_hashes) expect(firstRow.textContent).toContain(h);
    expect(table.querySelectorAll('tbody tr')).toHaveLength(3);
    expect(table.querySelectorAll('tbody tr')[2].textContent).toContain('Geen toegelaten feit · marktactiviteit onbekend');
    expect(JSON.stringify(flow)).toBe(before);
  });
  it.each(['point', 'table', 'group'])('%s selection pauses playback and reset restores the first complete prefix without changing full totals', method => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { inspection, flow } = fixtureMintFlow(); render(<InspectionView inspection={inspection} flow={flow} />);
    const totalText = full().textContent;
    fireEvent.click(screen.getByRole('button', { name: 'Afspelen' }));
    act(() => { vi.advanceTimersByTime(1999); });
    if (method === 'point') fireEvent.keyDown(within(region()).getByRole('button', { name: /^Flow package 2 ·/ }), { key: 'Enter' });
    else if (method === 'table') fireEvent.click(within(region()).getByRole('button', { name: 'Flow package 02' }));
    else fireEvent.click(within(region()).getByRole('button', { name: 'Context · ENGINEERING_VALIDATION_ONLY' }));
    const chosen = method === 'group' ? '1 / 3' : '2 / 3';
    expect(screen.getByLabelText('Geselecteerde replaypositie').textContent).toBe(chosen);
    act(() => { vi.advanceTimersByTime(10000); });
    expect(screen.getByLabelText('Geselecteerde replaypositie').textContent).toBe(chosen);
    fireEvent.click(screen.getByRole('button', { name: 'Opnieuw beginnen' }));
    expect(screen.getByLabelText('Geselecteerde replaypositie').textContent).toBe('1 / 3');
    expect(prefix().querySelector('[data-flow-group="ALL"]')?.textContent).toContain('-3');
    expect(full().textContent).toBe(totalText);
    // Dispatch jsdom's 0ms <details> toggle on the newly mounted parent,
    // as in the existing replay tests; future playback timers remain tested.
    act(() => { vi.advanceTimersByTime(0); });
    expect(vi.getTimerCount()).toBe(0);
    expect(within(screen.getByRole('article', { name: 'Geselecteerd atomair package' })).getByText(inspection.timeline.transactions[0].package_id)).toBeTruthy();
  });
  it('keeps negative integers beyond Number precision as source text while drawing only coordinates', () => {
    const { flow } = fixtureMintFlow();
    flow.report.packages[0].cumulative.ALL.net_token_raw = '-18446744073709551615';
    render(<MintFlowView flow={flow} selectedIndex={0} onSelect={vi.fn()} />);
    expect(within(region()).getAllByText('-18446744073709551615').length).toBeGreaterThan(1);
    const button = within(region()).getByRole('button', { name: /Flow package 1 .* -18446744073709551615 raw/ });
    expect(Number.isFinite(Number(button.querySelector('circle')?.getAttribute('cy')))).toBe(true);
  });
  it('rejects a wrong snapshot response as UNAVAILABLE without hiding the original mint dossier', async () => {
    const { inspection, flow } = fixtureMintFlow(); flow.report.inputs.timeline = '0'.repeat(64);
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url === '/api/inspection' ? inspection : flow), { status: 200 })));
    render(<MintInspector />);
    expect(await screen.findByText(/UNAVAILABLE · Geen geldige samenvatting/)).toBeTruthy();
    expect(screen.getByLabelText('Geverifieerde selectietellingen')).toBeTruthy();
    expect(within(region()).queryByRole('table')).toBeNull();
  });
});
