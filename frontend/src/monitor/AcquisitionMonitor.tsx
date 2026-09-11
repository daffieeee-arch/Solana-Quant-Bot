import { useEffect, useRef, useState } from 'react';
import { parseMonitorResponse, type MonitorResponse, type MonitorSnapshot } from './contract';

const number = new Intl.NumberFormat('nl-NL', { maximumFractionDigits: 1 });
const count = new Intl.NumberFormat('nl-NL', { maximumFractionDigits: 0 });
// The reader admits 16 snapshots of at most 64 KiB, plus its bounded JSON envelope.
export const MAX_MONITOR_RESPONSE_CHARS = 16 * 65_536 + 16 * 1024;
const stages = [
  ['PREPARING', 'Voorbereiden'], ['METADATA', 'Metadata'], ['DOWNLOADING', 'Ontvangen'],
  ['VERIFYING', 'Controleren'], ['PUBLISHING', 'Publiceren'], ['COMPLETE', 'Gereed'],
] as const;
const stageLabels: Record<MonitorSnapshot['stage'], string> = {
  PREPARING: 'Voorbereiden', METADATA: 'Metadata ophalen', DOWNLOADING: 'Download bezig',
  VERIFYING: 'Integriteit controleren', PUBLISHING: 'Duurzaam publiceren', COMPLETE: 'Selectie afgerond', STOPPED: 'Gestopt',
};
const operationLabels: Record<MonitorSnapshot['operations'][number]['state'], string> = {
  PENDING: 'Gepland', RESERVED: 'Gereserveerd', DOWNLOADING: 'Ontvangen', VERIFYING: 'Controleren',
  PUBLISHING: 'Publiceren', PUBLISHED: 'Gepubliceerd', FAILED: 'Mislukt',
};
const kindLabels: Record<MonitorSnapshot['kind'], string> = {
  AUTHENTIC_METADATA: 'Authentieke metadata', AUTHENTIC_PAYLOAD: 'Authentieke Raw', LOCAL_SIMULATION: 'Lokale simulatie',
};

export function bytes(value: number | null): string {
  if (value === null) return 'Nog onbekend';
  if (value < 1024) return `${count.format(value)} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let n = value / 1024; let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${number.format(n)} ${units[i]}`;
}

function duration(ms: number | null): string {
  if (ms === null) return 'Nog onbekend';
  if (ms < 1000) return `${count.format(ms)} ms`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  return `${Math.floor(seconds / 3600)} u ${Math.floor((seconds % 3600) / 60)} min`;
}
function when(ms: number): string { return new Date(ms).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC'); }
function fraction(value: number | null, total: number | null): number | null {
  return value === null || total === null || total === 0 ? null : Math.max(0, Math.min(100, value / total * 100));
}
function percent(value: number | null): string {
  if (value === null) return 'Omvang nog onbekend';
  // Rounded byte progress must never imply completion while an operation remains short.
  return value > 99.9 && value < 100 ? '<100%' : `${number.format(value)}%`;
}

function Progress({ value, label }: { value: number | null; label: string }) {
  return <div className={`progress-track${value === null ? ' unknown' : ''}`} role="progressbar"
    aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}
    aria-valuetext={value === null ? 'Omvang nog onbekend' : percent(value)}>
    <span style={{ width: value === null ? '0%' : `${value}%` }} />
  </div>;
}

function SpeedChart({ samples, available }: { samples: MonitorSnapshot['traffic']['speed_samples']; available: boolean }) {
  if (!available || samples.length < 2) return <div className="chart-empty">Nog onvoldoende snelheidsmetingen<span>Geen historische snelheid of ETA gereconstrueerd.</span></div>;
  const width = 780; const height = 120;
  const minTime = samples[0].elapsed_ms;
  const maxTime = samples[samples.length - 1].elapsed_ms;
  const maximum = Math.max(1, ...samples.map(sample => sample.bps));
  const points = samples.map(sample => `${((sample.elapsed_ms - minTime) / Math.max(1, maxTime - minTime) * width).toFixed(1)},${(height - sample.bps / maximum * (height - 12)).toFixed(1)}`).join(' ');
  return <div className="speed-chart">
    <div className="chart-scale"><span>{bytes(maximum)}/s</span><span>0 B/s</span></div>
    <svg viewBox={`0 0 ${width} ${height + 6}`} role="img" aria-label="Downloadsnelheid uit Rust-metingen" preserveAspectRatio="none">
      <defs><linearGradient id="speed-fill" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#3cd3b2" stopOpacity=".22" /><stop offset="1" stopColor="#3cd3b2" stopOpacity="0" /></linearGradient></defs>
      {[0.25, 0.5, 0.75, 1].map(v => <line key={v} x1="0" x2={width} y1={height * v} y2={height * v} className="chart-grid" />)}
      <polygon points={`0,${height} ${points} ${width},${height}`} fill="url(#speed-fill)" />
      <polyline points={points} fill="none" stroke="#3cd3b2" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
    <div className="chart-times"><span>{duration(minTime)}</span><span>{duration(maxTime)} · gemeten procesvenster</span></div>
  </div>;
}

function Stat({ label, value, detail, accent = false }: { label: string; value: string; detail: string; accent?: boolean }) {
  return <div className={`stat${accent ? ' accent' : ''}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function RunContent({ snapshot: s, stale, now }: { snapshot: MonitorSnapshot; stale: boolean; now: number }) {
  const recorded = s.mode === 'RECORDED';
  const simulation = s.kind === 'LOCAL_SIMULATION';
  const terminal = s.stage === 'COMPLETE' || s.stage === 'STOPPED';
  const progress = fraction(s.selection.received_selection_bytes, s.selection.planned_bytes);
  const publishedProgress = fraction(s.selection.published_bytes, s.selection.planned_bytes);
  const operationProgress = fraction(s.selection.operations_published, s.selection.operations_total);
  const current = s.operations.find(op => ['RESERVED', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING'].includes(op.state));
  const activeStep = stages.findIndex(([key]) => key === s.stage);
  const receivedLabel = s.traffic.received_basis === 'RECEIPTS_ONLY' ? 'Ontvangen volgens receipts'
    : s.traffic.received_basis === 'DURABLE_LOWER_BOUND' ? 'Ontvangen · duurzame ondergrens' : 'Daadwerkelijk ontvangen';
  return <>
    <section className={`evidence-banner${simulation ? ' simulated' : ''}`} aria-label="Databewijs">
      <div><span className="evidence-dot" /><strong>{kindLabels[s.kind]}</strong><span>{simulation ? 'LOOPBACK · FIXTURE' : 'ENGINEERING_VALIDATION_ONLY'}</span></div>
      <p>{simulation ? 'Echte Rust-metingen van lokale HTTP-responses. Geen authentieke chain-data.' : 'Bronbytes en receipts. Geen analyseklare coin-dataset of bewijs van een trading-edge.'}</p>
    </section>

    <section className="run-heading">
      <div><p className="eyebrow">RUN / EPOCH {s.epoch}</p><h1>{s.label}</h1><p className="run-path">{s.dataset_root}</p></div>
      <div className="run-state"><span className={`state-chip ${stale ? 'warn' : s.stage === 'STOPPED' ? 'bad' : 'good'}`}>
        <span className="status-dot" />{stale ? 'STALE · metingen verouderd' : recorded ? 'Vastgelegd resultaat' : stageLabels[s.stage]}</span>
        <small>{recorded ? 'Read-only controle' : 'Laatste Rust-meting'} · {when(s.updated_at_ms)}</small>
        {!recorded && <small>{duration(Math.max(0, now - s.updated_at_ms))} geleden · reeks {s.sequence}</small>}
      </div>
    </section>

    <div className="metric-grid">
      <Stat label={receivedLabel} value={bytes(s.traffic.received_bytes)} detail="Response-entity-bytes; niet fysieke wire bytes" />
      <Stat label="Duurzaam gepubliceerd" value={bytes(s.selection.published_bytes)} detail={`${bytes(s.selection.verified_bytes)} gecontroleerd · unieke selectie`} accent />
      <Stat label="Downloadsnelheid" value={recorded || stale ? 'Nog onbekend' : s.traffic.speed_bps === null ? 'Nog onbekend' : `${bytes(s.traffic.speed_bps)}/s`} detail={recorded ? 'Geen live meetreeks beschikbaar' : stale ? 'Geen actuele snelheidsclaim bij stale metingen' : 'Gemeten tijdens de HTTP-response'} />
      <Stat label={s.traffic.eta_scope === 'CURRENT_OPERATION' ? 'Downloadtijd huidige operatie' : 'Resterende downloadtijd selectie'} value={recorded || stale ? 'Nog onbekend' : duration(s.traffic.download_eta_ms)} detail="Exclusief verificatie en duurzame publicatie" />
    </div>

    <div className="main-grid">
      <section className="panel transfer-panel" aria-label="Downloadvoortgang">
        <div className="panel-heading"><div><p className="eyebrow">GEPLANDE SELECTIE</p><h2>{stageLabels[s.stage]}</h2></div><span className="mono">{count.format(s.selection.operations_published)} / {count.format(s.selection.operations_total)} operaties</span></div>
        <p className="metric-note">Huidige requestcyclus; voltooiing van de selectie staat in de publicatieteller.</p>
        <ol className="stage-list">{stages.map(([key, label], index) => <li key={key} className={index < activeStep ? 'complete' : index === activeStep ? 'current' : ''}><span>{index < activeStep ? '✓' : index + 1}</span>{label}</li>)}</ol>
        <div className="progress-caption"><span>Ontvangen selectie <b>{bytes(s.selection.received_selection_bytes)}</b>{s.selection.planned_bytes !== null && <> / {bytes(s.selection.planned_bytes)}</>}</span><strong>{percent(progress)}</strong></div>
        <Progress value={progress} label="Ontvangen geselecteerde bytes" />
        <div className="publication-caption"><span>Gepubliceerd {bytes(s.selection.published_bytes)}</span><span>{s.selection.planned_bytes === null ? 'Publicaties als tijdelijke noemer' : percent(publishedProgress)}</span></div>
        <Progress value={s.selection.planned_bytes === null ? operationProgress : publishedProgress} label={s.selection.planned_bytes === null ? 'Gepubliceerde operaties' : 'Gepubliceerde geselecteerde bytes'} />
        <p className="metric-note">Noemer: de geplande selectie — nooit de hele epoch of budgetcap. Retrybytes tellen niet als extra unieke data.</p>
        <div className="current-object"><span>HUIDIGE OPERATIE</span><code>{current ? `${current.method} ${current.path}${current.range ? ` · ${current.range}` : ''}` : terminal ? 'Geen request actief' : 'Wachten op eerste request'}</code></div>
        <div className="chart-heading"><h3>Snelheid tijdens requests</h3><span>{recorded ? 'Historische live metingen niet beschikbaar' : `${s.traffic.speed_samples.length} Rust-meetpunten`}</span></div>
        <SpeedChart samples={s.traffic.speed_samples} available={!recorded} />
        <div className="transfer-footer"><span>{recorded ? 'Initialisatie → laatste receipt' : 'Verstreken'} <b>{duration(s.elapsed_ms)}</b></span><span>Pogingen <b>{s.traffic.attempts}</b></span><span>Retries <b>{s.traffic.retries}</b></span></div>
      </section>

      <aside className="right-panels">
        <section className="panel" aria-label="Integriteitsstatus"><div className="panel-heading"><h2>Integriteit & publicatie</h2><span className="tiny-tag">RAW</span></div>
          <dl className="key-values"><div><dt>Receipts</dt><dd>{s.integrity.receipts}</dd></div><div><dt>CAR / CID</dt><dd>{s.integrity.car}</dd></div><div><dt>Root → slot membership</dt><dd className="unknown">UNAVAILABLE</dd></div></dl>
          <p className="integrity-note">Publicatie en integriteitscontrole zijn geen Research Ready-status.</p>
        </section>
        <section className="panel" aria-label="Opslag en budgetten"><div className="panel-heading"><h2>Opslag & budgetruimte</h2></div>
          <dl className="key-values"><div><dt>Gebruikte datasetopslag</dt><dd>{bytes(s.storage.used_bytes)}</dd></div><div><dt>Vrij op bestandssysteem</dt><dd>{bytes(s.storage.available_bytes)}</dd></div><div><dt>Run-opslagcap</dt><dd>{bytes(s.storage.cap_bytes)}</dd></div><div className="separated"><dt>Bytes gereserveerd</dt><dd>{bytes(s.traffic.reserved_bytes)}</dd></div><div><dt>Aggregate bytes over</dt><dd>{bytes(s.budgets.entity_bytes_remaining)}</dd></div><div><dt>Stage bytes over</dt><dd>{bytes(s.budgets.stage_entity_bytes_remaining)}</dd></div><div><dt>Pogingen over · stage / totaal</dt><dd>{s.budgets.stage_attempts_remaining} / {s.budgets.attempts_remaining}</dd></div><div><dt>Resterende deadline-toelating</dt><dd>{duration(s.budgets.runtime_remaining_ms)}</dd></div></dl>
          <p className="metric-note">Opslag en vrije ruimte gemeten bij import of operatiegrens, niet per netwerkfragment. Reserveringen zijn budgetverbruik, geen downloadvoortgang of betaalde querykosten. Geen nieuwe run geautoriseerd.</p>
        </section>
      </aside>
    </div>

    <section className="panel operation-panel"><div className="panel-heading"><div><p className="eyebrow">REQUESTS & PUBLICATIES</p><h2>Operaties</h2></div><span className="muted">{s.operations.length} vastgelegde operaties</span></div>
      <div className="table-scroll"><table><thead><tr><th>Operatie / bronpad</th><th>Status</th><th>Ontvangen / gepland</th><th>Gepubliceerd</th><th>Pogingen</th><th>HTTP</th></tr></thead><tbody>
        {s.operations.map(op => <tr key={op.sequence}><td><div className="operation-name"><span className="tiny-tag">{op.method}</span><code>{op.path}</code></div>{op.range && <small className="mono muted">{op.range}</small>}{op.error && <p className="inline-error">{op.error}</p>}</td>
          <td><span className={`operation-status ${op.state.toLowerCase()}`}>{operationLabels[op.state]}</span></td><td><span className="mono">{bytes(op.received_bytes)} / {op.expected_bytes === null ? 'onbekend' : bytes(op.expected_bytes)}</span>{op.expected_bytes !== 0 && <Progress value={fraction(op.received_bytes, op.expected_bytes)} label={`Operatie ${op.sequence} ontvangen bytes`} />}{op.expected_bytes === 0 && <small className="muted">Geen responsebody</small>}</td><td className="mono">{bytes(op.published_bytes)}</td><td className="mono">{op.attempts}</td><td className="mono">{op.status_code === null ? '—' : op.status_code}</td></tr>)}
      </tbody></table></div>
    </section>

    {s.errors.length > 0 && <section className="error-panel" role="alert"><h2>Concrete foutredenen</h2><ul>{s.errors.map((error, index) => <li key={`${index}-${error}`}>{error}</li>)}</ul><p>Geen automatische providerfallback. Deze monitor start of hervat geen acquisitie.</p></section>}

    <section className="panel provenance-panel"><div className="panel-heading"><div><p className="eyebrow">HERKOMST & EINDRESULTAAT</p><h2>{terminal ? 'Leesbare runsamenvatting' : 'Bewijs in opbouw'}</h2></div><span className="tiny-tag">READ ONLY</span></div>
      <dl className="provenance-grid"><div><dt>Run-ID</dt><dd className="mono">{s.id}</dd></div><div><dt>Officiële bron / werkelijk transport</dt><dd className="mono">{s.source}</dd></div><div><dt>Geselecteerde slots</dt><dd className="mono">{s.selected_slots ? `[${s.selected_slots.start}, ${s.selected_slots.end_exclusive})` : 'Nog niet geselecteerd · metadata-only'}</dd></div><div><dt>Verworpen telemetriesamples</dt><dd>{s.dropped_samples} · operationeel, geen evidenceverlies uit Raw</dd></div><div><dt>Start acquisitie / simulatie</dt><dd>{when(s.started_at_ms)}</dd></div><div><dt>Laatste publicatie / voltooiing</dt><dd>{s.completed_at_ms === null ? 'Nog niet voltooid' : when(s.completed_at_ms)}</dd></div></dl>
      <p className="domain-boundary">Transacties, Pump-events en ontdekte coins: <strong>nog onbekend — niet gedecodeerd in B4.</strong> Canonieke coinfeiten volgen pas in B5; geen browserdecoder of veronderstelde lancering.</p>
      <div className="artifact-list"><a href="/api/acquisition/runs" target="_blank" rel="noreferrer"><span>Rust-monitorresultaat JSON <b aria-hidden="true">↗</b></span><code>Read-only snapshot, geen nieuw Raw-bewijs</code><small>Operationele projectie · actuele serverrespons</small></a>{s.artifacts.length === 0 ? <p className="muted">Nog geen gepubliceerde receipt of resultaatverwijzing.</p> : s.artifacts.map(artifact => <a key={artifact.id} href={`/api/acquisition/runs/${encodeURIComponent(s.id)}/artifacts/${encodeURIComponent(artifact.id)}`} target="_blank" rel="noreferrer"><span>{artifact.label} <b aria-hidden="true">↗</b></span><code>{artifact.path}</code><small>SHA-256 {artifact.sha256}</small></a>)}</div>
    </section>
  </>;
}

export function AcquisitionMonitor() {
  const [response, setResponse] = useState<MonitorResponse | null>(null);
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('run') ?? '');
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const previous = useRef(new Map<string, MonitorSnapshot>());

  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>; let controller: AbortController;
    async function refresh() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2500);
      try {
        const result = await fetch('/api/acquisition/runs', { method: 'GET', cache: 'no-store', signal: controller.signal });
        if (!result.ok) throw new Error(`Monitor niet bereikbaar (HTTP ${result.status}).`);
        const body = await result.text();
        if (body.length > MAX_MONITOR_RESPONSE_CHARS) throw new Error('Monitorrespons overschrijdt de leesgrens.');
        const next = parseMonitorResponse(JSON.parse(body));
        for (const run of next.runs) {
          if (run.state !== 'READY') continue;
          const old = previous.current.get(run.id); const fresh = run.snapshot;
          if (old && (fresh.updated_at_ms < old.updated_at_ms || (fresh.session_id === old.session_id && fresh.sequence < old.sequence))) {
            throw new Error('Verouderde snapshot geweigerd; laatste bekende metingen blijven zichtbaar.');
          }
        }
        if (active) {
          previous.current.clear();
          next.runs.forEach(run => { if (run.state === 'READY') previous.current.set(run.id, run.snapshot); });
          setResponse(next); setError(null);
        }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : 'Monitorgegevens niet beschikbaar.'); }
      finally { clearTimeout(timeout); if (active) timer = setTimeout(refresh, 700); }
    }
    void refresh();
    const clock = setInterval(() => setNow(Date.now()), 500);
    return () => { active = false; clearTimeout(timer); clearInterval(clock); controller?.abort(); };
  }, []);

  const run = response?.runs.find(item => item.id === selected) ?? response?.runs[0];
  const snapshot = run?.state === 'READY' ? run.snapshot : undefined;
  const stale = Boolean(error) || Boolean(snapshot && snapshot.mode === 'LIVE' && now - snapshot.updated_at_ms > 3000);

  return <div className="acquisition-monitor">
    <header className="topbar"><a className="brand" href="/" aria-label="Solana Quant acquisitiemonitor"><span className="brand-mark" aria-hidden="true">≋</span><strong>SOLANA <span>QUANT</span></strong><span className="brand-divider" />Acquisitie</a><span className="topbar-note">V2 · LOKAAL ONDERZOEK · ALLEEN LEZEN</span></header>
    <main>
      <div className="workspace-heading"><div><p className="eyebrow">DATA-FIRST RESEARCH PLATFORM</p><h2>Acquisitiemonitor</h2><p>Van bronbytes naar controleerbaar bewijs.</p></div><span className={`connection ${error ? 'warn' : ''}`}><span className="status-dot" />{error ? 'Verbinding onderbroken' : response ? 'Lokale monitor verbonden' : 'Verbinden met lokale monitor…'}</span></div>
      {error && <div className="connection-error" role="alert"><strong>{error}</strong>{snapshot && <span>Laatst bekende snapshot — STALE; geen actuele voortgangsclaim.</span>}</div>}
      {response && <nav className="run-selector" aria-label="Run kiezen">{response.runs.map(item => <button type="button" key={item.id} onClick={() => setSelected(item.id)} aria-pressed={item.id === run?.id} className={item.id === run?.id ? 'selected' : ''}><span>{item.state === 'READY' ? kindLabels[item.snapshot.kind] : 'Niet beschikbaar'}</span><strong>{item.state === 'READY' ? item.snapshot.label : item.id}</strong><small>{item.state === 'READY' ? `Epoch ${item.snapshot.epoch} · ${item.snapshot.mode === 'RECORDED' ? 'vastgelegd resultaat' : stageLabels[item.snapshot.stage]}` : item.reason}</small></button>)}</nav>}
      {snapshot ? <RunContent snapshot={snapshot} stale={stale} now={now} /> : <section className="empty-state"><h1>{run?.state === 'UNAVAILABLE' ? 'Rungegevens niet beschikbaar' : response ? 'Nog geen Rust-snapshots' : 'Rust-metingen laden'}</h1><p>{run?.state === 'UNAVAILABLE' ? run.reason : 'Start de read-only import of de expliciete lokale simulatie volgens de startinstructies. De browser start geen downloader.'}</p></section>}
      <footer className="site-footer"><span>Rust meet en bepaalt · de browser observeert</span><span>Geen providerknoppen · geen wallet · geen acquisitietoestemming</span></footer>
    </main>
  </div>;
}
