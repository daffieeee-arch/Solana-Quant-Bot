// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixtureDevelopmentCohort } from '../../../tests/fixtures/development-cohort';
import { DevelopmentCohortPanel, DevelopmentCohortView } from './DevelopmentCohort';
import { SnapshotMismatch } from './read-response';
vi.mock('./read-response', async original => ({ ...await original<typeof import('./read-response')>(), registeredResponse: () => async () => { throw new SnapshotMismatch(); } }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('selects window and mint, opens both real pair references, keeps exact values and resets selection', () => {
  const { report } = fixtureDevelopmentCohort(), before = JSON.stringify(report);
  render(<DevelopmentCohortView report={report} />);
  expect(within(screen.getByLabelText('Vier-venstertotalen')).getByText('80541')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Inspecteer buy van het paar' }));
  let source = screen.getByRole('article', { name: 'Geselecteerde cohortbron' });
  expect(source.textContent).toContain(report.windows[0].mints[0].observed_pair!.buy_package_id);
  fireEvent.click(screen.getByRole('button', { name: 'Inspecteer sell van het paar' }));
  expect(source.textContent).toContain(report.windows[0].mints[0].observed_pair!.sell_package_id);
  expect(screen.getAllByText('18446744073709551615').length).toBeGreaterThan(0);
  expect(screen.getByText(/onvolledige semantische Pump-dekking verhindert/i)).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox', { name: 'Venster' }), { target: { value: '3' } });
  source = screen.getByRole('article', { name: 'Geselecteerde cohortbron' });
  expect(source.textContent).toContain('Selecteer een feit');
  expect(screen.getByText(/grensslot 422659944/)).toBeTruthy();
  expect(screen.getByRole('link', { name: /reproduceerbare DEVELOPMENT/ }).getAttribute('href')).toBe('/evidence/development-cohort.json');
  expect(JSON.stringify(report)).toBe(before);
});
it('a missing pair is a bounded observation, with explicit unknowns and no synthetic zero claims', () => {
  const { report } = fixtureDevelopmentCohort(); report.windows[0].mints[0].observed_pair = null; report.windows[0].mints[0].pair_state = 'NO_PAIR_IN_ADMITTED_FACTS';
  render(<DevelopmentCohortView report={report} />);
  expect(screen.getByText('NO_PAIR_IN_ADMITTED_FACTS')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Inspecteer buy van het paar' })).toBeNull();
  expect(screen.getAllByText(/UNAVAILABLE/).length).toBeGreaterThan(0);
  expect(screen.getByText('Research Ready: false')).toBeTruthy();
});
it('rejects stale page-pinned responses before any cohort rows appear', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  render(<DevelopmentCohortPanel />);
  expect((await screen.findByRole('status')).textContent).toContain('STALE');
  expect(screen.queryByRole('combobox', { name: 'Venster' })).toBeNull();
});
