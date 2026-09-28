import { useEffect, useState } from 'react';
import { InstructionCoveragePanel } from './InstructionCoverage';
import { MAX_COHORT_BYTES, parseDevelopmentCohort, type DevelopmentCohort, type CohortFact } from '../../../src/mint-inspector/development-cohort';
import { object } from '../../../src/mint-inspector/contract';
import { registeredResponse, SnapshotMismatch } from './read-response';

const shown = (v: unknown) => v === null || v === undefined ? 'UNAVAILABLE' : typeof v === 'string' ? v : JSON.stringify(v);
function Evidence({ title, value }: { title: string; value: unknown }) {
  return <details className="evidence"><summary>{title}</summary><pre className="raw-record">{JSON.stringify(value, null, 2)}</pre></details>;
}
export function DevelopmentCohortView({ report }: { report: DevelopmentCohort }) {
  const [ordinal, setOrdinal] = useState(0), [mintIndex, setMintIndex] = useState(0), [selected, setSelected] = useState<string | null>(null);
  const w = report.windows[ordinal], mint = w.mints[mintIndex], s = w.sample_identity;
  const fact = mint?.facts.find(f => f.record_sha256 === selected), packageFacts = fact ? mint.facts.filter(f => f.package_id === fact.package_id) : [];
  function selectMint(n: number) { setMintIndex(n); setSelected(null); }
  function inspect(f: CohortFact) { setSelected(f.record_sha256); }
  return <section className="cohort-view" aria-label="DEVELOPMENT-cohort">
    <div className="title-row"><div><p className="eyebrow">B7 · FASE 1 · BESCHRIJVENDE WAARNEMINGEN</p><h1>Vier vensters. Toegelaten feiten.</h1>
      <p className="subtitle">Vensters zijn de onderzoekseenheid. Geen B8-labels, definitieve recurrence-schatting of sufficiëntieoordeel.</p></div><span className="badge pilot">DEVELOPMENT</span></div>
    <div className="boundary"><strong>Research Ready: false</strong><span>RESERVED_EVALUATION is niet toegankelijk. De oorspronkelijke drie-slot-pilot en het mintdossier zijn andere selecties.</span></div>
    <div className="stats" aria-label="Vier-venstertotalen">{[['Slots', 'blocks'], ['Packages', 'packages'], ['Failures', 'failures'], ['Silver-feiten', 'silver_facts']].map(([label, key]) => <div className="stat" key={key}><span>{label}</span><strong>{report.totals[key]}</strong><small>Alle vier DEVELOPMENT-vensters</small></div>)}</div>
    <div className="cohort-selectors">
      <label>Venster<select value={ordinal} onChange={e => { setOrdinal(Number(e.target.value)); selectMint(0); }}>{report.windows.map((row, i) => <option key={row.ordinal} value={i}>Ordinal {row.ordinal} · [{shown(row.sample_identity.start_slot)}, {shown(row.sample_identity.end_slot_exclusive)})</option>)}</select></label>
      <label>Mint<select value={mintIndex} onChange={e => selectMint(Number(e.target.value))} disabled={!w.mints.length}>{w.mints.map((row, i) => <option key={row.mint} value={i}>{row.mint}{row.observed_pair ? ' · paar aangetoond' : ''}</option>)}</select></label>
    </div>
    <p>Geselecteerd: ordinal {w.ordinal} · RESEARCH_SAMPLING / DEVELOPMENT · [{shown(s.start_slot)}, {shown(s.end_slot_exclusive)}). De vensterlijst volgt de vastgelegde selectierang, niet acquisitietijd of historische chronologie.</p>
    <div className="table-scroll"><table><caption>Geselecteerd venster · gehele 16-slot-selectie</caption><thead><tr><th>Slots</th><th>Packages</th><th>Failures</th><th>Silver-feiten</th><th>Unieke toegelaten mints</th><th>Mints met aangetoond paar</th></tr></thead><tbody><tr><td>{w.counts.blocks}</td><td>{w.counts.packages}</td><td>{w.counts.failures}</td><td>{w.counts.silver_facts}</td><td>{w.mint_count}</td><td>{w.observed_pair_mints}</td></tr></tbody></table></div>
    <div className="table-scroll"><table><caption>Toegelaten feiten per helft · grensslot {w.midpoint_slot} hoort bij de laatste helft</caption><thead><tr><th>Selectie</th><th>Buys</th><th>Sells</th><th>Unieke mints</th></tr></thead><tbody>{(['FIRST_8', 'LAST_8'] as const).map(h => <tr key={h}><td>{h === 'FIRST_8' ? 'Eerste acht slots' : 'Laatste acht slots'}</td><td>{w.halves[h].buy_facts}</td><td>{w.halves[h].sell_facts}</td><td>{w.halves[h].unique_mints}</td></tr>)}</tbody></table></div>
    <section aria-label="Feiten van geselecteerde mint"><h2>Geselecteerde mint</h2><p className="cohort-mint">{mint?.mint ?? 'UNAVAILABLE · Geen toegelaten mintfeiten'}</p>
      {mint && <><p><strong>{mint.pair_state}</strong> · Een paar vereist een buy in de eerste helft en sell in de tweede helft, uit twee verschillende atomaire packages. De mint telt maximaal eenmaal per venster.</p>
      {mint.observed_pair && <div className="tags">{[mint.observed_pair.buy_fact_sha256, mint.observed_pair.sell_fact_sha256].map(h => <button key={h} onClick={() => setSelected(h)} aria-controls="cohort-source">Inspecteer {h === mint.observed_pair?.buy_fact_sha256 ? 'buy' : 'sell'} van het paar</button>)}</div>}
      <p>Geen aangetoond paar betekent alleen geen paar in de toegelaten feiten. Onvolledige semantische Pump-dekking verhindert een negatieve conclusie over werkelijke activiteit.</p>
      <div className="table-scroll"><table><caption>Bestaande Silver-feiten · canonieke chainvolgorde binnen deze mint en dit venster</caption><thead><tr><th>Chainpositie</th><th>Feit / helft</th><th>Token raw u64</th><th>Quote raw u64 / identiteit</th><th>Broninspectie</th></tr></thead><tbody>{mint.facts.map(f => { const event = object(f.record.event_reported); return <tr key={f.record_sha256} className={f.package_id === fact?.package_id ? 'current-trade' : ''} data-fact-hash={f.record_sha256}>
        <td>{f.slot} / tx {f.transaction_index}<small>Instructiepositie: {shown(object(f.record.event_context).selected_invocation ?? object(f.record.event_context).parent)}</small></td><td>{f.side} · {f.half}</td><td>{shown(event.token_amount_raw_u64)}</td><td>{shown(event.quote_amount_raw_u64)}<small>Quote: {shown(f.record.quote_mint_identity)} · decimals: {shown(f.record.quote_decimals)}</small></td><td><button aria-controls="cohort-source" onClick={() => inspect(f)}>Inspecteer feit {f.record_sha256.slice(0, 8)}</button></td></tr>; })}</tbody></table></div></>}
      <article id="cohort-source" aria-label="Geselecteerde cohortbron">
        {fact ? <><h3>Atomair package · broninspectie</h3><p className="cohort-mint">{fact.package_id}</p><p>Alle {packageFacts.length} toegelaten feiten van deze mint in dit package blijven samen. Dit overzicht is geen volledige Bronze-transactieweergave.</p>
          <Evidence title="Feiten en oorspronkelijke feithashes in dit package" value={packageFacts} />
          <Evidence title="Bron-, receipt- en samplebindingen" value={{ source: fact.record.source, receipt_evidence: fact.record.receipt_evidence, sample_identity: w.sample_identity, collection_sha256: w.collection_sha256, bronze_record_sha256: fact.record.bronze_record_sha256 }} />
          <Evidence title="Oorspronkelijke decoder, writer, bestandshashes en deelmanifest" value={w.parts[Number(fact.part_id)]} />
        </> : <p>Selecteer een feit of paar om de bronbindingen te inspecteren. Er wordt niets buiten de geregistreerde snapshot geopend.</p>}
      </article>
    </section>
    <InstructionCoveragePanel cohort={report} ordinal={ordinal} />
    <section className="quality-coverage"><h2>Dekking, diagnoses en onbekenden</h2>
      <p>Bronze-decodering, transactiesucces en Silver-toelating zijn afzonderlijke stappen. Failures blijven evidence en leveren geen toegelaten handelsfeiten. Nul ontbrekende Raw-slots bewijst geen volledige Pump-dekking.</p>
      <p><strong>UNPROVEN</strong> · Volledige semantische dekking. <strong>GAP</strong> betreft ontbrekende verwachte brondekking; <strong>QUARANTINED</strong> betreft aanwezige maar afgewezen evidence. <strong>UNAVAILABLE</strong> betekent onbekend, nooit nul.</p>
      <div className="table-scroll"><table><caption>Native slotuitkomsten · packages en diagnoses worden niet bij Silver opgeteld</caption><thead><tr><th>Slot</th><th>Staat</th><th>Packages</th><th>Failures</th><th>Bronze-disposities</th><th>Silver</th></tr></thead><tbody>{w.slot_outcomes.map(row => <tr key={shown(row.slot)}><td>{shown(row.slot)}</td><td>{shown(row.state)}</td><td>{shown(row.transaction_envelopes)}</td><td>{shown(row.transaction_status_counts && object(row.transaction_status_counts).ERROR)}</td><td>{shown(row.dispositions)}</td><td>{shown(row.silver_fact_count)}</td></tr>)}</tbody></table></div>
      <Evidence title="Slotdekking en expliciete ontbrekende informatie" value={{ coverage: w.coverage, slots: w.slot_outcomes, semantic: w.semantic_coverage }} />
      <Evidence title="Bestaande decoderdiagnoses met hun eigen noemer" value={{ outcomes: w.pump_layout_outcomes, denominator: w.diagnosis_denominator }} />
      <p>Diagnoses zijn geen unieke transacties of automatisch unieke instructies. Ontbrekende diagnoses worden niet afgeleid uit een verschil tussen packages en feiten.</p>
      <ul>{w.missing_information.map(reason => <li key={reason}>{reason}</li>)}</ul>
    </section>
    <section className="provenance"><h2>Vaste selectie & reproduceerbaarheid</h2><a href="/evidence/development-cohort.json" download>Download reproduceerbare DEVELOPMENT-JSON</a>
      <Evidence title="Collectie-, logische hashes en producentidentiteit" value={{ selection_sha256: report.selection_sha256, pins_sha256: report.pins_sha256, native_admission_sha256: report.native_admission_sha256, producer: report.producer, collection_sha256: w.collection_sha256, collection_path: w.collection_path, sample_identity: w.sample_identity, layers: w.layers }} />
      <p>READY · Gecontroleerde, onveranderlijke output; operationele klokken bepalen geen historische tijdlijn. Deze pagina berekent geen handelsstatistieken en opent geen providers of willekeurige bestanden.</p>
    </section>
  </section>;
}
export function DevelopmentCohortPanel() {
  const [result, setResult] = useState<DevelopmentCohort | null>(null), [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const timer = setTimeout(() => controller.abort(), 10000);
    void fetch('/api/development-cohort', { signal: controller.signal, cache: 'no-store', credentials: 'omit' })
      .then(registeredResponse('development-cohort', MAX_COHORT_BYTES)).then(parseDevelopmentCohort)
      .then(value => { if (active) setResult(value); }).catch(error => { if (active) setFailure(error instanceof SnapshotMismatch ? 'STALE' : 'UNAVAILABLE'); }).finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, []);
  if (result) return <DevelopmentCohortView report={result} />;
  return <section><h1>DEVELOPMENT-cohort</h1><p role="status">{failure ? `${failure} · Geen geldig geregistreerd rapport; geen aangevulde tellingen of alternatieve toegang.` : 'Geregistreerd cohort wordt gecontroleerd…'}</p></section>;
}
