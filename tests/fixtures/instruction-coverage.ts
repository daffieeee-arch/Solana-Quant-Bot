/** Synthetic transport/UI fixtures; no new protocol or authentic evidence. */
import { createHash } from 'node:crypto';
import { fixtureDevelopmentCohort } from './development-cohort.js';
import { INSTRUCTION_RULE, type InstructionCoverage } from '../../src/mint-inspector/instruction-coverage.js';
const sha = (s: string | Buffer) => createHash('sha256').update(s).digest('hex');
export function fixtureInstructionCoverage() {
  const cohort = fixtureDevelopmentCohort(), cohortBytes = Buffer.from(JSON.stringify(cohort.report));
  const windows = cohort.report.windows.map(w => {
    const packages = w.mints.flatMap(m => m.facts).map(f => {
      const pid = f.package_id, fh = f.record_sha256;
      const rows = ['ADMITTED_TRADE', 'SUPPORTING_EVENT_CPI'].map((category, n) => ({
        instruction_id: `${pid}:0:${n === 0 ? 'TOP' : 'CPI:0'}`, location: { kind: n === 0 ? 'DECLARED_TOP_LEVEL' : 'RECORDED_CPI', outer_index: '0', inner_order: n === 0 ? null : '0' },
        category, instruction_sha256: String(n + 1).repeat(64), reference_count: '1', may_hide_trade_observation: false,
        reasons: ['BOUND_EXISTING_SILVER' + (n === 0 ? '' : '_EVENT')], probes: [], diagnoses: [], trade_fact_sha256s: n === 0 ? [fh] : [], event_fact_sha256s: n === 1 ? [fh] : [], execution: 'NOT_ESTABLISHED_BY_INSTRUCTION_PRESENCE',
      }));
      return { package_id: pid, bronze_record_sha256: f.record.bronze_record_sha256, part_id: '0', effective_at: f.record.effective_at, transaction_status: 'OK', source: f.record.source, instructions: rows };
    });
    const facts = w.mints.flatMap(m => m.facts), links = facts.map((f,n) => ({ fact_sha256: f.record_sha256, package_id: f.package_id, trade_instruction_id: packages[n].instructions[0].instruction_id, event_instruction_id: packages[n].instructions[1].instruction_id, instruction_sha256: '1'.repeat(64), event_cpi_sha256: '2'.repeat(64) }));
    const inventory = { rule_version: INSTRUCTION_RULE, packages, fact_links: links, uncertainty: [], unique_pump_instructions: String(packages.length * 2), all_packages_checked: w.counts.packages, failed_packages_checked: w.counts.failures };
    const summary = { packages_with_identified_pump_instructions: String(packages.length), unique_pump_instructions: inventory.unique_pump_instructions, silver_facts: w.counts.silver_facts,
      category_counts: { ADMITTED_TRADE: String(packages.length), SUPPORTING_EVENT_CPI: String(packages.length), OTHER_INSTRUCTION: '0', REJECTED_TRADE: '0', UNEXPLAINED: '0' },
      failed_parent_instructions: '0', instruction_references: inventory.unique_pump_instructions, duplicate_instruction_references: '0', probe_references: '0', distinct_instruction_probe_values: '0', diagnosis_references: '0',
      reason_instruction_counts: { BOUND_EXISTING_SILVER: String(packages.length), BOUND_EXISTING_SILVER_EVENT: String(packages.length) }, probe_outcomes: {}, uncertain_packages: '0', uncertainty_items: '0', uncertainty_reason_counts: {}, potentially_hiding_packages: '0', negative_conclusion: 'NO_GAP_IDENTIFIED_BY_THIS_INVENTORY_NOT_A_NEGATIVE_LABEL' };
    return { ...w, instruction_inventory: inventory, summary, pair_evidence: w.mints.filter(m => m.observed_pair !== null).map(m => ({ mint: m.mint, observed_pair: m.observed_pair })) };
  });
  const admission = { schema: 'OF1_B7_DEVELOPMENT_INSTRUCTIONS_ADMISSION_1', pins_sha256: cohort.report.pins_sha256, selection_sha256: cohort.report.selection_sha256, research_ready: false, reader_source_sha256: 'a'.repeat(64), reader_binary_sha256: 'b'.repeat(64), windows };
  const admissionBytes = Buffer.from(JSON.stringify(admission));
  const report = { schema: 'OF1_B7_DEVELOPMENT_INSTRUCTIONS_1', state: 'READY', research_ready: false, evaluation_access: 'DENIED', selection_sha256: admission.selection_sha256, pins_sha256: admission.pins_sha256,
    native_admission_sha256: sha(admissionBytes), cohort_report_sha256: sha(cohortBytes), producer: { version: INSTRUCTION_RULE, python_sha256: 'c'.repeat(64), native_source_sha256: admission.reader_source_sha256, native_binary_sha256: admission.reader_binary_sha256 }, rules: { version: INSTRUCTION_RULE }, totals: cohort.report.totals, windows } as unknown as InstructionCoverage;
  return { cohort, cohortBytes, admissionBytes, report };
}
