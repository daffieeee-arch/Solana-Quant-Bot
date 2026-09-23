import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { INPUT_NAMES, MAX_RESPONSE_BYTES, parseInspection, type Inspection, type Json, type Package } from '../../../src/mint-inspector/contract';

import { PilotQualityPanel } from './PilotQuality';
import { readJsonResponse } from './read-response';

export type ViewState = 'READY' | 'STALE' | 'GAP' | 'REPLAYING' | 'UNAVAILABLE' | 'UNPROVEN' | 'QUARANTINED';
const stateText: Record<ViewState, string> = {
  READY: 'Geregistreerd rapport beschikbaar. Dit zegt niets over Research Ready.',
  STALE: 'Verouderde weergave; gebruik deze niet als actuele evidence.',
  GAP: 'Verwachte brondekking ontbreekt. Ontbrekend is geen nulactiviteit.',
  REPLAYING: 'Verwerking niet afgerond; er worden geen gedeeltelijke packages getoond.',
  UNAVAILABLE: 'Rapport niet beschikbaar. Er worden geen tellingen aangevuld.',
  UNPROVEN: 'Onvoldoende bewijs voor deze eigenschap.',
  QUARANTINED: 'Evidence aanwezig, maar niet betrouwbaar toegelaten.',
};
export function StateNotice({ state }: { state: ViewState }) {
  return <p className={`state-notice state-${state.toLowerCase()}`} role="status"><strong>{state}</strong> {stateText[state]}</p>;
}
const shown = (value: Json | undefined): string => value === null ? 'Niet ingevuld (null)'
  : value === undefined ? 'Niet beschikbaar' : typeof value === 'string' ? value : JSON.stringify(value);
function Raw({ value }: { value: unknown }) { return <pre className="raw-record">{JSON.stringify(value, null, 2)}</pre>; }
function Evidence({ title, value }: { title: string; value: unknown }) {
  return <details className="evidence"><summary>{title}</summary><Raw value={value} /></details>;
}
function Badge({ children, tone = '' }: { children: React.ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
const pilot = (p: Package) => p.collection_role === 'ORIGINAL_SELECTION';

function PackageDetails({ p, inspection }: { p: Package; inspection: Inspection }) {
  const batch = inspection.timeline.bindings.batches.find(b => b.batch_id === p.collection_batch_id && b.source_id === p.collection_source_id);
  return <article id="selected-package" className="package-detail" aria-label="Geselecteerd atomair package" key={p.package_id}>
    <header className="package-header">
      <div><p className="eyebrow">CHAINPOSITIE · GEEN KLOKTIJD</p><h2>Slot {p.slot} <span>/ tx {p.transaction_index}</span></h2></div>
      <Badge tone={p.transaction_status === 'ERROR' ? 'failure' : 'success'}>{p.transaction_status}</Badge>
    </header>
    <div className="tags"><Badge tone={pilot(p) ? 'pilot' : 'context'}>{pilot(p) ? 'Pilot · RESEARCH_SAMPLING' : 'Post-hoc context · ENGINEERING_VALIDATION_ONLY'}</Badge><Badge>Atomair package</Badge></div>
    <p className="package-note">Instructies, CPI’s en metadata blijven samen. Een mintverwijzing bewijst geen trade of accountrol.</p>
    <dl className="identity"><dt>Package</dt><dd>{p.package_id}</dd><dt>Signatuur</dt><dd>{p.signatures.join('\n') || 'Niet beschikbaar'}</dd></dl>
    {p.transaction_status === 'ERROR' && <div className="failure-note"><strong>Mislukte transactie · geen Silver-feiten</strong><Raw value={p.transaction_error} /></div>}
    <Evidence title="Opnamegronden en chainpositie" value={{ inclusion: p.inclusion, effective_at: p.effective_at, bronze_disposition: p.bronze_disposition, bronze_reason: p.bronze_reason }} />

    <details className="section-detail" open><summary>Handelsfeiten <span>{p.silver_facts.length}</span></summary>
      <p className="muted">Bestaande instruction/event-feiten. Eventvelden zijn geen historische accountstaat, prijs of fill.</p>
      {p.silver_facts.length === 0 && <p>Geen toegelaten mintfeit in dit package. Dit bewijst geen nulactiviteit.</p>}
      {p.silver_facts.map(f => <div className="trade" key={f.record_sha256}>
        <div className="trade-heading"><Badge tone={f.record.event_reported.is_buy ? 'buy' : 'sell'}>{f.record.event_reported.is_buy ? 'Buy' : 'Sell'}</Badge><span>{f.record.schema}</span></div>
        <dl className="values"><dt>Tokenhoeveelheid · event raw u64</dt><dd>{shown(f.record.event_reported.token_amount_raw_u64)}</dd>
          <dt>Quotehoeveelheid · event raw u64</dt><dd>{shown(f.record.event_reported.quote_amount_raw_u64)}</dd>
          <dt>Quote-eenheid</dt><dd>{shown(f.record.quote_mint_identity)}</dd>
          <dt>Feithash</dt><dd>{f.record_sha256}</dd></dl>
        <Evidence title="Transactiebrede balanscontext en bestaande delta’s" value={f.record.token_balance_context} />
        <Evidence title="Volledig bestaand Silver-record" value={f.record} />
      </div>)}
    </details>

    <details className="section-detail"><summary>Balansobservaties <span>{p.balance_observations.length}</span></summary>
      <p className="muted">Opgenomen pre/post-metadata, in oorspronkelijke lijstvolgorde. Geen berekende of instructiegebonden delta.</p>
      <div className="table-scroll"><table><thead><tr><th>Index</th><th>Zijde</th><th>Accountindex</th><th>Raw integer</th><th>Decimals</th><th>Bestaande decimale tekst</th></tr></thead>
        <tbody>{p.balance_observations.map(o => <tr key={o.observation_index}><td>{o.observation_index}</td><td>{shown(o.observation.side)}</td><td>{shown(o.observation.account_index)}</td><td>{shown(o.observation.amount_u64)}</td><td>{shown(o.observation.decimals)}</td><td>{shown(o.observation.exact_decimal_amount)}</td></tr>)}</tbody></table></div>
      <Evidence title="Volledige balansobservaties, inclusief aanwezigheid en onbekenden" value={p.balance_observations} />
    </details>

    <details className="section-detail"><summary>Diagnoses en afwijzingen <span>{p.diagnostics.length}</span></summary>
      <p className="muted">Verschillende diagnoses kunnen dezelfde instructie betreffen. UNKNOWN-minttoewijzing blijft onbekend.</p>
      {p.diagnostics.map((d, i) => <div className="diagnosis" key={`${shown(d.json_pointer)}:${i}`}>
        <div className="tags"><Badge>{shown(d.kind)}</Badge><Badge>{shown(d.mint_attribution)}</Badge></div>
        <p>Outer {shown(d.outer_index)} · inner {shown(d.inner_order)} · {shown(d.disposition ?? d.layout_outcome)}</p>
        <Evidence title={`Diagnose ${i + 1} · bestaande velden`} value={d} />
      </div>)}
      <Evidence title="Beschikbaarheid per diagnoselijn" value={p.diagnostic_inventory} />
    </details>
    <details className="section-detail"><summary>Instructies en CPI-referenties <span>{p.instructions.length}</span></summary>
      <p className="muted">Bestaande chainvolgorde. Een verklaarde instructie of opgenomen CPI bewijst niet alle uitvoeringsrechten.</p>
      <Raw value={{ inventory: p.instruction_inventory, instructions: p.instructions }} />
    </details>
    <details className="section-detail"><summary>Bron-, receipt- en manifestbindingen</summary>
      <p className="muted">Historische paden zijn provenance. De viewer opent deze paden niet. Hashcontrole van de rapporten herhaalt geen Raw-decode.</p>
      <Raw value={{ bronze_record_sha256: p.bronze_record_sha256, decoder_source_sha256: p.decoder_source_sha256, source: p.source,
        receipt: p.receipt, sample_identity: p.sample_identity, batch, collection_sha256: inspection.inputs.collection }} />
    </details>
  </article>;
}

export function InspectionView({ inspection }: { inspection: Inspection }) {
  const [index, setIndex] = useState(0);
  const [view, setView] = useState<'mint' | 'pilot'>('mint');
  const [playing, setPlaying] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const t = inspection.timeline, p = t.transactions[index];
  const last = t.transactions.length - 1;
  function pause() {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    setPlaying(false);
  }
  const move = (next: number) => { pause(); setIndex(Math.max(0, Math.min(last, next))); };
  const workspace = (next: 'mint' | 'pilot') => { pause(); setView(next); };
  useEffect(() => {
    if (!playing || view !== 'mint' || index >= last) return;
    // One presentation step per committed package; never catch up using wall-clock time.
    let active = true;
    timer.current = setTimeout(() => {
      if (!active) return;
      timer.current = null;
      setIndex(index + 1);
      if (index + 1 === last) setPlaying(false);
    }, 2000);
    return () => { active = false; if (timer.current !== null) clearTimeout(timer.current); timer.current = null; };
  }, [playing, view, index, last]);
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || (event.target as HTMLElement).closest('input,textarea,select,[contenteditable]')) return;
    const next = event.key === 'ArrowLeft' ? index - 1 : event.key === 'ArrowRight' ? index + 1
      : event.key === 'Home' ? 0 : event.key === 'End' ? t.transactions.length - 1 : null;
    if (next !== null) { event.preventDefault(); move(next); }
  }
  return <>
    <header className="topbar"><span className="brand-mark">SQ</span><span>Solana Quant <strong>V2</strong></span><span className="local-only">LOKAAL · ALLEEN LEZEN</span></header>
    <main>
      <nav className="workspace-nav" aria-label="Inspectieweergave"><button aria-pressed={view === 'mint'} onClick={() => workspace('mint')}>Mintdossier</button><button aria-pressed={view === 'pilot'} onClick={() => workspace('pilot')}>Datakwaliteit pilot</button></nav>
      {view === 'pilot' ? <PilotQualityPanel key={`${inspection.inputs.collection}:${inspection.inputs.plan}`} bindings={inspection.inputs} mintCounts={t.counts} /> : <>
      <div className="title-row"><div><p className="eyebrow">B6 · GEDEELTELIJKE LOKALE ONTWIKKELING</p><h1>Een mint. {t.counts.transactions} packages.</h1><p className="subtitle">Een controleerbaar lifecyclefragment uit bestaande OF1-evidence.</p></div><Badge tone="context">Post-hoc beschrijvend</Badge></div>
      <div className="mint-line"><span>MINT</span><code>{t.mint}</code></div>
      <p className="totals-caption">Volledig dossier · tellingen onafhankelijk van de replaypositie</p>
      <div className="stats" aria-label="Geverifieerde selectietellingen">
        {[['Packages', t.counts.transactions, `${t.counts.by_role.ORIGINAL_SELECTION?.transactions ?? 'UNAVAILABLE'} pilot · ${t.counts.by_role.POSTHOC_DESCRIPTIVE_CONTEXT?.transactions ?? 'UNAVAILABLE'} context`], ['Silver-feiten', t.counts.silver_facts, `${shown(t.counts.buys)} buys · ${shown(t.counts.sells)} sells`], ['Balansobservaties', t.counts.balance_observations, `${t.counts.by_role.ORIGINAL_SELECTION?.balance_observations ?? 'UNAVAILABLE'} pilot · ${t.counts.by_role.POSTHOC_DESCRIPTIVE_CONTEXT?.balance_observations ?? 'UNAVAILABLE'} context`], ['Mislukte packages', t.counts.status.ERROR, 'Zonder Silver-feiten']].map(([label, n, note]) => <div className="stat" key={label}><span>{label}</span><strong>{n}</strong><small>{note}</small></div>)}
      </div>
      <div className="boundary"><strong>Research Ready: false</strong><span>Creatie, completion, migratie en volledige levensduur blijven onbewezen. Eerste en laatste waarneming zijn geen begin of einde.</span></div>
      <details className="lifecycle"><summary>Lifecyclefasen en bewijsgrenzen</summary><div className="phase-grid">{inspection.lifecycle.phases.map(phase => <section key={phase.phase}><h3>{phase.phase}</h3><Badge>{phase.status}</Badge><p>{phase.evidence}</p><p className="muted">{phase.limit}</p></section>)}</div></details>

      <section className="inspector" onKeyDown={keys} aria-label="Mintpackages in chainvolgorde" tabIndex={0}>
        <div className="inspector-toolbar"><div><h2>Chronologische tijdlijn</h2><p>← → Vorige / volgende · Home / End · Handmatige selectie pauzeert</p></div>
          <nav aria-label="Package kiezen"><button onClick={() => move(index - 1)} disabled={index === 0}>← Vorige</button><output aria-label="Geselecteerde replaypositie" aria-live="polite">{index + 1} / {t.transactions.length}</output><button onClick={() => move(index + 1)} disabled={index >= last}>Volgende →</button></nav>
          <div className="playback-controls"><button onClick={() => playing ? pause() : setPlaying(true)} disabled={index >= last}>{playing ? 'Pauzeren' : 'Afspelen'}</button><button onClick={() => move(0)}>Opnieuw beginnen</button><span role="status">{playing ? 'Speelt af' : index === last ? 'Einde fragment' : 'Gepauzeerd'}</span></div>
        </div>
        <p className="playback-note">Presentatietempo: één heel package per 2 seconden. Geen historische latency, informatiebeschikbaarheid of uitvoerbare handelsmogelijkheid.</p>
        <p className="list-caption">VOLLEDIGE SELECTIE · CHAINVOLGORDE · GEEN TIJDSCHAAL</p>
        <ol className="package-list" aria-label="Volledige packagetijdlijn">{t.transactions.map((row, i) => <li key={row.package_id}><button className={i === index ? 'selected' : ''} aria-current={i === index ? 'true' : undefined} aria-controls="selected-package" onClick={() => move(i)}>
          <span className="row-index">{String(i + 1).padStart(2, '0')}</span><span className="row-main"><strong>{row.slot} <span>/ {row.transaction_index}</span></strong><small>{pilot(row) ? 'Pilot' : 'Post-hoc context'} · {row.transaction_status} · {row.silver_facts.length} feiten</small></span></button></li>)}</ol>
        <section className="trade-overview" aria-label="Handelstabel volledig dossier"><h2>Handelsfeiten · volledig dossier ({t.counts.silver_facts})</h2>
          <p className="muted">Oorspronkelijke eventhoeveelheden, geen fills of transactiebrede balansdelta’s. Raw integers worden niet omgerekend. Quote-eenheden blijven onbekend waar de bron dat aangeeft.</p>
          <div className="table-scroll"><table><caption className="sr-only">Bestaande Silver-feiten in packagevolgorde; selecteer een package voor alle feiten en bronbindingen.</caption><thead><tr><th>Feit</th><th>Slot / transactie</th><th>Token · raw u64</th><th>Quote · raw u64</th><th>Geregistreerde eenheden</th><th>Package</th></tr></thead>
            <tbody>{t.transactions.flatMap((row, i) => row.silver_facts.map(f => <tr key={f.record_sha256} data-fact-hash={f.record_sha256} className={index === i ? 'current-trade' : ''}>
              <td><Badge tone={f.record.event_reported.is_buy ? 'buy' : 'sell'}>{f.record.event_reported.is_buy ? 'Buy' : 'Sell'}</Badge></td>
              <td>{row.slot} / {row.transaction_index}<small>{pilot(row) ? 'Pilot' : 'Post-hoc context'}</small></td>
              <td>{shown(f.record.event_reported.token_amount_raw_u64)}</td><td>{shown(f.record.event_reported.quote_amount_raw_u64)}</td>
              <td><small>Token-decimals (balanscontext): {shown(f.record.token_balance_context?.decimals)}</small><small>Quote: {shown(f.record.quote_mint_identity)} · decimals: {shown(f.record.quote_decimals)}</small></td>
              <td><button aria-controls="selected-package" onClick={() => move(i)}>Package {String(i + 1).padStart(2, '0')}</button></td>
            </tr>))}</tbody></table></div>
        </section>
        {p ? <PackageDetails key={p.package_id} p={p} inspection={inspection} /> : <StateNotice state="UNAVAILABLE" />}

      </section>
      <section className="provenance"><h2>Provenance & beschikbaarheid</h2><StateNotice state="READY" />
        <p>Deze pagina leest een bij startup geverifieerde, onveranderlijke rapportsnapshot. Geen polling, providerverkeer of nieuwe toelating.</p>
        <dl className="identity">{INPUT_NAMES.map(name => <div key={name}><dt><a href={`/evidence/${name}.json`} download>{name}.json</a> · SHA256</dt><dd>{inspection.inputs[name]}</dd></div>)}</dl>
        <Evidence title="Collectiedekking, bronklassen en bestaande laagtellingen" value={t.collection} />
        <Evidence title="Selectiequery, code-identiteit en manifestbindingen" value={t.bindings} />
        <ul>{t.limits.map(limit => <li key={limit}>{limit}</li>)}</ul>
        <p>GAP = ontbrekende verwachte brondekking · UNAVAILABLE = niet beschikbaar · QUARANTINED = aanwezig maar afgewezen. Ontbrekende dekking is geen nulactiviteit.</p>
      </section>
      </>}
    </main><footer>Rust-geautoriseerde feiten · Geen browserdecode · Geen prijs-, fill- of rendementsclaim</footer>
  </>;
}

export function MintInspector() {
  const [data, setData] = useState<Inspection | null>(null), [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const timer = setTimeout(() => controller.abort(), 10000);
    void fetch('/api/inspection', { signal: controller.signal, cache: 'no-store', credentials: 'omit' }).then(r => readJsonResponse(r, MAX_RESPONSE_BYTES)).then(parseInspection)
      .then(value => { if (active) setData(value); }).catch(() => { if (active) setFailed(true); }).finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, []);
  if (data) return <InspectionView inspection={data} />;
  return <main className="loading"><h1>Mintinspecteur</h1>{failed ? <StateNotice state="UNAVAILABLE" /> : <p role="status">Geregistreerd rapport wordt gecontroleerd…</p>}</main>;
}
