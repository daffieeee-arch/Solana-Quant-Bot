// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixtureInspection } from '../../../tests/fixtures/mint-inspector';
import { EventObservations, reserveValue } from './EventObservations';
import { InspectionView } from './MintInspector';
import type { Json } from '../../../src/mint-inspector/contract';

afterEach(() => { cleanup(); vi.useRealTimers(); });
const real = 'real_token_reserves_raw_u64', virtual = 'virtual_token_reserves_raw_u64';
function data() {
  const d = fixtureInspection(), e = d.timeline.transactions[0].silver_facts[0].record.event_reported;
  Object.assign(e, { [real]: '18446744073709551614', [virtual]: '18446744073709551615', user_address: 'reported-user', creator_address_reported: 'reported-creator', fee_recipient: 'reported-fee' });
  return d;
}

describe('existing event observations presentation', () => {
  it('keeps exact u64 extremes, zero, missing, null and invalid source values distinct', () => {
    for (const raw of ['0', '9007199254740993', '18446744073709551615']) expect(reserveValue(raw)).toEqual({ text: raw, value: BigInt(raw) });
    expect(reserveValue(undefined)).toEqual({ text: 'UNAVAILABLE · veld ontbreekt', value: null });
    expect(reserveValue(null)).toEqual({ text: 'UNAVAILABLE · null', value: null });
    const invalid: Json[] = ['', '-1', '00', '1.5', '1e3', ' 1', '18446744073709551616', '9'.repeat(200), false, [], {}];
    for (const raw of invalid) {
      expect(reserveValue(raw).value).toBeNull(); expect(reserveValue(raw).text).toBe(`ONGELDIG · ${JSON.stringify(raw)}`);
    }
  });
  it('retains separate facts in atomic parent groups, original order/values/hashes and no observations on failures', () => {
    const d = data(), p = d.timeline.transactions[0];
    const extra = structuredClone(p.silver_facts[0]); extra.record_sha256 = '8'.repeat(64);
    extra.record.event_reported[real] = '18446744073709551615'; extra.record.event_reported[virtual] = '18446744073709551615';
    p.silver_facts.push(extra); const before = JSON.stringify(d);
    const select = vi.fn(); render(<EventObservations packages={d.timeline.transactions} selectedIndex={0} onSelect={select} />);
    const points = screen.getAllByRole('button').filter(b => b.hasAttribute('data-reserve-field'));
    expect(points).toHaveLength(4);
    expect(points.map(b => b.getAttribute('data-raw-value'))).toEqual(['18446744073709551614', '18446744073709551615', '18446744073709551615', '18446744073709551615']);
    expect(new Set(points.map(b => b.getAttribute('transform'))).size).toBe(4);
    expect(points.every(b => !/NaN|Infinity/.test(b.getAttribute('transform')!))).toBe(true);
    const groups = document.querySelectorAll('[data-reserve-package]');
    expect(groups[0].querySelectorAll('tr')).toHaveLength(2); expect(groups[1].querySelectorAll('tr')).toHaveLength(0); expect(groups[2].querySelectorAll('tr')).toHaveLength(0);
    expect([...groups[0].querySelectorAll('tr')].map(r => r.getAttribute('data-reserve-row'))).toEqual(p.silver_facts.map(f => f.record_sha256));
    const addresses = screen.getByRole('table', { name: 'Adresrollen per bestaand feit' });
    expect(within(addresses).getAllByText('reported-user')).toHaveLength(2);
    expect(within(addresses).getAllByText('reported-creator')).toHaveLength(2);
    expect(within(addresses).getAllByText('reported-fee')).toHaveLength(2);
    fireEvent.click(points[3]); expect(select).toHaveBeenLastCalledWith(0);
    expect(document.querySelector('svg polyline')).toBeNull(); // No interpolated series.
    expect(JSON.stringify(d)).toBe(before);
  });
  it('plots an explicit zero but never creates points for missing/invalid observations or failed packages', () => {
    const d = data(), e = d.timeline.transactions[0].silver_facts[0].record.event_reported;
    e[real] = '0'; e[virtual] = null;
    const view = render(<EventObservations packages={d.timeline.transactions} selectedIndex={2} onSelect={vi.fn()} />);
    expect(document.querySelectorAll('[data-reserve-field]')).toHaveLength(1);
    expect(document.querySelector('[data-reserve-field]')?.getAttribute('data-raw-value')).toBe('0');
    expect(screen.getByText('UNAVAILABLE · null')).toBeTruthy();
    e[real] = '18446744073709551616'; delete e[virtual];
    view.rerender(<EventObservations packages={d.timeline.transactions} selectedIndex={2} onSelect={vi.fn()} />);
    expect(document.querySelectorAll('[data-reserve-field]')).toHaveLength(0);
    expect(screen.getByText('ONGELDIG · "18446744073709551616"')).toBeTruthy();
    expect(screen.getByText('UNAVAILABLE · veld ontbreekt')).toBeTruthy();
    expect(screen.getByText(/Geen geldige u64-reservepunten/)).toBeTruthy();
  });
  it('displays missing/null/invalid address values and escapes reported text without enrichment links', () => {
    const d = data(), e = d.timeline.transactions[0].silver_facts[0].record.event_reported;
    delete e.user_address; e.creator_address_reported = null; e.fee_recipient = [];
    const view = render(<EventObservations packages={d.timeline.transactions} selectedIndex={0} onSelect={vi.fn()} />);
    const table = screen.getByRole('table', { name: 'Adresrollen per bestaand feit' });
    expect(within(table).getByText('UNAVAILABLE · veld ontbreekt')).toBeTruthy();
    expect(within(table).getByText('UNAVAILABLE · null')).toBeTruthy();
    expect(within(table).getByText('ONGELDIG · []')).toBeTruthy();
    e.user_address = '<img src=x onerror=alert(1)>';
    view.rerender(<EventObservations packages={d.timeline.transactions} selectedIndex={0} onSelect={vi.fn()} />);
    expect(within(table).getByText('<img src=x onerror=alert(1)>')).toBeTruthy();
    expect(table.querySelector('img,a')).toBeNull();
  });
  it.each(['point-click', 'point-enter', 'point-space', 'reserve-table', 'address-table'])('%s selects the full parent and pauses replay, preserving focus and counts', method => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const d = data(); render(<InspectionView inspection={d} />);
    const totals = screen.getByLabelText('Geverifieerde selectietellingen').textContent;
    fireEvent.click(screen.getByRole('button', { name: 'Volgende →' })); fireEvent.click(screen.getByRole('button', { name: 'Afspelen' }));
    act(() => { vi.advanceTimersByTime(1999); });
    const target = method.startsWith('point') ? screen.getByRole('button', { name: /^Real token:/ })
      : within(screen.getByRole('table', { name: method === 'reserve-table' ? 'Exacte reservewaarnemingen' : 'Adresrollen per bestaand feit' })).getByRole('button', { name: 'Package 01 · feit 1' });
    target.focus();
    if (method === 'point-enter' || method === 'point-space') fireEvent.keyDown(target, { key: method === 'point-enter' ? 'Enter' : ' ' });
    else fireEvent.click(target);
    expect(screen.getByLabelText('Geselecteerde replaypositie').textContent).toBe('1 / 3');
    expect(document.activeElement).toBe(target);
    act(() => { vi.advanceTimersByTime(10000); });
    expect(screen.getByLabelText('Geselecteerde replaypositie').textContent).toBe('1 / 3');
    const selected = screen.getByRole('article', { name: 'Geselecteerd atomair package' });
    expect(within(selected).getByText(d.timeline.transactions[0].package_id)).toBeTruthy();
    expect(within(selected).getByText('Pilot · RESEARCH_SAMPLING')).toBeTruthy();
    expect(screen.getByLabelText('Geverifieerde selectietellingen').textContent).toBe(totals);
    expect(screen.getByText('Research Ready: false')).toBeTruthy();
  });
});
