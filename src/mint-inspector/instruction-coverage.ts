/** Presentation contract over Rust's fixed DEVELOPMENT inventory, not a decoder. */
import { check, exactTree, hash, list, object, uint, type ObjectValue } from './contract.js';
import { same, type DevelopmentCohort } from './development-cohort.js';
export const INSTRUCTION_RULE = 'DEVELOPMENT_INSTRUCTION_COVERAGE_1';
export const INSTRUCTION_CATEGORIES = ['ADMITTED_TRADE', 'SUPPORTING_EVENT_CPI', 'OTHER_INSTRUCTION', 'REJECTED_TRADE', 'UNEXPLAINED'] as const;
export type InventoryInstruction = ObjectValue & { instruction_id: string; location: ObjectValue; category: string; reasons: string[]; probes: ObjectValue[]; diagnoses: ObjectValue[]; trade_fact_sha256s: string[]; event_fact_sha256s: string[]; may_hide_trade_observation: boolean };
export type InventoryPackage = ObjectValue & { package_id: string; bronze_record_sha256: string; part_id: string; effective_at: ObjectValue; transaction_status: string | null; instructions: InventoryInstruction[] };
export type InstructionWindow = ObjectValue & { ordinal: string; summary: ObjectValue; instruction_inventory: { packages: InventoryPackage[]; uncertainty: ObjectValue[]; fact_links: ObjectValue[] }; parts: ObjectValue[] };
export interface InstructionCoverage { schema: 'OF1_B7_DEVELOPMENT_INSTRUCTIONS_1'; native_admission_sha256: string; cohort_report_sha256: string; producer: ObjectValue; rules: ObjectValue; windows: InstructionWindow[]; totals: ObjectValue }
export function parseInstructionCoverage(value: unknown, cohort: DevelopmentCohort, cohortSha: string): InstructionCoverage {
  exactTree(value, 0, { left: 500000 }); hash(cohortSha);
  const r = object(value), producer = object(r.producer);
  check(r.schema === 'OF1_B7_DEVELOPMENT_INSTRUCTIONS_1' && r.state === 'READY' && r.research_ready === false && r.evaluation_access === 'DENIED'
    && r.cohort_report_sha256 === cohortSha && r.selection_sha256 === cohort.selection_sha256 && r.pins_sha256 === cohort.pins_sha256
    && same(r.totals, cohort.totals) && producer.version === INSTRUCTION_RULE && object(r.rules).version === INSTRUCTION_RULE);
  hash(r.native_admission_sha256); for (const k of ['native_source_sha256', 'native_binary_sha256', 'python_sha256']) hash(producer[k]);
  const windows = list(r.windows).map(object); check(windows.length === 4);
  for (const [n,w] of windows.entries()) {
    const c = cohort.windows[n];
    for (const key of ['ordinal', 'collection_sha256', 'collection_path', 'sample_identity', 'counts', 'layers', 'parts', 'coverage', 'slot_outcomes'] as const) check(same(w[key], c[key]));
    check(w.observed_pair_mints === c.observed_pair_mints && same(w.pair_evidence, c.mints.filter(m => m.observed_pair !== null).map(m => ({ mint: m.mint, observed_pair: m.observed_pair }))));
    const inv = object(w.instruction_inventory), summary = object(w.summary), categories = object(summary.category_counts);
    check(inv.rule_version === INSTRUCTION_RULE && inv.all_packages_checked === c.counts.packages && inv.failed_packages_checked === c.counts.failures);
    const ids = new Map<string, { packageId: string; row: Record<string, unknown>; status: unknown }>(), parents = new Set<string>();
    let probes = 0, diagnostics = 0;
    for (const p of list(inv.packages).map(object)) {
      check(typeof p.package_id === 'string' && !parents.has(p.package_id)); parents.add(p.package_id); hash(p.bronze_record_sha256); uint(p.part_id);
      check(p.package_id.endsWith(':' + p.bronze_record_sha256) && BigInt(p.part_id) < BigInt(c.parts.length));
      const chain = object(p.effective_at); uint(chain.slot); uint(chain.transaction_index_in_slot);
      check(BigInt(chain.slot) >= BigInt(c.sample_identity.start_slot as string) && BigInt(chain.slot) < BigInt(c.sample_identity.end_slot_exclusive as string));
      for (const ix of list(p.instructions).map(object)) {
        const loc = object(ix.location); uint(loc.outer_index);
        check(loc.kind === 'DECLARED_TOP_LEVEL' && loc.inner_order === null || loc.kind === 'RECORDED_CPI' && typeof loc.inner_order === 'string');
        if (loc.inner_order !== null) uint(loc.inner_order);
        const id = `${p.package_id}:${loc.outer_index}:${loc.inner_order === null ? 'TOP' : `CPI:${loc.inner_order}`}`;
        check(ix.instruction_id === id && !ids.has(id) && INSTRUCTION_CATEGORIES.includes(ix.category as typeof INSTRUCTION_CATEGORIES[number]));
        ids.set(id, { packageId: p.package_id, row: ix, status: p.transaction_status }); hash(ix.instruction_sha256);
        check(typeof ix.may_hide_trade_observation === 'boolean' && ix.execution === 'NOT_ESTABLISHED_BY_INSTRUCTION_PRESENCE');
        check(list(ix.reasons).length > 0 && list(ix.reasons).every(v => typeof v === 'string'));
        for (const field of ['probes', 'diagnoses']) for (const ev of list(ix[field]).map(object)) { hash(ev.evidence_sha256); check(typeof ev.bronze_json_pointer === 'string'); }
        probes += list(ix.probes).length; diagnostics += list(ix.diagnoses).length;
      }
    }
    for (const category of INSTRUCTION_CATEGORIES) {
      uint(categories[category]); check(categories[category] === String([...ids.values()].filter(x => x.row.category === category).length));
    }
    check(summary.unique_pump_instructions === String(ids.size) && inv.unique_pump_instructions === String(ids.size)
      && summary.packages_with_identified_pump_instructions === String(parents.size)
      && summary.probe_references === String(probes) && summary.diagnosis_references === String(diagnostics)
      && summary.silver_facts === c.counts.silver_facts);
    const facts = c.mints.flatMap(m => m.facts), linked = new Set<string>();
    const links = list(inv.fact_links).map(object); check(links.length === facts.length);
    for (const l of links) {
      hash(l.fact_sha256); check(!linked.has(l.fact_sha256)); linked.add(l.fact_sha256);
      const f = facts.find(f => f.record_sha256 === l.fact_sha256), t = ids.get(l.trade_instruction_id as string), e = ids.get(l.event_instruction_id as string);
      check(f && t && e && l.trade_instruction_id !== l.event_instruction_id && t.status === 'OK' && e.status === 'OK'
        && t.packageId === f.package_id && e.packageId === f.package_id && l.package_id === f.package_id
        && t.row.category === 'ADMITTED_TRADE' && e.row.category === 'SUPPORTING_EVENT_CPI'
        && t.row.instruction_sha256 === f.record.instruction_sha256 && l.instruction_sha256 === f.record.instruction_sha256
        && e.row.instruction_sha256 === object(f.record.event_context).event_cpi_sha256 && l.event_cpi_sha256 === e.row.instruction_sha256);
    }
    for (const [id, entry] of ids) for (const [field, role] of [['trade_fact_sha256s', 'trade_instruction_id'], ['event_fact_sha256s', 'event_instruction_id']])
      check(same(entry.row[field], links.filter(l => l[role] === id).map(l => l.fact_sha256)));
    const unknown = list(inv.uncertainty).map(object), unknownIds = new Set<string>(); let items = 0;
    for (const u of unknown) { check(typeof u.package_id === 'string' && !unknownIds.has(u.package_id)); unknownIds.add(u.package_id); hash(u.bronze_record_sha256); const rows = list(u.items); check(rows.length > 0); items += rows.length; }
    check(summary.uncertain_packages === String(unknown.length) && summary.uncertainty_items === String(items));
    uint(summary.potentially_hiding_packages);
    check(summary.negative_conclusion === (summary.potentially_hiding_packages === '0' ? 'NO_GAP_IDENTIFIED_BY_THIS_INVENTORY_NOT_A_NEGATIVE_LABEL' : 'UNAVAILABLE'));
  }
  return value as InstructionCoverage;
}
