import { useEffect, useState } from 'react';
import { MAX_RESPONSE_BYTES, type Inspection } from '../../../src/mint-inspector/contract';
import { FLOW_GROUPS, parseMintFlow, type FlowGroup, type FlowTotals, type MintFlow } from '../../../src/mint-inspector/mint-flow';
import { registeredResponse, SnapshotMismatch } from './read-response';

const labels: Record<FlowGroup, string> = { ALL: 'Gecombineerd · post-hoc beschrijvend',
  ORIGINAL_SELECTION: 'Pilot · RESEARCH_SAMPLING', POSTHOC_DESCRIPTIVE_CONTEXT: 'Context · ENGINEERING_VALIDATION_ONLY' };
function Totals({ title, groups }: { title: string; groups: Record<FlowGroup, FlowTotals> }) {
  return <div className="table-scroll"><table><caption>{title}</caption><thead><tr><th>Selectie</th><th>Koop · raw</th><th>Verkoop · raw</th><th>Bruto · raw</th><th>Koop − verkoop · raw</th><th>Unieke event-users</th><th>Buy / sell-feiten</th></tr></thead>
    <tbody>{FLOW_GROUPS.map(group => <tr key={group} data-flow-group={group}><th scope="row">{labels[group]}</th>
      <td>{groups[group].buy_token_raw}</td><td>{groups[group].sell_token_raw}</td><td>{groups[group].gross_token_raw}</td><td>{groups[group].net_token_raw}</td>
      <td>{groups[group].unique_event_users}</td><td>{groups[group].buy_facts} / {groups[group].sell_facts}</td></tr>)}</tbody></table></div>;
}

export function MintFlowView({ flow, selectedIndex, onSelect }: { flow: MintFlow; selectedIndex: number; onSelect: (index: number) => void }) {
  const [group, setGroup] = useState<FlowGroup>('ALL');
  const r = flow.report, current = r.packages[selectedIndex];
  const values = r.packages.map(p => BigInt(p.cumulative[group].net_token_raw));
  const low = values.reduce((a, b) => a < b ? a : b, 0n), high = values.reduce((a, b) => a > b ? a : b, 0n);
  // Scale precomputed signed values to pixels only. Never sum facts in the UI.
  const y = (value: bigint) => high === low ? 115 : 210 - Number((value - low) * 190n / (high - low));
  const x = (index: number) => 175 + index * 590 / Math.max(1, r.packages.length - 1);
  return <section className="flow-overview" aria-label="Brongebonden volume en flow">
    <h2>Toegelaten waarnemingen · volume & flow</h2>
    <p>Ruwe tokeneenheden · mint <code>{r.mint}</code>. Python rapporteert exacte sommen van bestaande toegelaten eventfeiten.
      Geen totaal marktvolume, netto bezit, bewezen personen, winst of strategie-indicator.</p>
    <p className="muted">Koop/verkoop = som van token_amount_raw_u64 per zijde; bruto = koop + verkoop; cumulatief = koop − verkoop.
      Unieke event-users zijn verschillende gerapporteerde user_address-waarden binnen de getoonde selectie, geen walletclusters.</p>
    <Totals title="Volledig dossier · onafhankelijk van replaypositie" groups={r.totals} />
    <Totals title={`Tot en met geselecteerd package ${String(selectedIndex + 1).padStart(2, '0')} · inclusief alle feiten van dit package`} groups={current.cumulative} />
    <p>Quote-identiteit: <strong>UNKNOWN</strong> · quote-decimals: <strong>UNAVAILABLE (null)</strong> · volume in een benoemde quotevaluta: <strong>UNAVAILABLE</strong>. Geen prijs- of SOL-omrekening.</p>
    <div className="flow-groups" role="group" aria-label="Cumulatieve reeks kiezen">{FLOW_GROUPS.map(key => <button key={key} aria-pressed={group === key}
      onClick={() => { onSelect(selectedIndex); setGroup(key); }}>{labels[key]}</button>)}</div>
    <p>Cumulatieve koop-minus-verkoopreeks · {labels[group]}. Eén punt na ieder volledig package, in chainvolgorde; geen tussentijdse factstappen of historische tijdschaal.</p>
    <div className="table-scroll"><svg className="flow-chart" viewBox="0 0 810 250" role="group" aria-label="Cumulatieve toegelaten tokenflow · exacte waarden in tabel">
      <text x="5" y="22">{high.toString()}</text><text x="5" y="212">{low.toString()}</text>
      <line x1="175" x2="775" y1={y(0n)} y2={y(0n)} className="flow-axis" />
      {r.packages.map((p, i) => <g key={p.package_id}><g role="button" tabIndex={0} aria-pressed={i === selectedIndex}
        aria-label={`Flow package ${i + 1} · ${p.slot}/${p.transaction_index} · ${p.cumulative[group].net_token_raw} raw · ${labels[group]}`}
        onClick={() => onSelect(i)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(i); } }}>
        <circle cx={x(i)} cy={y(values[i])} r="12" className="flow-target" />
        <circle cx={x(i)} cy={y(values[i])} r={i === selectedIndex ? 7 : 5} className={p.fact_hashes.length ? 'flow-point' : 'flow-no-fact'} />
        </g>
        <text x={x(i)} y="240" textAnchor="middle">{String(i + 1).padStart(2, '0')}</text>
      </g>)}
    </svg></div>
    <p className="muted">Open punten hebben geen nieuwe toegelaten feiten; de eerder opgetelde waarnemingen blijven staan. Dit bewijst geen nulactiviteit in de markt. Failures voegen geen handelsfeiten toe. Schermcoördinaten vervangen nooit de exacte bronwaarden.</p>
    <div className="table-scroll"><table aria-label="Exacte cumulatieve flow en bronfeiten"><thead><tr><th>Package / chainpositie</th><th>Bronklasse / status</th><th>Toegelaten koop − verkoop in package · raw</th><th>Cumulatief · {labels[group]} · raw</th><th>Bronfeiten van dit package</th></tr></thead>
      <tbody>{r.packages.map((p, i) => <tr key={p.package_id} className={i === selectedIndex ? 'current-trade' : ''} data-flow-package={p.package_id}>
        <td><button aria-controls="selected-package" onClick={() => onSelect(i)}>Flow package {String(i + 1).padStart(2, '0')}</button><small>{p.slot} / {p.transaction_index}</small></td>
        <td>{p.collection_role === 'ORIGINAL_SELECTION' ? 'Pilot' : 'Post-hoc context'}<small>{p.slice_class} · {p.transaction_status}</small></td>
        <td>{p.package_totals.net_token_raw}<small>{p.package_totals.facts} toegelaten feiten</small></td><td>{p.cumulative[group].net_token_raw}</td>
        <td>{p.fact_hashes.length ? p.fact_hashes.map(h => <code className="flow-hash" key={h}>{h}</code>) : 'Geen toegelaten feit · marktactiviteit onbekend'}</td>
      </tr>)}</tbody></table></div>
    <details className="evidence"><summary>Flowrapport, definities en snapshotbindingen</summary>
      <p><a href="/evidence/mint-flow.json" download>Exact Python-rapport</a> · SHA256 <code>{flow.sha256}</code></p>
      <pre className="raw-record">{JSON.stringify({ selection: r.selection_class, inputs: r.inputs, producer: r.producer, quote_volume: r.quote_volume }, null, 2)}</pre>
      <p>Packageknoppen openen alle bestaande bronfeiten, adressen en receiptbindingen. De pilotrij betreft alleen deze post-hoc mintselectie, niet alle mints uit het afzonderlijke pilotkwaliteitsscherm. Research Ready: false.</p>
    </details>
  </section>;
}

export function useMintFlow(inspection: Inspection | null): MintFlow | 'STALE' | null | undefined {
  const key = inspection ? JSON.stringify(inspection.inputs) : null;
  const [result, setResult] = useState<{ key: string; flow: MintFlow | 'STALE' | null | undefined } | null>(null);
  useEffect(() => {
    if (!inspection || !key) return;
    const controller = new AbortController(); let active = true;
    setResult({ key, flow: null });
    const timer = setTimeout(() => controller.abort(), 10000);
    void fetch('/api/mint-flow', { signal: controller.signal, cache: 'no-store', credentials: 'omit' })
      .then(registeredResponse('mint-flow', MAX_RESPONSE_BYTES)).then(value => parseMintFlow(value, inspection))
      .then(value => { if (active) setResult({ key, flow: value }); }).catch(error => { if (active) setResult({ key, flow: error instanceof SnapshotMismatch ? 'STALE' : undefined }); }).finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [inspection, key]);
  return result?.key === key ? result?.flow : null;
}

export function MintFlowUnavailable({ loading, stale = false }: { loading: boolean; stale?: boolean }) {
  return <section aria-label="Brongebonden volume en flow"><h2>Toegelaten waarnemingen · volume & flow</h2><p role="status">{loading
    ? 'Geregistreerde Python-samenvatting wordt gecontroleerd…'
    : `${stale ? 'STALE' : 'UNAVAILABLE'} · Geen geldige samenvatting voor deze geregistreerde snapshot. Er worden geen statistieken aangevuld.`}</p></section>;
}
