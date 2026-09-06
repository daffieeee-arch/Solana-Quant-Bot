// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AcquisitionMonitor, MAX_MONITOR_RESPONSE_CHARS, bytes } from './AcquisitionMonitor';
import { parseMonitorResponse, type MonitorResponse, type MonitorSnapshot } from './contract';

const wall = 1_788_800_000_000;
const id = 'a'.repeat(64);
function snapshot(overrides: Partial<MonitorSnapshot> = {}): MonitorSnapshot {
  return {
    schema_version: 'OF1_MONITOR_1', id, session_id: 'fixture-session', sequence: 1,
    updated_at_ms: wall, kind: 'LOCAL_SIMULATION', mode: 'LIVE', stage: 'DOWNLOADING',
    label: 'Begrensde loopbackdemo', dataset_root: '/tmp/monitor-fixture/dataset',
    source: '127.0.0.1:4321 · lokale HTTP-simulatie', epoch: 978,
    selected_slots: { start: 422496000, end_exclusive: 422496001 },
    started_at_ms: wall - 1000, completed_at_ms: null, elapsed_ms: 1000,
    selection: { operations_total: 1, operations_published: 0, planned_bytes: 1048576, received_selection_bytes: 131072, published_bytes: 0, verified_bytes: 0 },
    traffic: { received_bytes: 131072, received_basis: 'PROCESS_OBSERVED', reserved_bytes: 2097152,
      attempts: 1, retries: 0, speed_bps: 131072, download_eta_ms: 7000, eta_scope: 'SELECTION',
      speed_samples: [{ elapsed_ms: 500, bps: 100000 }, { elapsed_ms: 1000, bps: 131072 }] },
    storage: { used_bytes: 140000, available_bytes: 1000000000, cap_bytes: 10000000 },
    budgets: { attempts_remaining: 15, entity_bytes_remaining: 132120576, stage_attempts_remaining: 2, stage_entity_bytes_remaining: 10000000, runtime_remaining_ms: 590000 },
    operations: [{ sequence: 0, method: 'GET', path: '/978/epoch-978.car', range: 'bytes=1000-1049575', state: 'DOWNLOADING', expected_bytes: 1048576, received_bytes: 131072, published_bytes: 0, attempts: 1, status_code: 206, error: null }],
    integrity: { receipts: 'PENDING', car: 'UNAVAILABLE_NOT_VERIFIED', root_to_slot: 'UNAVAILABLE' },
    domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4', artifacts: [], errors: [], dropped_samples: 0,
    ...overrides,
  };
}
function envelope(...snapshots: MonitorSnapshot[]): MonitorResponse {
  return { schema_version: 'OF1_MONITOR_HTTP_1', read_at_unix_ms: wall, runs: snapshots.map(snapshot => ({ id: snapshot.id, state: 'READY', snapshot })) };
}
function recorded(): MonitorSnapshot {
  return snapshot({
    id: 'b'.repeat(64), kind: 'AUTHENTIC_METADATA', mode: 'RECORDED', stage: 'COMPLETE', label: 'OF1 epoch 978 · metadata',
    dataset_root: '/home/operator/solana-quant-data/runs/metadata', source: 'files.old-faithful.net:443',
    selected_slots: null, updated_at_ms: wall - 86400000, started_at_ms: wall - 86420850,
    completed_at_ms: wall - 86400000, elapsed_ms: 20850,
    selection: { operations_total: 4, operations_published: 4, planned_bytes: 5184161, received_selection_bytes: 5184161, published_bytes: 5184161, verified_bytes: 5184161 },
    traffic: { received_bytes: 5184161, received_basis: 'RECEIPTS_ONLY', reserved_bytes: 5192192, attempts: 4, retries: 0, speed_bps: null, download_eta_ms: null, eta_scope: null, speed_samples: [] },
    operations: [{ sequence: 3, method: 'HEAD', path: '/978/epoch-978.car', range: null, state: 'PUBLISHED', expected_bytes: 0, received_bytes: 0, published_bytes: 0, attempts: 1, status_code: 200, error: null }],
    artifacts: [{ id: 'receipt-3', label: 'CAR HEAD receipt', path: 'published/0000000003/receipt.json', sha256: 'c'.repeat(64) }],
  });
}
function respond(body: unknown, ok = true, status = 200) { return { ok, status, text: async () => JSON.stringify(body) }; }
async function mount(body: unknown) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(body)));
  await act(async () => { render(<AcquisitionMonitor />); });
}

describe('separate V2 acquisition monitor', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(wall); });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('displays authentic metadata as receipt evidence, not historical live speed or research readiness', async () => {
    await mount(envelope(recorded()));
    expect(screen.getByRole('heading', { name: 'OF1 epoch 978 · metadata' })).toBeInTheDocument();
    expect(screen.getByText('ENGINEERING_VALIDATION_ONLY')).toBeInTheDocument();
    expect(screen.getByText('Vastgelegd resultaat')).toBeInTheDocument();
    expect(screen.getByText('4 / 4 operaties')).toBeInTheDocument();
    expect(screen.getByText('Ontvangen volgens receipts')).toBeInTheDocument();
    expect(screen.getByText(/Initialisatie → laatste receipt/)).toBeInTheDocument();
    expect(screen.getAllByText('Nog onbekend').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/nog onbekend — niet gedecodeerd in B4/)).toBeInTheDocument();
    expect(screen.getByText(/Publicatie en integriteitscontrole zijn geen Research Ready-status/)).toBeInTheDocument();
    expect(screen.queryByText(/STALE · metingen/)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /CAR HEAD receipt/ })).toHaveAttribute('href', `/api/acquisition/runs/${'b'.repeat(64)}/artifacts/receipt-3`);
    expect(screen.getByText('Geen responsebody')).toBeInTheDocument();
  });

  it('reacts to measured bytes during a request before verification or publication', async () => {
    const first = snapshot();
    await mount(envelope(first));
    const progress = screen.getByRole('progressbar', { name: 'Ontvangen geselecteerde bytes' });
    expect(progress).toHaveAttribute('aria-valuenow', '12.5');
    expect(screen.getByRole('progressbar', { name: 'Gepubliceerde geselecteerde bytes' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('LOOPBACK · FIXTURE')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Downloadsnelheid uit Rust-metingen' })).toBeInTheDocument();
    const second = snapshot({ sequence: 2, updated_at_ms: wall + 700, elapsed_ms: 1700,
      selection: { ...first.selection, received_selection_bytes: 262144 },
      traffic: { ...first.traffic, received_bytes: 262144, speed_bps: 262144, download_eta_ms: 3000 },
      operations: [{ ...first.operations[0], received_bytes: 262144 }],
    });
    vi.mocked(fetch).mockResolvedValue(respond(envelope(second)) as Response);
    await act(async () => { await vi.advanceTimersByTimeAsync(701); });
    expect(progress).toHaveAttribute('aria-valuenow', '25');
    expect(screen.getByRole('progressbar', { name: 'Gepubliceerde geselecteerde bytes' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('256 KiB/s')).toBeInTheDocument();
    expect(screen.getByText('3 s')).toBeInTheDocument();
    expect(screen.getByText('2 MiB')).toBeInTheDocument();
    expect(screen.getByText(/Exclusief verificatie en duurzame publicatie/)).toBeInTheDocument();
  });

  it('uses the selected bytes denominator and separates retry traffic from unique progress', async () => {
    const s = snapshot(); s.traffic.received_bytes = 1048576; s.traffic.attempts = 2; s.traffic.retries = 1;
    s.traffic.reserved_bytes = 4194304; s.operations[0].attempts = 2;
    await mount(envelope(s));
    expect(screen.getByRole('progressbar', { name: 'Ontvangen geselecteerde bytes' })).toHaveAttribute('aria-valuenow', '12.5');
    expect(screen.getByText('4 MiB')).toBeInTheDocument();
    expect(screen.getByText(/Retrybytes tellen niet als extra unieke data/)).toBeInTheDocument();
  });

  it('does not round incomplete selected byte progress up to a completion claim', async () => {
    const s = snapshot(); s.selection.received_selection_bytes = 1048575;
    await mount(envelope(s));
    expect(screen.getByText('<100%')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Ontvangen geselecteerde bytes' })).toHaveAttribute('aria-valuetext', '<100%');
  });

  it('renders unknown selection size and ETA explicitly rather than using budget or zero', async () => {
    const s = snapshot(); s.selection.planned_bytes = null; s.traffic.speed_bps = null; s.traffic.download_eta_ms = null;
    s.operations[0].expected_bytes = null;
    await mount(envelope(s));
    expect(screen.getByRole('progressbar', { name: 'Ontvangen geselecteerde bytes' })).not.toHaveAttribute('aria-valuenow');
    expect(screen.getByRole('progressbar', { name: 'Gepubliceerde operaties' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('Omvang nog onbekend')).toBeInTheDocument();
    expect(screen.getAllByText('Nog onbekend')).toHaveLength(2);
    expect(bytes(null)).toBe('Nog onbekend');
    expect(bytes(0)).toBe('0 B');
  });

  it('shows stale live measurements without inventing a current speed or ETA', async () => {
    await mount(envelope(snapshot({ updated_at_ms: wall - 5000 })));
    expect(screen.getByText('STALE · metingen verouderd')).toBeInTheDocument();
    expect(screen.getByText('Geen actuele snelheidsclaim bij stale metingen')).toBeInTheDocument();
    expect(within(screen.getByText('Downloadsnelheid').parentElement!).getByText('Nog onbekend')).toBeInTheDocument();
  });

  it('retains a visibly stale snapshot after a connection failure and only sends GET', async () => {
    await mount(envelope(snapshot()));
    vi.mocked(fetch).mockRejectedValue(new Error('Lokale reader gestopt'));
    await act(async () => { await vi.advanceTimersByTimeAsync(701); });
    expect(screen.getByRole('alert')).toHaveTextContent('Lokale reader gestopt');
    expect(screen.getByRole('heading', { name: 'Begrensde loopbackdemo' })).toBeInTheDocument();
    expect(screen.getByText('STALE · metingen verouderd')).toBeInTheDocument();
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      expect(url).toBe('/api/acquisition/runs'); expect(init?.method).toBe('GET');
    }
  });

  it('rejects an older sequence without overwriting the newest received measurements', async () => {
    await mount(envelope(snapshot({ sequence: 4 })));
    const old = snapshot({ sequence: 3 }); old.selection.received_selection_bytes = 1;
    vi.mocked(fetch).mockResolvedValue(respond(envelope(old)) as Response);
    await act(async () => { await vi.advanceTimersByTimeAsync(701); });
    expect(screen.getByRole('alert')).toHaveTextContent('Verouderde snapshot geweigerd');
    expect(screen.getByRole('progressbar', { name: 'Ontvangen geselecteerde bytes' })).toHaveAttribute('aria-valuenow', '12.5');
  });

  it('selects authentic and simulation runs without starting or changing an acquisition', async () => {
    await mount(envelope(recorded(), snapshot()));
    fireEvent.click(screen.getByRole('button', { name: /Lokale simulatie Begrensde loopbackdemo/ }));
    expect(screen.getByText('LOOPBACK · FIXTURE')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Begrensde loopbackdemo' })).toBeInTheDocument();
    expect(screen.getAllByRole('button')).toHaveLength(2);
    expect(within(screen.getByRole('navigation', { name: 'Run kiezen' })).getAllByRole('button')).toHaveLength(2);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it('shows bounded concrete stop reasons and unavailable runs without fabricated counters', async () => {
    await mount(envelope(snapshot({ stage: 'STOPPED', errors: ['DEADLINE_EXCEEDED: stage admission closed'] })));
    expect(screen.getByRole('alert')).toHaveTextContent('DEADLINE_EXCEEDED');
    cleanup();
    await mount({ schema_version: 'OF1_MONITOR_HTTP_1', read_at_unix_ms: wall,
      runs: [{ id, state: 'UNAVAILABLE', reason: 'Snapshot ontbreekt' }] });
    expect(screen.getByRole('heading', { name: 'Rungegevens niet beschikbaar' })).toBeInTheDocument();
    expect(screen.queryByText('Daadwerkelijk ontvangen')).not.toBeInTheDocument();
  });

  it('shows unknown unpublished historical bytes and labels an operation-only ETA', async () => {
    const s = snapshot(); s.operations[0].received_bytes = null;
    s.traffic.received_basis = 'DURABLE_LOWER_BOUND'; s.traffic.eta_scope = 'CURRENT_OPERATION';
    await mount(envelope(s));
    expect(screen.getByText('Ontvangen · duurzame ondergrens')).toBeInTheDocument();
    expect(screen.getByText('Downloadtijd huidige operatie')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Operatie 0 ontvangen bytes' })).not.toHaveAttribute('aria-valuenow');
    expect(screen.getByText('Nog onbekend / 1 MiB')).toBeInTheDocument();
  });

  it('admits the bounded sixteen-snapshot envelope beyond the old one-MiB ceiling', async () => {
    const runs = Array.from({ length: 16 }, (_, index) => {
      const s = snapshot({ id: index.toString(16).padStart(64, '0') });
      s.operations = Array.from({ length: 32 }, (_, sequence) => ({ ...s.operations[0], sequence, path: '/fixture/' + 'x'.repeat(1700) }));
      const spare = 65_500 - JSON.stringify(s).length;
      expect(spare).toBeGreaterThan(0);
      // Fill a permitted textual source annotation while staying below the per-snapshot cap.
      expect(spare + s.source.length).toBeLessThan(4096);
      s.source += 'x'.repeat(spare);
      expect(JSON.stringify(s).length).toBe(65_500);
      return s;
    });
    const response = envelope(...runs);
    expect(JSON.stringify(response).length).toBeGreaterThan(1_048_576);
    expect(JSON.stringify(response).length).toBeLessThan(MAX_MONITOR_RESPONSE_CHARS);
    await mount(response);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getAllByRole('button')).toHaveLength(16);
  });

  it('keeps the UI isolated, responsive, keyboard reachable and free of external assets', () => {
    const entry = readFileSync('frontend/src/monitor/entry.tsx', 'utf8');
    const component = readFileSync('frontend/src/monitor/AcquisitionMonitor.tsx', 'utf8');
    const css = readFileSync('frontend/src/monitor/monitor.css', 'utf8');
    expect(entry).not.toMatch(/App|ResearchCockpit|cockpit-entry/);
    expect(component).not.toMatch(/from .*dashboard|from .*provider|from .*wallet/);
    expect(css).not.toMatch(/@import|https?:\/\//);
    expect(css).toContain('prefers-reduced-motion');
    expect(css).toContain(':focus-visible');
    expect(css).toContain('overflow-wrap: anywhere');
    expect(css).toContain('@media (max-width: 520px)');
  });
});

describe('Rust snapshot display contract', () => {
  it('accepts explicit unknowns and preserves every Rust measurement unchanged', () => {
    const response = envelope(snapshot(), recorded());
    expect(parseMonitorResponse(response)).toBe(response);
  });
  it('rejects missing, unsafe, duplicated or unexpected contract identities', () => {
    const missing = envelope(snapshot()) as any; delete missing.runs[0].snapshot.selection.planned_bytes;
    const unsafe = envelope(snapshot()); unsafe.runs[0].state === 'READY' && (unsafe.runs[0].snapshot.traffic.received_bytes = Number.MAX_SAFE_INTEGER + 1);
    const wrongDomain = envelope(snapshot()) as any; wrongDomain.runs[0].snapshot.domain_counts = 0;
    const wrongId = envelope(snapshot()) as any; wrongId.runs[0].snapshot.id = 'c'.repeat(64);
    for (const input of [missing, unsafe, wrongDomain, wrongId, envelope(snapshot(), snapshot())]) {
      expect(() => parseMonitorResponse(input)).toThrow('Ongeldig Rust-monitorcontract');
    }
  });
  it('rejects unbounded lists and invalid artifact identifiers', () => {
    const many = snapshot(); many.traffic.speed_samples = Array(65).fill({ elapsed_ms: 1, bps: 1 });
    const artifact = recorded(); artifact.artifacts[0].id = '../raw.bin';
    for (const input of [envelope(many), envelope(artifact)]) expect(() => parseMonitorResponse(input)).toThrow();
  });
});
