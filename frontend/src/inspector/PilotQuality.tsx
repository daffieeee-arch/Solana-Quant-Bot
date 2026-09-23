import { useEffect, useState } from 'react';
import { type InputHashes, type Json, type Timeline } from '../../../src/mint-inspector/contract';
import { MAX_PILOT_BYTES, PILOT_INPUT_NAMES, parsePilotQuality, type PilotQuality } from '../../../src/mint-inspector/pilot-quality';
import { readJsonResponse } from './read-response';

const shown = (v: Json | undefined) => v === null || v === undefined ? 'UNAVAILABLE' : typeof v === 'string' ? v : JSON.stringify(v);
function Evidence({ title, value }: { title: string; value: unknown }) {
  return <details className="evidence"><summary>{title}</summary><pre className="raw-record">{JSON.stringify(value, null, 2)}</pre></details>;
}
type MintCounts = Pick<Timeline['counts'], 'transactions' | 'silver_facts' | 'balance_observations'>;
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
    <section className="quality-coverage"><h2>Dekking en ontbrekende informatie</h2>
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
  const [data, setData] = useState<PilotQuality | null>(null), [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const timer = setTimeout(() => controller.abort(), 10000);
    void fetch('/api/pilot-quality', { signal: controller.signal, cache: 'no-store', credentials: 'omit' })
      .then(r => readJsonResponse(r, MAX_PILOT_BYTES)).then(value => parsePilotQuality(value, bindings))
      .then(value => { if (active) setData(value); }).catch(() => { if (active) setFailed(true); }).finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [bindings]);
  if (data) return <PilotQualityView quality={data} mintCounts={mintCounts} />;
  return <section><h1>Datakwaliteit pilot</h1><p role="status">{failed ? 'UNAVAILABLE · Geen geldig geregistreerd pilotrapport beschikbaar. Er worden geen tellingen aangevuld.' : 'Geregistreerd pilotrapport wordt gecontroleerd…'}</p></section>;
}
