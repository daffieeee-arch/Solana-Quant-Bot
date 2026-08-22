#!/usr/bin/env node
import { readFileSync } from 'node:fs';

const basicAuth=/https?:\/\/[^\s/@:]+:[^\s/@]+@/iu;
const credentialAssignment=/(?:^|\b)(?:TRITON_TOKEN|GITHUB_TOKEN|API_TOKEN|PASSWORD|SECRET|API_KEY|COOKIE|CREDENTIAL)=[^\s\x00]{8,}/iu;
const credentialName=/(?:TOKEN|PASSWORD|SECRET|API_KEY|COOKIE|CREDENTIAL)/iu;

export function validateImageMetadata(environment, historyLines) {
  const errors=[];
  if (!Array.isArray(environment) || !Array.isArray(historyLines)) return ['credential_metadata_shape_invalid'];
  for (const row of environment) {
    if (typeof row!=='string') { errors.push('credential_environment_not_string'); continue; }
    const name=row.split('=',1)[0];
    if (credentialName.test(name) || basicAuth.test(row) || credentialAssignment.test(row)) errors.push(`credential_environment_forbidden:${name}`);
  }
  for (const row of historyLines) {
    if (typeof row!=='string' || basicAuth.test(row) || credentialAssignment.test(row)) errors.push('credential_history_forbidden');
  }
  return [...new Set(errors)].sort();
}

if (import.meta.url===`file://${process.argv[1]}`) {
  const [envPath,historyPath]=process.argv.slice(2);
  if (!envPath||!historyPath) throw new Error('usage: validate-image-metadata.mjs <config-env.json> <history.ndjson>');
  const environment=JSON.parse(readFileSync(envPath,'utf8'));
  const historyLines=readFileSync(historyPath,'utf8').split(/\r?\n/).filter(Boolean);
  const errors=validateImageMetadata(environment,historyLines);
  if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
  console.log('Phase 8D1 image Config.Env/history credential scan PASS');
}
