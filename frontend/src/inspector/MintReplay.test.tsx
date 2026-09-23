// @vitest-environment jsdom
import React, { StrictMode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixtureInspection } from '../../../tests/fixtures/mint-inspector';
import { InspectionView } from './MintInspector';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const tick = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });
// jsdom queues a 0ms toggle event when an open <details> mounts. Dispatch it
// before counting pending playback timers; do not clear or run future timers.
const pendingTimers = () => { tick(0); return vi.getTimerCount(); };
const position = () => screen.getByLabelText('Geselecteerde replaypositie').textContent;
const timeline = () => screen.getByRole('list', { name: 'Volledige packagetijdlijn' });
const selected = () => screen.getByRole('article', { name: 'Geselecteerd atomair package' });

describe('atomic mint presentation replay', () => {
  it('advances one whole package per step in StrictMode, preserves totals and stops at the failed final package', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); const data = fixtureInspection(), before = JSON.stringify(data);
    render(<StrictMode><InspectionView inspection={data} /></StrictMode>);
    const totals = screen.getByLabelText('Geverifieerde selectietellingen').textContent;
    click('Afspelen'); tick(1999); expect(position()).toBe('1 / 3'); tick(1);
    expect(position()).toBe('2 / 3'); expect(within(selected()).getByText(data.timeline.transactions[1].package_id)).toBeTruthy();
    expect(within(selected()).getByText('Geen toegelaten mintfeit in dit package. Dit bewijst geen nulactiviteit.')).toBeTruthy();
    tick(2000); expect(position()).toBe('3 / 3');
    expect(within(selected()).getByText('Mislukte transactie · geen Silver-feiten')).toBeTruthy();
    expect(screen.getByText('Einde fragment')).toBeTruthy(); expect(screen.getByRole('button', { name: 'Afspelen' }).hasAttribute('disabled')).toBe(true);
    expect(pendingTimers()).toBe(0); tick(20000); expect(position()).toBe('3 / 3');
    expect(screen.getByLabelText('Geverifieerde selectietellingen').textContent).toBe(totals);
    expect(JSON.stringify(data)).toBe(before);
  });
  it('cancels the pending step on pause and gives resume a fresh full interval', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); render(<InspectionView inspection={fixtureInspection()} />);
    click('Afspelen'); tick(1999); click('Pauzeren'); expect(pendingTimers()).toBe(0); tick(10000); expect(position()).toBe('1 / 3');
    click('Afspelen'); tick(1999); expect(position()).toBe('1 / 3'); tick(1); expect(position()).toBe('2 / 3');
    click('Opnieuw beginnen'); expect(position()).toBe('1 / 3'); expect(pendingTimers()).toBe(0); tick(5000); expect(position()).toBe('1 / 3');
  });
  it.each(['timeline', 'same-package', 'next', 'previous', 'keyboard', 'trade'])('pauses on manual %s selection without a stale step', method => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); render(<InspectionView inspection={fixtureInspection()} />);
    click('Volgende →'); click('Afspelen'); tick(1999);
    if (method === 'timeline' || method === 'same-package') fireEvent.click(within(timeline()).getAllByRole('button')[method === 'timeline' ? 0 : 1]);
    if (method === 'next') click('Volgende →');
    if (method === 'previous') click('← Vorige');
    if (method === 'keyboard') fireEvent.keyDown(screen.getByRole('region', { name: 'Mintpackages in chainvolgorde' }), { key: 'Home' });
    if (method === 'trade') click('Package 01');
    const expected = method === 'same-package' ? '2 / 3' : method === 'next' ? '3 / 3' : '1 / 3';
    expect(position()).toBe(expected); expect(pendingTimers()).toBe(0); tick(10000); expect(position()).toBe(expected);
  });
  it('pauses on workspace change, keeps position on return, and clears timers on unmount', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('fixture unavailable')));
    const view = render(<StrictMode><InspectionView inspection={fixtureInspection()} /></StrictMode>);
    click('Volgende →'); click('Afspelen'); tick(1999); click('Datakwaliteit pilot');
    await act(async () => { await Promise.resolve(); });
    tick(10000); click('Mintdossier'); expect(position()).toBe('2 / 3'); expect(pendingTimers()).toBe(0);
    click('Afspelen'); view.unmount(); expect(pendingTimers()).toBe(0); tick(10000);
  });
  it('keeps every fact once, exact raw integers, null/unknown units and parent links without changing records', () => {
    const data = fixtureInspection(), p = data.timeline.transactions[0], original = p.silver_facts[0];
    const second = structuredClone(original); second.record_sha256 = '8'.repeat(64); second.record.event_reported.is_buy = true;
    second.record.event_reported.quote_amount_raw_u64 = '9007199254740993';
    second.record.token_balance_context = { decimals: '6' }; second.record.quote_decimals = null;
    p.silver_facts.push(second); data.timeline.counts.silver_facts = '2';
    const before = JSON.stringify(data); render(<InspectionView inspection={data} />);
    const table = within(screen.getByRole('region', { name: 'Handelstabel volledig dossier' }));
    expect(table.getAllByRole('row')).toHaveLength(3);
    expect(table.getByText('Sell')).toBeTruthy(); expect(table.getByText('Buy')).toBeTruthy();
    expect(table.getByText('9007199254740993')).toBeTruthy(); expect(table.getAllByText('18446744073709551615')).toHaveLength(2);
    expect(table.getByText('Token-decimals (balanscontext): 6')).toBeTruthy();
    expect(table.getByText(/Quote: UNKNOWN · decimals: Niet ingevuld \(null\)/)).toBeTruthy();
    expect([...document.querySelectorAll('[data-fact-hash]')].map(r => r.getAttribute('data-fact-hash'))).toEqual([original.record_sha256, second.record_sha256]);
    click('Volgende →'); fireEvent.click(table.getAllByRole('button', { name: 'Package 01' })[1]);
    expect(position()).toBe('1 / 3'); expect(within(selected()).getByText(p.package_id)).toBeTruthy();
    expect(JSON.stringify(data)).toBe(before);
  });
});
