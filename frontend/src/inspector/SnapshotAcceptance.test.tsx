// @vitest-environment jsdom
// Deliberately synthetic state/transport fixtures, not extra authentic observations.
import React, { StrictMode } from 'react';
import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { fixtureInspection } from '../../../tests/fixtures/mint-inspector';
import { fixturePilotQuality } from '../../../tests/fixtures/pilot-quality';
import { fixtureMintFlow } from '../../../tests/fixtures/mint-flow';
import { MintInspector } from './MintInspector';
import { PilotQualityPanel, PilotQualityView } from './PilotQuality';
import { useMintFlow } from './MintFlow';
import { parsePilotQuality } from '../../../src/mint-inspector/pilot-quality';

const pilotBindings = { ...fixtureInspection().inputs, collection: 'a'.repeat(64), plan: 'a'.repeat(64) };
const counts = { transactions: '15', silver_facts: '5', balance_observations: '58' };
function pin(name: string, value: unknown) {
  const meta = document.createElement('meta'); meta.name = `inspector-snapshot-${name}`;
  meta.content = createHash('sha256').update(JSON.stringify(value)).digest('hex'); document.head.append(meta);
}
function deferred() { let resolve!: (r: Response) => void; const promise = new Promise<Response>(r => { resolve = r; }); return { promise, resolve }; }
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.head.querySelectorAll('meta[name^="inspector-snapshot-"]').forEach(n => n.remove()); });
describe('immutable snapshot acceptance', () => {
  it('publishes READY only after contract and page hash validation, ignores an older StrictMode response', async () => {
    const data = fixtureInspection(), first = deferred(), second = deferred(); pin('inspection', data);
    let reads = 0; const signals: AbortSignal[] = [];
    vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
      if (url !== '/api/inspection') return Promise.resolve(new Response('', { status: 404 }));
      signals.push(options.signal as AbortSignal); return ++reads === 1 ? first.promise : second.promise;
    }));
    render(<StrictMode><MintInspector /></StrictMode>);
    expect(screen.queryByLabelText('Geverifieerde selectietellingen')).toBeNull();
    await act(async () => { second.resolve(new Response(JSON.stringify(data))); });
    expect(await screen.findByText('READY')).toBeTruthy(); expect(screen.getByLabelText('Geverifieerde selectietellingen')).toBeTruthy();
    expect(signals[0].aborted).toBe(true);
    await act(async () => { first.resolve(new Response('{"old":true}')); });
    expect(screen.getByLabelText('Geverifieerde selectietellingen')).toBeTruthy(); expect(screen.queryByText('STALE')).toBeNull();
  });
  it('withholds mismatched mint bytes as STALE, without leaking prior counts or accepting an internally valid replacement', async () => {
    const data = fixtureInspection(); pin('inspection', data);
    const replacement = structuredClone(data); replacement.timeline.transactions[0].signatures = ['another-snapshot'];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(replacement))));
    render(<MintInspector />);
    expect(await screen.findByText('STALE')).toBeTruthy(); expect(screen.queryByLabelText('Geverifieerde selectietellingen')).toBeNull();
  });
  it('withholds the previous pilot immediately on changed binding and ignores a late previous response', async () => {
    const q = fixturePilotQuality(), a = pilotBindings; pin('pilot-quality', q);
    const first = deferred(), second = deferred(); vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise));
    const view = render(<PilotQualityPanel bindings={a} mintCounts={counts} />);
    const b = { ...a, collection: 'f'.repeat(64) }; view.rerender(<PilotQualityPanel bindings={b} mintCounts={counts} />);
    await act(async () => { second.resolve(new Response(JSON.stringify(q))); });
    expect(await screen.findByText(/UNAVAILABLE · Geen geldig geregistreerd/)).toBeTruthy();
    await act(async () => { first.resolve(new Response(JSON.stringify(q))); });
    expect(screen.queryByLabelText('Geverifieerde pilottellingen')).toBeNull();
  });
  it('removes already displayed pilot counts when a new snapshot fails and distinguishes changed bytes', async () => {
    const q = fixturePilotQuality(), a = pilotBindings; pin('pilot-quality', q);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(q))).mockResolvedValueOnce(new Response('{}')));
    const view = render(<PilotQualityPanel bindings={a} mintCounts={counts} />);
    expect(await screen.findByLabelText('Geverifieerde pilottellingen')).toBeTruthy();
    view.rerender(<PilotQualityPanel bindings={{ ...a, collection: 'e'.repeat(64) }} mintCounts={counts} />);
    expect(screen.queryByLabelText('Geverifieerde pilottellingen')).toBeNull();
    expect(await screen.findByText(/STALE · Geen geldig geregistreerd/)).toBeTruthy();
  });
  it('withholds an old flow immediately when its inspection changes', async () => {
    const { inspection, flow } = fixtureMintFlow(); pin('mint-flow', flow);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(flow))).mockResolvedValueOnce(new Response('{}')));
    function Harness({ data }: { data: typeof inspection }) { const f = useMintFlow(data); return <p>{f && f !== 'STALE' ? f.sha256 : 'NO_FLOW'}</p>; }
    const view = render(<Harness data={inspection} />); await screen.findByText(flow.sha256);
    view.rerender(<Harness data={{ ...inspection, inputs: { ...inspection.inputs, timeline: 'e'.repeat(64) } }} />);
    expect(screen.queryByText(flow.sha256)).toBeNull(); await waitFor(() => expect(screen.getByText('NO_FLOW')).toBeTruthy());
  });
  it('shows a concrete coverage GAP with its denominator and never turns missing fields into zero', () => {
    const q = fixturePilotQuality(); q.manifest.counts.missing = '2'; q.manifest.counts.decoded = '3222'; q.manifest.counts.quarantined = null;
    parsePilotQuality(q, pilotBindings);
    render(<PilotQualityView quality={q} mintCounts={counts} />);
    expect(screen.getByRole('status').textContent).toContain('2 verwachte packages ontbreken binnen 3224');
    expect(screen.getByRole('status').textContent).toContain('3222 gedecodeerde packages');
    expect(screen.getByText('QUARANTINED · Bronze').nextElementSibling?.textContent).toBe('UNAVAILABLE');
    expect(screen.getByText(/55340232221128654845 Raw-bytes/)).toBeTruthy(); // exact sum exceeds u64
    expect(screen.getByText('Gemeten rapportgeneratieduur · seconden').nextElementSibling?.textContent).toBe('UNAVAILABLE');
  });
});
