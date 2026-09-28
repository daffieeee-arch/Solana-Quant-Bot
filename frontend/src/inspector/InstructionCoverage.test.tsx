// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { fixtureInstructionCoverage } from '../../../tests/fixtures/instruction-coverage';
import { InstructionCoverageView, InstructionCoveragePanel } from './InstructionCoverage';
import { SnapshotMismatch } from './read-response';
vi.mock('./read-response', async original => ({ ...await original<typeof import('./read-response')>(), registeredResponse: () => async () => { throw new SnapshotMismatch(); } }));
afterEach(() => { cleanup(); document.head.querySelectorAll('meta[name^="inspector-snapshot"]').forEach(m=>m.remove()); vi.unstubAllGlobals(); });
it('filters instruction reasons without changing denominators; paginates and keeps atomic source roles inspectable', () => {
  const { report } = fixtureInstructionCoverage(), before = JSON.stringify(report);
  render(<InstructionCoverageView report={report} ordinal={0} />);
  expect(screen.getByRole('status').textContent).toContain('114 instructies');
  fireEvent.click(screen.getByRole('button', { name: 'Volgende instructiepagina' }));
  expect(screen.getByRole('status').textContent).toContain('pagina 2');
  fireEvent.change(screen.getByRole('combobox', { name: 'Instructiereden' }), { target: { value: 'BOUND_EXISTING_SILVER_EVENT' } });
  expect(screen.getByRole('status').textContent).toContain('57 instructies');
  expect(screen.getByRole('status').textContent).toContain('pagina 1');
  fireEvent.click(screen.getAllByRole('button', { name: /Inspecteer instructie/ })[0]);
  const source = screen.getByRole('article', { name: 'Geselecteerde instructiebron' });
  expect(source.textContent).toContain('SUPPORTING_EVENT_CPI'); expect(source.textContent).toContain('ADMITTED_TRADE');
  expect(within(screen.getByRole('table', { name: /Afzonderlijke noemers/ })).getByText('114')).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox', { name: 'Instructiecategorie' }), { target: { value: 'UNEXPLAINED' } });
  expect(screen.getByRole('status').textContent).toContain('0 instructies');
  expect(source.textContent).toContain('Selecteer een instructie');
  expect(JSON.stringify(report)).toBe(before);
});
it('missing CPI context stays unknown without invented instructions or negative conclusions', () => {
  const { report } = fixtureInstructionCoverage(), w = report.windows[0];
  w.summary.uncertain_packages='1'; w.summary.uncertainty_items='1'; w.summary.potentially_hiding_packages='1'; w.summary.negative_conclusion='UNAVAILABLE';
  w.instruction_inventory.uncertainty=[{ package_id:'fixture-unknown',effective_at:{slot:'422526145',transaction_index_in_slot:'9'},part_id:'0',items:[{reason:'CPI_INFORMATION_UNAVAILABLE'}]}];
  render(<InstructionCoverageView report={report} ordinal={0} />);
  expect(screen.getByText('Negatieve conclusie: UNAVAILABLE')).toBeTruthy();
  expect(screen.getByText(/CPI_INFORMATION_UNAVAILABLE/)).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('114 instructies');
});
it('unregistered inventory is unavailable without requests; a stale response never publishes inventory', async () => {
  const { cohort }=fixtureInstructionCoverage(), fetcher=vi.fn().mockResolvedValue({ok:true});vi.stubGlobal('fetch',fetcher);
  const first=render(<InstructionCoveragePanel cohort={cohort.report} ordinal={0}/>);
  expect(screen.getByRole('status').textContent).toContain('UNAVAILABLE');expect(fetcher).not.toHaveBeenCalled();first.unmount();
  for (const name of ['development-cohort','development-instructions']) {const m=document.createElement('meta');m.name=`inspector-snapshot-${name}`;m.content='a'.repeat(64);document.head.append(m);}
  render(<InstructionCoveragePanel cohort={cohort.report} ordinal={0}/>);
  expect((await screen.findByText(/STALE/)).textContent).toContain('STALE');expect(screen.queryByRole('combobox')).toBeNull();
});
