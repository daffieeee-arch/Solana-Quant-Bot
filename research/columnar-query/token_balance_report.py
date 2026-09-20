"""Display already projected Rust balance facts. No protobuf or Pump decoding."""
import hashlib
import html
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def query_contract(db):
    """Old manifests lack this projection; never invent an empty balance table."""
    bronze = {row[1] for row in db.execute("PRAGMA table_info('bronze')").fetchall()}
    silver = {row[1] for row in db.execute("PRAGMA table_info('silver')").fetchall()}
    raw = (HERE / 'token-balances.sql.json').read_bytes()
    binding = {'sql_sha256': hashlib.sha256(raw).hexdigest(),
               'renderer_sha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
               'status': 'AVAILABLE_TYPED_RUST_PROJECTION'}
    proposal_raw = (HERE / 'token-balance-horizon-proposal.json').read_bytes()
    proposal = json.loads(proposal_raw)
    if proposal['network_authorized'] is not False or proposal['research_ready'] is not False:
        raise ValueError('horizon proposal cannot authorize network or promote research evidence')
    binding['future_context_proposal_not_current_selection'] = proposal
    binding['horizon_proposal_sha256'] = hashlib.sha256(proposal_raw).hexdigest()
    if 'token_balance_observations' not in bronze or 'units_binding_status' not in silver:
        binding['status'] = 'UNAVAILABLE_NOT_PROJECTED_BY_ORIGINAL_WRITER'
        return {}, binding
    return json.loads(raw), binding


def objects(table):
    return [dict(zip([c['name'] for c in table['columns']], row)) for row in table['rows']]


def summary(queries, binding):
    if binding['status'] != 'AVAILABLE_TYPED_RUST_PROJECTION':
        return {'status': binding['status'], 'packages_with_balances': None,
                'observations': None, 'trades_with_bound_base_units': None}
    coverage = objects(queries['token_balance_coverage'])
    trades = objects(queries['trade_units'])
    return {'status': binding['status'],
            'packages_with_balances': sum(int(r['packages_with_recorded_balances']) for r in coverage),
            'observations': (sum(int(r['recorded_observations']) for r in coverage if r['recorded_observations'] is not None)
                             if any(r['recorded_observations'] is not None for r in coverage) else None),
            'packages_without_projection': sum(int(r['projection_unavailable_packages']) for r in coverage),
            'trades': len(trades),
            'trades_with_bound_base_units': sum(r['units_binding_status'] == 'BOUND_RECORDED_BASE_UNITS' for r in trades),
            'conflicting_trade_contexts': sum(r['units_binding_status'] == 'CONFLICTING' for r in trades),
            'trade_decimals_observed': sorted({int(r['units_decimals']) for r in trades if r['units_decimals'] is not None}),
            'meaning': 'Retained transaction metadata and separate Rust unit binding; not historical account snapshots, quote units or instruction-only deltas.'}


def render(queries, binding):
    e = lambda v: html.escape('UNAVAILABLE' if v is None else str(v))
    if not binding or binding['status'] != 'AVAILABLE_TYPED_RUST_PROJECTION':
        return '<section><h2>Tokenbalansen & eenheden</h2><p>UNAVAILABLE — deze oorspronkelijke writer projecteerde nog geen tokenbalanscontext. Geen conclusie van nul activiteit.</p></section>'
    cards = []
    for row in objects(queries['trade_units']):
        cards.append(f"""<section><h3>Slot {e(row['slot'])} · transactie {e(row['transaction_index'])} · {'buy' if row['is_buy'] is True else 'sell' if row['is_buy'] is False else 'zijde onbekend'}</h3>
<p>Mint <code>{e(row['mint'])}</code><br>Tokenprogramma <code>{e(row['units_base_token_program'])}</code></p>
<p>Balans-/eenhedenbinding: <b>{e(row['units_binding_status'])}</b>; metadata-decimals <b>{e(row['units_decimals'])}</b> ({e(row['units_decimals_evidence'])}).</p>
<p>Opgenomen event: <b>{e(row['token_amount_raw_u64'])}</b> raw; afzonderlijke exacte basis-tokenweergave <b>{e(row['units_event_token_amount_decimal'])}</b>.</p>
<p>Instructiegrenzen: spendable_quote_in {e(row['spendable_quote_in_raw_u64'])}; min_tokens_out {e(row['min_tokens_out_raw_u64'])}; sell amount {e(row['amount_raw_u64'])}; min_sol_output {e(row['min_sol_output_raw_u64'])}. Afwezig is geen nul.</p>
<p>Oorspronkelijk tradeveld base_decimals: {e(row['base_decimals'])}; het nieuwe bewijs staat uitsluitend in de aparte context. Quote-decimals {e(row['quote_decimals'])}; geen economische prijs- of fillberekening.</p></section>""")
    return ('<section><h2>Tokenbalansen & brongebonden eenhedencontext</h2>'
            '<p>Uitgevoerde inventaris: <b>' + e(json.dumps(summary(queries, binding))) + '</b>.</p>'
            '<p>Rust projecteert de behouden pre/post-statusmetadata; DuckDB leest de getypeerde Parquet-kolommen. '
            'Een leeg repeated veld onderscheidt niet of balansregistratie ontbrak of een lege lijst aanwezig was. '
            'Wire-aanwezigheid en protobuf-defaults blijven apart. Een ontbrekende balans wordt nooit nul.</p>'
            '<p>De exacte decimale strings zijn door Rust berekend uit een brongebonden integer/decimals-combinatie, '
            'niet uit ui_amount-floats. Dit bewijst geen volledige historische accountsnapshot of CPI-privileges. '
            'Een pre/postdelta omvat de hele transactie: niet automatisch één instructie, event of fee.</p>'
            + ''.join(cards)
            + '<details><summary>Eenmalig tijdsvervolgvoorstel — geen acquisitietoestemming</summary><p>'
              'Voorgesteld: zestien volgende slots per bestaande pilotwaarneming; extra context '
              '[422669519,422669535). Dit is een ontwerpkeuze, geen bewezen voldoende minimumduur. '
              'Stop bij dit eindpunt, ook zonder paar. Oorspronkelijke sample blijft onveranderd; '
              'post-hoc context is geen nieuwe onafhankelijke onderzoekssteekproef. '
              'Eerst een brongebonden batchroute en concreet gemeten nieuw runbudget; geen request nu.'
              '</p><pre>' + e(json.dumps(binding.get('future_context_proposal_not_current_selection'), indent=2))
            + '</pre></details></section>')
