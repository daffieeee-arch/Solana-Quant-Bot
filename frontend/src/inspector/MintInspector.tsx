import { useEffect, useState, type KeyboardEvent } from 'react';
import { INPUT_NAMES, MAX_RESPONSE_BYTES, parseInspection, type Inspection, type Json, type Package } from '../../../src/mint-inspector/contract';

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
  return <article className="package-detail" aria-label="Geselecteerd atomair package" key={p.package_id}>
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
  const t = inspection.timeline, p = t.transactions[index];
  const move = (next: number) => setIndex(Math.max(0, Math.min(t.transactions.length - 1, next)));
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || (event.target as HTMLElement).closest('input,textarea,select,[contenteditable]')) return;
    const next = event.key === 'ArrowLeft' ? index - 1 : event.key === 'ArrowRight' ? index + 1
      : event.key === 'Home' ? 0 : event.key === 'End' ? t.transactions.length - 1 : null;
    if (next !== null) { event.preventDefault(); move(next); }
  }
  return <>
    <header className="topbar"><span className="brand-mark">SQ</span><span>Solana Quant <strong>V2</strong></span><span className="local-only">LOKAAL · ALLEEN LEZEN</span></header>
    <main>
      <div className="title-row"><div><p className="eyebrow">B6 · GEDEELTELIJKE LOKALE ONTWIKKELING</p><h1>Een mint. {t.counts.transactions} packages.</h1><p className="subtitle">Een controleerbaar lifecyclefragment uit bestaande OF1-evidence.</p></div><Badge tone="context">Post-hoc beschrijvend</Badge></div>
      <div className="mint-line"><span>MINT</span><code>{t.mint}</code></div>
      <div className="stats" aria-label="Geverifieerde selectietellingen">
        {[['Packages', t.counts.transactions, `${t.counts.by_role.ORIGINAL_SELECTION?.transactions ?? 'UNAVAILABLE'} pilot · ${t.counts.by_role.POSTHOC_DESCRIPTIVE_CONTEXT?.transactions ?? 'UNAVAILABLE'} context`], ['Silver-feiten', t.counts.silver_facts, `${shown(t.counts.buys)} buys · ${shown(t.counts.sells)} sells`], ['Balansobservaties', t.counts.balance_observations, `${t.counts.by_role.ORIGINAL_SELECTION?.balance_observations ?? 'UNAVAILABLE'} pilot · ${t.counts.by_role.POSTHOC_DESCRIPTIVE_CONTEXT?.balance_observations ?? 'UNAVAILABLE'} context`], ['Mislukte packages', t.counts.status.ERROR, 'Zonder Silver-feiten']].map(([label, n, note]) => <div className="stat" key={label}><span>{label}</span><strong>{n}</strong><small>{note}</small></div>)}
      </div>
      <div className="boundary"><strong>Research Ready: false</strong><span>Creatie, completion, migratie en volledige levensduur blijven onbewezen. Eerste en laatste waarneming zijn geen begin of einde.</span></div>
      <details className="lifecycle"><summary>Lifecyclefasen en bewijsgrenzen</summary><div className="phase-grid">{inspection.lifecycle.phases.map(phase => <section key={phase.phase}><h3>{phase.phase}</h3><Badge>{phase.status}</Badge><p>{phase.evidence}</p><p className="muted">{phase.limit}</p></section>)}</div></details>

      <section className="inspector" onKeyDown={keys} aria-label="Mintpackages in chainvolgorde" tabIndex={0}>
        <div className="inspector-toolbar"><div><h2>Transactiepackages</h2><p>← → Vorige / volgende · Home / End · Enter of spatie klapt details open</p></div>
          <nav aria-label="Package kiezen"><button onClick={() => move(index - 1)} disabled={index === 0}>← Vorige</button><output aria-live="polite">{index + 1} / {t.transactions.length}</output><button onClick={() => move(index + 1)} disabled={index === t.transactions.length - 1}>Volgende →</button></nav>
        </div>
        <div className="inspection-grid"><aside><p className="list-caption">COMPLETE SELECTIE · CHAINVOLGORDE</p><ol className="package-list">{t.transactions.map((row, i) => <li key={row.package_id}><button className={i === index ? 'selected' : ''} aria-current={i === index ? 'true' : undefined} onClick={() => move(i)}>
          <span className="row-index">{String(i + 1).padStart(2, '0')}</span><span className="row-main"><strong>{row.slot} <span>/ {row.transaction_index}</span></strong><small>{pilot(row) ? 'Pilot' : 'Post-hoc context'} · {row.silver_facts.length} feiten</small></span><span className={`status-dot ${row.transaction_status === 'ERROR' ? 'error' : ''}`} aria-label={row.transaction_status} /></button></li>)}</ol></aside>
          {p ? <PackageDetails key={p.package_id} p={p} inspection={inspection} /> : <StateNotice state="UNAVAILABLE" />}
        </div>
      </section>
      <section className="provenance"><h2>Provenance & beschikbaarheid</h2><StateNotice state="READY" />
        <p>Deze pagina leest een bij startup geverifieerde, onveranderlijke rapportsnapshot. Geen polling, providerverkeer of nieuwe toelating.</p>
        <dl className="identity">{INPUT_NAMES.map(name => <div key={name}><dt><a href={`/evidence/${name}.json`} download>{name}.json</a> · SHA256</dt><dd>{inspection.inputs[name]}</dd></div>)}</dl>
        <Evidence title="Collectiedekking, bronklassen en bestaande laagtellingen" value={t.collection} />
        <Evidence title="Selectiequery, code-identiteit en manifestbindingen" value={t.bindings} />
        <ul>{t.limits.map(limit => <li key={limit}>{limit}</li>)}</ul>
        <p>GAP = ontbrekende verwachte brondekking · UNAVAILABLE = niet beschikbaar · QUARANTINED = aanwezig maar afgewezen. Ontbrekende dekking is geen nulactiviteit.</p>
      </section>
    </main><footer>Rust-geautoriseerde feiten · Geen browserdecode · Geen prijs-, fill- of rendementsclaim</footer>
  </>;
}

async function readResponse(response: Response): Promise<Inspection> {
  if (!response.ok || !response.body) throw new Error('Unavailable');
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try {
    for (;;) { const result = await reader.read(); if (result.done) break;
      size += result.value.byteLength; if (size > MAX_RESPONSE_BYTES) throw new Error('Response limit'); chunks.push(result.value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return parseInspection(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)));
}
export function MintInspector() {
  const [data, setData] = useState<Inspection | null>(null), [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const timer = setTimeout(() => controller.abort(), 10000);
    void fetch('/api/inspection', { signal: controller.signal, cache: 'no-store', credentials: 'omit' }).then(readResponse)
      .then(value => { if (active) setData(value); }).catch(() => { if (active) setFailed(true); }).finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, []);
  if (data) return <InspectionView inspection={data} />;
  return <main className="loading"><h1>Mintinspecteur</h1>{failed ? <StateNotice state="UNAVAILABLE" /> : <p role="status">Geregistreerd rapport wordt gecontroleerd…</p>}</main>;
}
