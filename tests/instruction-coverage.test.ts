import { expect, it } from 'vitest';
import { fixtureInstructionCoverage } from './fixtures/instruction-coverage.js';
import { bindInstructionCoverage } from '../src/mint-inspector/instruction-binding.js';
import { object, list } from '../src/mint-inspector/contract.js';
it('binds every original fact to distinct trade/event roles and the exact cohort snapshot', () => {
  const f = fixtureInstructionCoverage();
  expect(bindInstructionCoverage(Buffer.from(JSON.stringify(f.report)), f.admissionBytes, f.cohortBytes)).toEqual(f.report);
});
it.each(['role','snapshot','cohort','duplicate','failure','event','phantom','inventory','counts','missing-cpi'])('rejects invalid instruction evidence: %s', variant => {
  const f = fixtureInstructionCoverage(), w = f.report.windows[0], inv = w.instruction_inventory, p = inv.packages[0];
  if (variant === 'role') object(object(w.sample_identity).b7).cohort_role = 'RESERVED_EVALUATION';
  if (variant === 'snapshot') w.collection_sha256 = '0'.repeat(64);
  if (variant === 'cohort') f.report.cohort_report_sha256 = '0'.repeat(64);
  if (variant === 'duplicate') p.instructions.push(p.instructions[0]);
  if (variant === 'failure') p.transaction_status = 'ERROR';
  if (variant === 'event') inv.fact_links[0].event_instruction_id = inv.fact_links[0].trade_instruction_id;
  if (variant === 'phantom') p.instructions[0].trade_fact_sha256s.push('0'.repeat(64));
  if (variant === 'inventory') p.instructions[0].probes.push({ evidence_sha256:'d'.repeat(64), bronze_json_pointer:'/invented' });
  if (variant === 'counts') object(w.summary.category_counts).ADMITTED_TRADE = '0';
  if (variant === 'missing-cpi') p.instructions.pop();
  expect(() => bindInstructionCoverage(Buffer.from(JSON.stringify(f.report)), f.admissionBytes, f.cohortBytes)).toThrow();
});
