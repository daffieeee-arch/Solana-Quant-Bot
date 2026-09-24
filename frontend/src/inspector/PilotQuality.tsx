import { useEffect, useState } from 'react';
import { object, list, type InputHashes, type Timeline } from '../../../src/mint-inspector/contract';
import { MAX_PILOT_BYTES, PILOT_INPUT_NAMES, parsePilotQuality, type PilotQuality } from '../../../src/mint-inspector/pilot-quality';
import { registeredResponse, SnapshotMismatch } from './read-response';

const shown = (v: unknown) => v === null || v === undefined ? 'UNAVAILABLE' : typeof v === 'string' ? v : JSON.stringify(v);
function Evidence({ title, value }: { title: string; value: unknown }) {
  return <details className="evidence"><summary>{title}</summary><pre className="raw-record">{JSON.stringify(value, null, 2)}</pre></details>;
}
type MintCounts = Pick<Timeline['counts'], 'transactions' | 'silver_facts' | 'balance_observations'>;
function Operations({ quality }: { quality: PilotQuality }) {
  const receipts = list(quality.manifest.provenance.selected_receipts).map(object);
  const total = receipts.reduce((sum, r) => sum + BigInt(r.raw_bytes as string), 0n).toString();
  const operations = quality.operations, acquisitions = operations ? list(operations.acquisitions).map(object) : [];
  const report = operations ? object(operations.report) : null;
  return <section className="quality-coverage" aria-label="Operationele bronbytes en klokken"><h2>Bronbytes & operationele klokken</h2>
    <p><strong>{total} Raw-bytes</strong> · Som van de drie unieke geselecteerde payloadreceipts. Geen totaal netwerkverkeer, metadata-/retrykosten of volledige opslagomvang.</p>
    <div className="table-scroll"><table><caption>Oorspronkelijke acquisitiereceipts · tijdstip na ontvangst (Unix ms, UTC)</caption>
      <thead><tr><th>Slot / receipt</th><th>Raw-bytes</th><th>acquired_at.wall_ms</th><th>Receipt-SHA256</th></tr></thead>
      <tbody>{receipts.map((r, i) => <tr key={r.sequence as string}><td>{shown(list(quality.manifest.range.selected_slots)[i])} / {shown(r.sequence)}</td><td>{shown(r.raw_bytes)}</td><td>{shown(acquisitions[i]?.acquired_at_unix_ms)}</td><td><code>{shown(r.sha256)}</code></td></tr>)}</tbody></table></div>
    <p>Downloadduur: <strong>UNAVAILABLE</strong> · Geen expliciet gebonden request-start/eindduur in deze receipts. Ontvangsttijdstippen, decoderklokken en bestandsdatums zijn geen downloadduur.</p>
    {!operations && <p>Acquisitie- en rapportklokken: UNAVAILABLE · De aanvullende operationele receipts zijn niet geregistreerd.</p>}
    <div className="table-scroll"><table><caption>Offline decoder · bestaande operationele UTC-klokken (Unix ms)</caption>
      <thead><tr><th>Batch</th><th>processed_at_unix_ms</th><th>finished_at_unix_ms</th></tr></thead>
      <tbody>{quality.decoders.map(d => <tr key={d.batch_id as string}><td>{shown(d.batch_id)}</td><td>{shown(d.processed_at_unix_ms)}</td><td>{shown(d.finished_at_unix_ms)}</td></tr>)}</tbody></table></div>
    <p>Deze decoderklokken begrenzen de geregistreerde verwerkingsfase, inclusief rapportopbouw, niet de hele proceslooptijd. Een monotone decoderlooptijd is UNAVAILABLE: de receipt registreert UTC-klokken, geen elapsed-counter; klokcorrecties zijn niet uitgesloten.</p>
    <dl><dt>Rapportgeneratie · start UTC</dt><dd>{shown(report?.started_at_utc)}</dd><dt>Rapportgeneratie · einde UTC</dt><dd>{shown(report?.completed_at_utc)}</dd><dt>Gemeten rapportgeneratieduur · seconden</dt><dd>{shown(report?.elapsed_seconds)}</dd></dl>
    <p>Rapportgeneratie betreft het bestaande pilotrapport (Python perf_counter), niet acquisitie, decoderduur of deze browserweergave. Operationele klokken worden niet gebruikt in chainvolgorde, replay, reservegrafieken of handelsstatistieken.</p>
    {operations && <Evidence title="Operationele receiptbindingen en klokprovenance" value={operations} />}
    {operations && <ul>{Object.entries(object(operations.inputs)).map(([name, digest]) => <li key={name}><a href={`/evidence/pilot-${name}.json`} download>{name}.json</a> · <code>{shown(digest)}</code></li>)}</ul>}
  </section>;
}
export function PilotQualityView({ quality, mintCounts }: { quality: PilotQuality; mintCounts: MintCounts }) {
  const m = quality.manifest, c = m.counts, p = m.provenance, r = m.range;
  return <section aria-label="Datakwaliteit drie-slot-pilot">
    <div className="title-row"><div><p className="eyebrow">B6 · BRONGEBONDEN PILOTOVERZICHT</p><h1>Drie slots. De hele pilot.</h1>
      <p className="subtitle">Alle mints in de oorspronkelijke selectie · [{shown(r.start_slot)}, {shown(r.end_slot_exclusive)})</p></div><span className="badge pilot">RESEARCH_SAMPLING</span></div>
    <p className="selection-note">Andere selectie dan het mintdossier: dat toont {mintCounts.transactions} packages, {mintCounts.silver_facts} feiten en {mintCounts.balance_observations} balansobservaties uit pilot én post-hoc context. Die contextslots zijn hier niet opgenomen.</p>
    <div className="stats" aria-label="Geverifieerde pilottellingen">{[
      ['Blokken', c.selected_blocks, 'Oorspronkelijke drie-slot-selectie'], ['Packages', c.atomic_packages, 'Volledige atomaire transacties'],
      ['Mislukte transacties', c.failed_transactions, 'Geen toegelaten Silver-feiten'], ['Silver-feiten', c.silver_facts, 'Bestaande toegelaten feiten'],
    ].map(([label, count, note]) => <div className="stat" key={shown(label)}><span>{shown(label)}</span><strong>{shown(count)}</strong><small>{shown(note)}</small></div>)}</div>
    <div className="boundary"><strong>Research Ready: false</strong><span>Bronklasse is geen onderzoeksgoedkeuring. Creatie, completion, migratie en volledige levensduur blijven onbewezen.</span></div>
    <div className="quality-stages">
      <section><p className="eyebrow">01 · BRONZE-DECODERING</p><h2>{shown(c.decoded)} gedecodeerde packages</h2>
        <p>Dit betreft het transactie-observatiepakket. Het bewijst geen volledige Pump-instructiedekking.</p>
        <dl><dt>QUARANTINED · Bronze</dt><dd>{shown(c.quarantined)}</dd><dt>Unsupported · Bronze</dt><dd>{shown(c.unsupported)}</dd><dt>Missing · Bronze</dt><dd>{shown(c.missing)}</dd></dl>
      </section>
      <section><p className="eyebrow">02 · TRANSACTIESUCCES</p><h2>{shown(c.successful_transactions)} succesvol</h2>
        <p>{shown(c.failed_transactions)} mislukt. Beide blijven evidence in hun atomaire package. Succes bewijst geen Silver-toelating.</p>
        <dl><dt>Top-level instructies</dt><dd>{shown(c.top_level_instructions)}</dd><dt>Opgenomen CPI-instructies</dt><dd>{shown(c.recorded_cpi_instructions)}</dd></dl>
      </section>
      <section><p className="eyebrow">03 · SILVER-TOELATING</p><h2>{shown(c.silver_facts)} toegelaten feiten</h2>
        <p>Begrensde, bestaande decoderprofielen. Afwijzingen zijn geen nulactiviteit; Mayhem blijft afgewezen.</p>
        <dl><dt>Feiten uit mislukte transacties</dt><dd>{shown(c.silver_facts_on_failed_transactions)}</dd><dt>Totaal afgewezen Pump-instructies</dt><dd>UNAVAILABLE</dd></dl>
        <p className="muted">Dit manifest bevat geen volledig afwijzingsinventaris. Een verschil tussen package- en feittellingen is geen afwijzingstelling.</p>
      </section>
    </div>
    <Operations quality={quality} />
    <section className="quality-coverage"><h2>Dekking en ontbrekende informatie</h2>
      {c.missing !== undefined && c.missing !== null && BigInt(c.missing as string) > 0n && <p role="status"><strong>GAP</strong> · {shown(c.missing)} verwachte packages ontbreken binnen {shown(c.transactions)} gedeclareerde transacties. De aanwezige {shown(c.decoded)} gedecodeerde packages vullen dit gat niet.</p>}
      <p>GAP · ontbrekende verwachte slots: <strong>{Array.isArray(r.gap_slots) && r.gap_slots.length === 0 ? 'Geen binnen dit bronbereik' : shown(r.gap_slots)}</strong>.</p>
      <p>UNAVAILABLE · historische accountstaat, feitelijke CPI-rechten en historische programma-activering zijn niet bewezen door dit rapport. QUARANTINED betekent aanwezige maar afgewezen evidence.</p>
      <p>Geen gaps in dit bereik bewijst geen volledige lifecycle of universele Pump-dekking. Eventreserves zijn geen accountstaat. Er worden geen prijzen, fills of rendementen afgeleid.</p>
    </section>
    <section className="provenance"><h2>Bronnen, decoder en writer inspecteren</h2>
      <p>READY · De geregistreerde rapportbytes zijn bij startup gecontroleerd. Deze weergave voert geen nieuwe Raw-/Parquet-verificatie uit. Historische paden zijn alleen provenance.</p>
      <dl className="identity">{PILOT_INPUT_NAMES.map(name => <div key={name}><dt><a href={`/evidence/pilot-${name}.json`} download>pilot-{name}.json</a> · SHA256</dt><dd>{quality.inputs[name]}</dd></div>)}</dl>
      <Evidence title="Bronrange, sample en geselecteerde receipts" value={{ range: r, sample_identity: m.sample_identity, source: p.selected_source, receipts: p.selected_receipts }} />
      <Evidence title="Decoderidentiteit per batch" value={quality.decoders} />
      <Evidence title="Native Rust-writer, instellingen en batchbindingen" value={{ writers: p.writer_identities, batches: p.selected_batches }} />
      <Evidence title="Bestandshashes en logische hashes" value={{ files: m.files, logical_identities: m.logical_identities }} />
      <Evidence title="Rapportcode, collectie en alle bestaande manifestvelden" value={m} />
    </section>
  </section>;
}

export function PilotQualityPanel({ bindings, mintCounts }: { bindings: InputHashes; mintCounts: MintCounts }) {
  const key = `${bindings.collection}:${bindings.plan}`;
  const [result, setResult] = useState<{ key: string; data?: PilotQuality; failure?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const timer = setTimeout(() => controller.abort(), 10000);
    void fetch('/api/pilot-quality', { signal: controller.signal, cache: 'no-store', credentials: 'omit' })
      .then(registeredResponse('pilot-quality', MAX_PILOT_BYTES)).then(value => parsePilotQuality(value, bindings))
      .then(value => { if (active) setResult({ key, data: value }); }).catch(error => { if (active) setResult({ key, failure: error instanceof SnapshotMismatch ? 'STALE' : 'UNAVAILABLE' }); }).finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [bindings, key]);
  const data = result?.key === key ? result.data : undefined, failure = result?.key === key ? result.failure : undefined;
  if (data) return <PilotQualityView quality={data} mintCounts={mintCounts} />;
  return <section><h1>Datakwaliteit pilot</h1><p role="status">{failure ? `${failure} · Geen geldig geregistreerd pilotrapport beschikbaar. Er worden geen tellingen aangevuld.` : 'Geregistreerd pilotrapport wordt gecontroleerd…'}</p></section>;
}
