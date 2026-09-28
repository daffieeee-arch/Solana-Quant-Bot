import { createHash } from 'node:crypto';
import { check, list, object } from './contract.js';
import { exactNativeJson } from './cohort-binding.js';
import { parseDevelopmentCohort, same } from './development-cohort.js';
import { parseInstructionCoverage } from './instruction-coverage.js';
const sha = (v: Buffer) => createHash('sha256').update(v).digest('hex');
export function bindInstructionCoverage(report: Buffer, admission: Buffer, cohortBytes: Buffer) {
  const cohort = parseDevelopmentCohort(JSON.parse(cohortBytes.toString('utf8')));
  const r = parseInstructionCoverage(JSON.parse(report.toString('utf8')), cohort, sha(cohortBytes));
  const a = object(exactNativeJson(admission.toString('utf8')));
  check(r.native_admission_sha256 === sha(admission) && a.schema === 'OF1_B7_DEVELOPMENT_INSTRUCTIONS_ADMISSION_1'
    && a.selection_sha256 === cohort.selection_sha256 && a.pins_sha256 === cohort.pins_sha256 && a.research_ready === false
    && a.reader_source_sha256 === r.producer.native_source_sha256 && a.reader_binary_sha256 === r.producer.native_binary_sha256);
  const windows = list(a.windows).map(object); check(windows.length === 4);
  r.windows.forEach((w,i) => {
    for (const k of ['ordinal','collection_sha256','collection_path','sample_identity','counts','layers','parts','coverage','slot_outcomes','instruction_inventory']) check(same(w[k],windows[i][k]));
  });
  return r;
}
