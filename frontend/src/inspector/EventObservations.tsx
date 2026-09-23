import type { Json, Package, Trade } from '../../../src/mint-inspector/contract';

const RESERVES = [
  { field: 'real_token_reserves_raw_u64', label: 'Real token', shape: 'real' },
  { field: 'virtual_token_reserves_raw_u64', label: 'Virtual token', shape: 'virtual' },
] as const;
const ADDRESSES = [
  { field: 'user_address', label: 'Gerapporteerde user' },
  { field: 'creator_address_reported', label: 'Gerapporteerde creator' },
  { field: 'fee_recipient', label: 'Gerapporteerde fee recipient' },
] as const;
const U64_MAX = 18446744073709551615n;
const rawText = (value: Json | undefined) => value === undefined ? 'veld ontbreekt' : JSON.stringify(value);

/** Display validity only. No repair, protocol decode or change to the source record. */
export function reserveValue(raw: Json | undefined): { text: string; value: bigint | null } {
  if (raw === undefined) return { text: 'UNAVAILABLE · veld ontbreekt', value: null };
  if (raw === null) return { text: 'UNAVAILABLE · null', value: null };
  if (typeof raw !== 'string' || raw.length > 20 || !/^(0|[1-9][0-9]*)$/.test(raw) || BigInt(raw) > U64_MAX) {
    return { text: `ONGELDIG · ${rawText(raw)}`, value: null };
  }
  return { text: raw, value: BigInt(raw) };
}
function addressText(raw: Json | undefined): string {
  if (raw === undefined) return 'UNAVAILABLE · veld ontbreekt';
  if (raw === null) return 'UNAVAILABLE · null';
  return typeof raw === 'string' && raw.length > 0 ? raw : `ONGELDIG · ${rawText(raw)}`;
}
const packageLabel = (index: number) => `Package ${String(index + 1).padStart(2, '0')}`;
const sourceClass = (p: Package) => p.collection_role === 'ORIGINAL_SELECTION'
  ? 'Pilot · RESEARCH_SAMPLING' : 'Post-hoc context · ENGINEERING_VALIDATION_ONLY';

type Props = { packages: Package[]; selectedIndex: number; onSelect: (index: number) => void };
type FactRow = { p: Package; packageIndex: number; fact: Trade; factIndex: number };
function FactReference({ row, onSelect }: { row: FactRow; onSelect: Props['onSelect'] }) {
    return <><button aria-controls="selected-package" onClick={() => onSelect(row.packageIndex)}>{packageLabel(row.packageIndex)} · feit {row.factIndex + 1}</button>
      <small>Slot {row.p.slot} / tx {row.p.transaction_index}</small><small>{sourceClass(row.p)}</small>
      <code className="fact-hash">{row.fact.record_sha256}</code></>;
  }
export function EventObservations({ packages, selectedIndex, onSelect }: Props) {
  // Retain input/package/fact order, including distinct facts sharing an atomic parent.
  const facts = packages.flatMap((p, packageIndex) => p.silver_facts.map((fact, factIndex) => ({ p, packageIndex, fact, factIndex })));
  const points = facts.flatMap(row => RESERVES.flatMap(series => {
    const value = reserveValue(row.fact.record.event_reported[series.field]);
    return value.value === null ? [] : [{ ...row, ...series, value: value.value, exact: value.text }];
  }));
  const min = points.reduce<bigint | null>((a, p) => a === null || p.value < a ? p.value : a, null);
  const max = points.reduce<bigint | null>((a, p) => a === null || p.value > a ? p.value : a, null);
  const band = 800 / Math.max(1, packages.length);
  const x = (i: number) => 170 + band * (i + 0.5);
  // Only a bounded pixel ratio becomes Number; original u64 strings remain authoritative.
  const y = (value: bigint) => min === null || max === null || max === min ? 130
    : 230 - Number((value - min) * 200000n / (max - min)) / 1000;
  const selected = packages[selectedIndex];
  return <section className="event-observations" aria-label="Eventwaarnemingen volledig dossier">
    <header><p className="eyebrow">VOLLEDIG DOSSIER · {facts.length} BESTAANDE FEITEN</p><h2>Door events gerapporteerde reserves</h2>
      <p>Dit zijn eventvelden, geen geverifieerde historische accountstaat. Geen interpolatie, forward-fill of reserve voor een package zonder waarneming.</p>
      <p className="selection-context">Geselecteerd: {packageLabel(selectedIndex)}{selected ? ` · slot ${selected.slot} / tx ${selected.transaction_index}` : ' · UNAVAILABLE'}. Selectie van een punt of feit pauzeert en opent het volledige package.</p>
      {selected?.silver_facts.length === 0 && <p>Geselecteerd package: geen toegelaten feit en geen reservepunt. De volledige-dossierwaarnemingen blijven hieronder zichtbaar.</p>}
    </header>
    <div className="reserve-legend"><span className="real">● Real token · real_token_reserves_raw_u64</span><span className="virtual">◆ Virtual token · virtual_token_reserves_raw_u64</span></div>
    <p className="muted">Beide reeksen delen de getoonde raw-u64-schaal. X is packagevolgorde, geen tijdschaal. Punten zijn binnen hun package versprongen voor leesbaarheid, zonder extra chainpositie. Schermcoördinaten zijn geen bronwaarden.</p>
    {points.length === 0 ? <p>UNAVAILABLE · Geen geldige u64-reservepunten. Zie de oorspronkelijke waarden hieronder.</p> :
      <div className="reserve-chart-scroll"><svg className="reserve-chart" viewBox="0 0 1000 280" role="group" aria-label="Reservepunten in packagevolgorde">
        <title>Eventgerapporteerde tokenreserves — twee losse puntenreeksen, geen accountstaat</title>
        <rect className="selected-band" x={170 + band * selectedIndex} y="20" width={band} height="220" />
        {[...new Set([min, max])].map(value => value !== null && <g key={value.toString()} className="reserve-axis"><line x1="170" x2="970" y1={y(value)} y2={y(value)} /><text x="158" y={y(value) + 4} textAnchor="end">{value.toString()}</text></g>)}
        {packages.map((p, i) => <g key={p.package_id} className="reserve-axis"><text x={x(i)} y="262" textAnchor="middle">{String(i + 1).padStart(2, '0')}</text></g>)}
        {points.map(point => {
          const label = `${point.label}: ${point.exact} · ${packageLabel(point.packageIndex)} · feit ${point.factIndex + 1} · slot ${point.p.slot} / tx ${point.p.transaction_index} · ${sourceClass(point.p)} · ${point.fact.record_sha256}`;
          const seriesIndex = point.shape === 'real' ? 0 : 1;
          const offset = ((point.factIndex * 2 + seriesIndex + 0.5) / (point.p.silver_facts.length * 2) - 0.5) * band * 0.7;
          return <g key={`${point.fact.record_sha256}:${point.field}`} className={`reserve-point ${point.shape}`}
            data-reserve-field={point.field} data-reserve-hash={point.fact.record_sha256} data-raw-value={point.exact}
            transform={`translate(${x(point.packageIndex) + offset} ${y(point.value)})`}
            role="button" tabIndex={0} aria-label={label} aria-pressed={selectedIndex === point.packageIndex} aria-controls="selected-package"
            onClick={() => onSelect(point.packageIndex)} onKeyDown={event => {
              if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(point.packageIndex); }
            }}>
            <title>{label}</title><circle className="point-target" r="13" />
            {point.shape === 'real' ? <circle className="point-symbol" r="5" /> : <path className="point-symbol" d="M 0 -7 L 7 0 L 0 7 L -7 0 Z" />}
          </g>;
        })}
      </svg></div>}
    <div className="table-scroll"><table className="observation-table" aria-label="Exacte reservewaarnemingen">
      <caption>Exacte oorspronkelijke waarden · alle feiten, per atomair package gegroepeerd. 0 is een opgenomen nul; UNAVAILABLE en ONGELDIG zijn geen nul.</caption>
      <thead><tr><th>Package / chainpositie / feithash</th>{RESERVES.map(s => <th key={s.field}>{s.label}<small>{s.field}</small></th>)}</tr></thead>
      {packages.map((p, i) => <tbody key={p.package_id} data-reserve-package={p.package_id} className={selectedIndex === i ? 'selected-observations' : ''}>
        {facts.filter(row => row.packageIndex === i).map(row => <tr key={row.fact.record_sha256} data-reserve-row={row.fact.record_sha256}>
          <th scope="row"><FactReference row={row} onSelect={onSelect} /></th>{RESERVES.map(s => <td key={s.field}>{reserveValue(row.fact.record.event_reported[s.field]).text}</td>)}
        </tr>)}
      </tbody>)}
    </table></div>
    <section className="reported-addresses" aria-label="Gerapporteerde adressen volledig dossier"><h2>Gerapporteerde adressen</h2>
      <p>De drie veldrollen blijven afzonderlijk. Een adres bewijst geen signerrechten, economisch eigendom, unieke persoon of walletcluster. Geen externe verrijking.</p>
      <div className="table-scroll"><table className="observation-table" aria-label="Adresrollen per bestaand feit"><thead><tr><th>Package / chainpositie / feithash</th><th>Gerapporteerde veldrol</th><th>Oorspronkelijke adrestekst</th></tr></thead>
        {packages.map((p, i) => <tbody key={p.package_id} data-address-package={p.package_id} className={selectedIndex === i ? 'selected-observations' : ''}>
          {facts.filter(row => row.packageIndex === i).flatMap(row => ADDRESSES.map((role, roleIndex) => <tr key={`${row.fact.record_sha256}:${role.field}`} data-address-hash={row.fact.record_sha256} data-address-field={role.field}>
            {roleIndex === 0 && <th scope="row" rowSpan={3}><FactReference row={row} onSelect={onSelect} /></th>}
            <td>{role.label}<small><code>{role.field}</code></small></td><td className="reported-address">{addressText(row.fact.record.event_reported[role.field])}</td>
          </tr>))}
        </tbody>)}
      </table></div>
    </section>
  </section>;
}
