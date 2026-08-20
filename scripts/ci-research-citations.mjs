#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, posix, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_EVIDENCE = 'docs/research/PUMP_ACTIVATION_EVIDENCE_EPOCH_978.json';
const DEFAULT_SCRATCH = 'docs/research/PUMP_ACTIVATION_SCRATCH_MANIFEST.json';
const DEFAULT_DOCUMENT = 'docs/PHASE7A_PUMP_ACTIVATION_EVIDENCE.md';
const TRUSTED_EVIDENCE_CANONICAL_SHA256 = '2cf821104aa7cfb6778175f3c93ac6f595dce425c07e890af309965f3c373422';
const TRUSTED_EVIDENCE_FILE_SHA256 = '116b3d954a1f9099b4dab4a25db7f849739d676a75fe54b7f2c322d14e87c820';
const TRUSTED_SCRATCH_CANONICAL_SHA256 = '2525ce0fa81dfb2027973b199b0e769569c364aaccb423c8768ed4654fc4230b';
const TRUSTED_SCRATCH_FILE_SHA256 = '15c8f8db11537c606c6e3639e8f231c6a43f2f7b78fa7c0676198fde836ad7b0';
const TRUSTED_DOCUMENT_SHA256 = '978d8a1941e94c8d7aacb81fc3d0f863026bba7b5bf7af169b2f8d133945e258';
const EXPECTED_ENTRY_IDS = [
  'instruction:create',
  'instruction:create_v2',
  'instruction:buy',
  'instruction:sell',
  'instruction:buy_exact_sol_in',
  'instruction:buy_v2',
  'instruction:sell_v2',
  'instruction:buy_exact_quote_in_v2',
  'event:CreateEvent',
  'event:TradeEvent',
];
const EXPECTED_AUTHORIZATION_KEYS = [
  'registryUpdateAuthorized',
  'acceptedSilverAuthorized',
  'bandwidthPreflightAuthorized',
  'networkShapingAuthorized',
  'pilotAExecutionAuthorized',
  'pilotBExecutionAuthorized',
  'carOrRangeRetrievalAuthorized',
  'archiveStreamAuthorized',
  'realSlotProcessingAuthorized',
  'runtimeMutationAuthorized',
];
const EXPECTED_SOURCE_IDS = [
  'SRC-AGAVE-BPF-LOADER',
  'SRC-ANCHOR-IDL-DERIVATION',
  'SRC-ANCHOR-LEGACY-IDL-CLI',
  'SRC-OF1-FILES-DOC',
  'SRC-OF1-GSFA-HEAD',
  'SRC-OF1-SIG-EXISTS-HEAD',
  'SRC-OF1-SIG-TO-CID-HEAD',
  'SRC-PUMP-IDL-CANDIDATE-REVISION',
  'SRC-PUMP-IDL-HISTORY',
  'SRC-PUMP-RELEASES',
  'SRC-PUMP-TAGS',
  'SRC-SCRATCH-INVENTORY',
  'SRC-SOLANA-ANCHOR-IDL-ACCOUNT',
  'SRC-SOLANA-ANCHOR-IDL-LAST-WRITE',
  'SRC-SOLANA-ANCHOR-IDL-SIGS-1',
  'SRC-SOLANA-ANCHOR-IDL-SIGS-2',
  'SRC-SOLANA-ANCHOR-IDL-TX-BATCH-002',
  'SRC-SOLANA-ANCHOR-IDL-TX-BATCH-003',
  'SRC-SOLANA-ANCHOR-IDL-TX-BATCH-004',
  'SRC-SOLANA-ANCHOR-IDL-TX-BATCH-005',
  'SRC-SOLANA-ANCHOR-IDL-TX-BATCH-006',
  'SRC-SOLANA-ANCHOR-IDL-TX-FINAL-RETRY-1',
  'SRC-SOLANA-ANCHOR-IDL-TX-FINAL-RETRY-2',
  'SRC-SOLANA-CANDIDATE-BUFFER-SIGS-1',
  'SRC-SOLANA-CANDIDATE-BUFFER-SIGS-2',
  'SRC-SOLANA-PROGRAM-ACCOUNT',
  'SRC-SOLANA-PROGRAMDATA-ACCOUNT',
  'SRC-SOLANA-PROGRAMDATA-SIGS-1',
  'SRC-SOLANA-PROGRAMDATA-SIGS-2',
  'SRC-SOLANA-PROGRAMDATA-TX-BATCH-002',
  'SRC-SOLANA-PROGRAMDATA-TX-BATCH-003',
  'SRC-SOLANA-PROGRAMDATA-TX-BATCH-004',
  'SRC-SOLANA-PROGRAMDATA-TX-BATCH-005',
  'SRC-SOLANA-PROGRAMDATA-TX-BATCH-005-RETRY-1',
  'SRC-SOLANA-PROGRAMDATA-TX-BATCH-005-RETRY-2',
  'SRC-SOLANA-PROGRAMDATA-TX-BOUNDARIES',
  'SRC-SOLANA-PROGRAMDATA-TX-FINAL-RETRY-001',
  'SRC-SOLANA-PROGRAMDATA-TX-FINAL-RETRY-002',
  'SRC-SOLANA-PROGRAMDATA-TX-FINAL-RETRY-003',
  'SRC-SOLANA-PROGRAMDATA-TX-FINAL-RETRY-004',
  'SRC-SOLANA-PROGRAMDATA-TX-FINAL-RETRY-005',
  'SRC-SOLANA-PROGRAMDATA-TX-FINAL-RETRY-006',
  'SRC-SOLANA-SDK-LOADER-INSTRUCTION',
  'SRC-SOLANA-SDK-LOADER-STATE',
  'SRC-SOLANA-SLOT-END-BLOCK',
  'SRC-SOLANA-SLOT-END-TIME',
  'SRC-SOLANA-SLOT-START-BLOCK',
  'SRC-SOLANA-SLOT-START-TIME',
];
const EXPECTED_CLAIM_IDS = [
  'ACT-CANDIDATE-TIME',
  'ACT-PROGRAMDATA',
  'ACT-UPGRADE-BOUNDARY',
  'ACT-HISTORY-INCOMPLETE',
  'ACT-BINARY-UNPROVEN',
  'ACT-OFFICIAL-IDL',
  'ACT-ONCHAIN-IDL',
  'ACT-RANGE-OBSERVATIONS',
  'ACT-NETWORK-BUDGET',
  'ACT-SCRATCH-BINDING',
  'ACT-REGISTRY-HOLD',
  'ACT-NO-AUTHORIZATION',
];
const SHA256 = /^[0-9a-f]{64}$/;
const IDENTIFIER = /^[A-Z][A-Z0-9-]*$/;
const IMMUTABLE_REF = /^[0-9a-f]{40}$/;
const ACTIVATION_PRIMARY_SOURCE_TYPES = new Set([
  'RAW_SOLANA_RPC',
  'OFFICIAL_SOLANA_SOURCE',
  'OFFICIAL_PUMP_GIT_HISTORY',
  'OFFICIAL_PUMP_IDL',
  'OFFICIAL_ANCHOR_SOURCE',
  'OFFICIAL_OLD_FAITHFUL_DOC',
  'OFFICIAL_OLD_FAITHFUL_HEAD',
  'OFFICIAL_PUMP_GITHUB_API',
]);
const ALLOWED_SOURCE_TYPES = new Set([
  ...ACTIVATION_PRIMARY_SOURCE_TYPES,
  'LOCAL_IMMUTABLE_HASH_CHAIN',
]);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'number' || typeof value === 'string') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((entry) => canonicalJson(entry)).join(',')}]`;
  if (!isRecord(value)) throw new Error('non_canonical_value');
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function exactStringSet(values, expected) {
  return Array.isArray(values)
    && values.every((value) => typeof value === 'string')
    && JSON.stringify([...values].sort()) === JSON.stringify([...expected].sort());
}

function duplicateIds(values, key) {
  if (!Array.isArray(values)) return [];
  const seen = new Set();
  const duplicates = new Set();
  for (const value of values) {
    if (!isRecord(value) || typeof value[key] !== 'string') continue;
    if (seen.has(value[key])) duplicates.add(value[key]);
    seen.add(value[key]);
  }
  return [...duplicates].sort();
}

function resolvePointer(root, pointer) {
  if (pointer === '') return root;
  if (typeof pointer !== 'string' || !pointer.startsWith('/')) return undefined;
  let value = root;
  for (const rawPart of pointer.slice(1).split('/')) {
    const part = rawPart.replaceAll('~1', '/').replaceAll('~0', '~');
    if (Array.isArray(value)) {
      if (!/^\d+$/.test(part) || Number(part) >= value.length) return undefined;
      value = value[Number(part)];
    } else if (isRecord(value) && Object.prototype.hasOwnProperty.call(value, part)) {
      value = value[part];
    } else {
      return undefined;
    }
  }
  return value;
}

function hasMutableGithubDefaultBranch(url) {
  return /githubusercontent\.com\/[^/]+\/[^/]+\/(?:main|master)(?:\/|$)/i.test(url)
    || /github\.com\/[^/]+\/[^/]+\/(?:blob|tree)\/(?:main|master)(?:\/|$)/i.test(url)
    || /[?&]ref=(?:main|master)(?:&|$)/i.test(url);
}

function isAllowedOfficialSourceUrl(sourceType, url) {
  if (typeof url !== 'string') return false;
  switch (sourceType) {
    case 'RAW_SOLANA_RPC':
      return url === 'https://api.mainnet-beta.solana.com';
    case 'OFFICIAL_SOLANA_SOURCE':
      return /^https:\/\/api\.github\.com\/repos\/anza-xyz\/(?:solana-sdk|agave)\/git\/blobs\/[0-9a-f]{40}$/.test(url);
    case 'OFFICIAL_PUMP_GIT_HISTORY':
      return url.startsWith('https://api.github.com/repos/pump-fun/pump-public-docs/commits?');
    case 'OFFICIAL_PUMP_IDL':
      return /^https:\/\/raw\.githubusercontent\.com\/pump-fun\/pump-public-docs\/[0-9a-f]{40}\/idl\/pump\.json$/.test(url);
    case 'OFFICIAL_ANCHOR_SOURCE':
      return /^https:\/\/api\.github\.com\/repos\/otter-sec\/anchor\/git\/blobs\/[0-9a-f]{40}$/.test(url);
    case 'OFFICIAL_OLD_FAITHFUL_DOC':
      return url.startsWith('https://docs.old-faithful.net/');
    case 'OFFICIAL_OLD_FAITHFUL_HEAD':
      return url.startsWith('https://files.old-faithful.net/978/');
    case 'OFFICIAL_PUMP_GITHUB_API':
      return /^https:\/\/api\.github\.com\/repos\/pump-fun\/pump-public-docs\/(?:releases|tags)\?/.test(url);
    case 'LOCAL_IMMUTABLE_HASH_CHAIN':
      return url === 'file:///opt/data/research-scratch/pump-activation-evidence-epoch978/scratch-inventory.json';
    default:
      return false;
  }
}

const OFFLINE_BUILTIN_IMPORTS = new Set(['node:crypto', 'node:fs', 'node:path', 'node:url']);

function staticImportSpecifiers(source) {
  const specifiers = [];
  const pattern = /(?:^|[;\n])\s*(?:import|export)\s+(?:[^'"\n;]*?\s+from\s*)?['"]([^'"]+)['"]/gm;
  for (const match of source.matchAll(pattern)) specifiers.push(match[1]);
  return specifiers;
}

function executableSurface(source) {
  let output = '';
  let state = 'code';
  let quote = '';
  for (let index = 0; index < source.length; index += 1) {
    const current = source[index];
    const next = source[index + 1];
    if (state === 'line-comment') {
      if (current === '\n') { state = 'code'; output += '\n'; } else output += ' ';
      continue;
    }
    if (state === 'block-comment') {
      if (current === '*' && next === '/') { output += '  '; index += 1; state = 'code'; }
      else output += current === '\n' ? '\n' : ' ';
      continue;
    }
    if (state === 'string') {
      if (current === '\\') { output += '  '; index += 1; continue; }
      if (current === quote) { output += ' '; state = 'code'; } else output += current === '\n' ? '\n' : ' ';
      continue;
    }
    if (current === '/' && next === '/') { output += '  '; index += 1; state = 'line-comment'; continue; }
    if (current === '/' && next === '*') { output += '  '; index += 1; state = 'block-comment'; continue; }
    if (current === "'" || current === '"' || current === '`') { quote = current; output += ' '; state = 'string'; continue; }
    output += current;
  }
  return output;
}

export function validateTrackedResearchPaths(paths) {
  const errors = [];
  for (const rawPath of paths) {
    const path = String(rawPath).replaceAll('\\', '/');
    if (/^docs\/research\/(?:responses\/|.*\.body$|responses\/.*\.meta\.json$)/i.test(path)) {
      errors.push(`raw scratch response must not be tracked: ${path}`);
    }
  }
  return [...new Set(errors)].sort();
}

export function validateCitationModuleSources(sources, entry = 'scripts/ci-research-citations.mjs') {
  const errors = [];
  const visited = new Set();
  const visit = (path) => {
    if (visited.has(path)) return;
    visited.add(path);
    const source = sources[path];
    if (typeof source !== 'string') {
      errors.push(`offline module graph is missing local source: ${path}`);
      return;
    }
    const surface = executableSurface(source);
    if (/\bimport\s*\(/.test(source) || /\bimport\s*\(/.test(surface)) {
      errors.push(`dynamic import is forbidden in offline module graph: ${path}`);
    }
    if (/\b(?:createRequire|require)\s*\(/.test(source)) {
      errors.push(`forbidden loader capability in offline module graph: ${path}`);
    }
    const forbiddenTerms = [
      ['create', 'Require'].join(''),
      'require',
      ['getBuiltin', 'Module'].join(''),
      ['_linked', 'Binding'].join(''),
      ['bind', 'ing'].join(''),
      'fetch',
      ['Web', 'Socket'].join(''),
      ['XMLHttp', 'Request'].join(''),
      'eval',
      'Function',
    ];
    for (const term of forbiddenTerms) {
      const invocation = new RegExp(`\\b${term.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}\\s*(?:\\.|\\()`, 'u');
      if (invocation.test(surface)) errors.push(`forbidden loader/network capability ${term} in offline module graph: ${path}`);
    }
    for (const specifier of staticImportSpecifiers(source)) {
      if (OFFLINE_BUILTIN_IMPORTS.has(specifier)) continue;
      if (specifier.startsWith('./') || specifier.startsWith('../')) {
        const target = posix.normalize(posix.join(posix.dirname(path), specifier));
        visit(target);
      } else {
        errors.push(`forbidden import ${specifier} in offline module graph: ${path}`);
      }
    }
  };
  visit(entry);
  return [...new Set(errors)].sort();
}

function collectCitationModuleSources(root, entry) {
  const sources = {};
  const pending = [entry];
  while (pending.length > 0) {
    const path = pending.pop();
    if (Object.prototype.hasOwnProperty.call(sources, path)) continue;
    const absolute = resolve(root, path);
    if (!existsSync(absolute) || !statSync(absolute).isFile()) continue;
    const source = readFileSync(absolute, 'utf8');
    sources[path] = source;
    for (const specifier of staticImportSpecifiers(source)) {
      if (specifier.startsWith('./') || specifier.startsWith('../')) {
        pending.push(posix.normalize(posix.join(posix.dirname(path), specifier)));
      }
    }
  }
  return sources;
}

export function validateCitationModuleGraph({ root = process.cwd(), entry = 'scripts/ci-research-citations.mjs' } = {}) {
  return validateCitationModuleSources(collectCitationModuleSources(root, entry), entry);
}

function collectResearchPaths(root) {
  const base = resolve(root, 'docs/research');
  if (!existsSync(base)) return [];
  const output = [];
  const walk = (absolute, relative) => {
    for (const name of readdirSync(absolute).sort()) {
      const childAbsolute = resolve(absolute, name);
      const childRelative = `${relative}/${name}`;
      const stats = statSync(childAbsolute);
      if (stats.isDirectory()) walk(childAbsolute, childRelative);
      else if (stats.isFile()) output.push(childRelative);
    }
  };
  walk(base, 'docs/research');
  return output;
}

function validateScratchManifest(scratchManifest, errors) {
  if (!isRecord(scratchManifest)) {
    errors.push('scratch manifest must be an object');
    return new Map();
  }
  if (scratchManifest.schemaVersion !== 'PUMP_ACTIVATION_SCRATCH_MANIFEST_1') {
    errors.push('scratch manifest schemaVersion mismatch');
  }
  if (scratchManifest.rawScratchResponsesCommitted !== false) {
    errors.push('raw scratch responses must not be committed');
  }
  if (scratchManifest.totalScratchFiles !== 201) errors.push('scratch total file count must equal 201');
  const inventory = scratchManifest.inventory;
  const sidecar = scratchManifest.inventoryChecksumSidecar;
  if (!isRecord(inventory)
    || inventory.path !== 'scratch-inventory.json'
    || inventory.bytes !== 36806
    || inventory.sha256 !== '2251ec21a1ef905fd41e5a62e198f6bcf6ff163add20f29ceaac3392ec691c2c') {
    errors.push('scratch inventory binding is missing or drifted');
  }
  if (!isRecord(sidecar)
    || sidecar.path !== 'scratch-inventory.sha256'
    || sidecar.bytes !== 96
    || sidecar.sha256 !== '5ee43fc45ffa26dcbc82d89d52afd0769d9f8661251e4e5fc7dea2f1dcbf5fbb'
    || sidecar.content !== '2251ec21a1ef905fd41e5a62e198f6bcf6ff163add20f29ceaac3392ec691c2c  36806  scratch-inventory.json') {
    errors.push('scratch inventory checksum sidecar binding is missing or drifted');
  }
  if (!isRecord(scratchManifest.sourceInventory)
    || scratchManifest.sourceInventory.schemaVersion !== 'PHASE7A_SCRATCH_INVENTORY_1'
    || scratchManifest.sourceInventory.fileCountExcludingInventoryAndSidecar !== 199
    || scratchManifest.sourceInventory.root !== '/opt/data/research-scratch/pump-activation-evidence-epoch978'
    || !Array.isArray(scratchManifest.sourceInventory.files)
    || scratchManifest.sourceInventory.files.length !== 199) {
    errors.push('scratch source inventory must contain exactly 199 files');
    return new Map();
  }
  const reconstructedInventory = `${JSON.stringify(scratchManifest.sourceInventory, null, 2)}\n`;
  const reconstructedSha256 = createHash('sha256').update(reconstructedInventory).digest('hex');
  if (Buffer.byteLength(reconstructedInventory) !== 36806
    || reconstructedSha256 !== '2251ec21a1ef905fd41e5a62e198f6bcf6ff163add20f29ceaac3392ec691c2c') {
    errors.push('scratch source inventory digest does not match the pinned inventory hash');
  }
  const files = new Map();
  for (const file of scratchManifest.sourceInventory.files) {
    if (!isRecord(file) || typeof file.path !== 'string' || !Number.isSafeInteger(file.bytes) || file.bytes < 0 || !SHA256.test(file.sha256 ?? '')) {
      errors.push('scratch source inventory contains malformed file binding');
      continue;
    }
    if (files.has(file.path)) errors.push(`scratch source inventory has duplicate path: ${file.path}`);
    files.set(file.path, file);
  }
  return files;
}

export function validateResearchCitationData({ evidence, scratchManifest, documents }) {
  const errors = [];
  try {
    if (sha256(canonicalJson(evidence)) !== TRUSTED_EVIDENCE_CANONICAL_SHA256) {
      errors.push('trusted evidence semantic digest mismatch');
    }
    if (sha256(canonicalJson(scratchManifest)) !== TRUSTED_SCRATCH_CANONICAL_SHA256) {
      errors.push('trusted scratch semantic digest mismatch');
    }
  } catch {
    errors.push('trusted evidence semantic digest could not be computed');
  }
  if (typeof documents?.[DEFAULT_DOCUMENT] !== 'string'
    || sha256(documents[DEFAULT_DOCUMENT]) !== TRUSTED_DOCUMENT_SHA256) {
    errors.push('trusted citation document digest mismatch');
  }
  const scratchFiles = validateScratchManifest(scratchManifest, errors);
  if (!isRecord(evidence)) return [...errors, 'activation evidence must be an object'].sort();

  if (evidence.schemaVersion !== 'PUMP_ACTIVATION_EVIDENCE_EPOCH_978_1') errors.push('activation evidence schemaVersion mismatch');
  if (evidence.status !== 'CANDIDATE_UNAPPROVED') errors.push('activation status must remain CANDIDATE_UNAPPROVED');
  if (evidence.activationVerdict !== 'HOLD_UNPROVEN_ACTIVATION') errors.push('activation verdict must remain HOLD_UNPROVEN_ACTIVATION');
  if (evidence.approved !== false) errors.push('approved must remain false');
  if (evidence.researchReady !== false) errors.push('researchReady must remain false');
  if (evidence.pilotEligible !== false) errors.push('pilotEligible must remain false');
  if (evidence.promotionCount !== 0) errors.push('promotion count must remain zero');
  if (evidence.registryMutationPerformed !== false) errors.push('registry mutation performed must remain false');

  if (!isRecord(evidence.scratchBinding)
    || evidence.scratchBinding.manifestPath !== DEFAULT_SCRATCH
    || evidence.scratchBinding.totalScratchFiles !== scratchManifest?.totalScratchFiles
    || evidence.scratchBinding.inventoryBytes !== scratchManifest?.inventory?.bytes
    || evidence.scratchBinding.inventorySha256 !== scratchManifest?.inventory?.sha256
    || evidence.scratchBinding.inventoryChecksumSidecarBytes !== scratchManifest?.inventoryChecksumSidecar?.bytes
    || evidence.scratchBinding.inventoryChecksumSidecarSha256 !== scratchManifest?.inventoryChecksumSidecar?.sha256
    || evidence.scratchBinding.rawResponsesCommitted !== false) {
    errors.push('scratch inventory binding is missing or inconsistent');
  }

  if (!isRecord(evidence.authorizations)
    || !exactStringSet(Object.keys(evidence.authorizations), EXPECTED_AUTHORIZATION_KEYS)
    || Object.values(evidence.authorizations).some((value) => value !== false)) {
    errors.push('every authorization flag must exist exactly once and remain false');
  }

  if (!Array.isArray(evidence.registryEntries) || evidence.registryEntries.length !== EXPECTED_ENTRY_IDS.length) {
    errors.push('registry evidence must contain exactly ten entries');
  } else {
    const ids = evidence.registryEntries.map((entry) => entry?.entryId);
    if (JSON.stringify([...ids].sort()) !== JSON.stringify([...EXPECTED_ENTRY_IDS].sort())) {
      errors.push('registry evidence entry IDs must equal the ten expected entries');
    }
    for (const entry of evidence.registryEntries) {
      if (!isRecord(entry)
        || entry.recommendedRegistryVerdict !== 'STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION'
        || entry.provenAtSlotRange !== false) {
        errors.push(`registry entry ${entry?.entryId ?? 'unknown'} must remain unproven activation`);
      }
    }
  }

  if (!Array.isArray(evidence.sources)) errors.push('sources must be an array');
  if (!Array.isArray(evidence.claims)) errors.push('claims must be an array');
  if (!exactStringSet((evidence.sources ?? []).map((source) => source?.sourceId), EXPECTED_SOURCE_IDS)) {
    errors.push('required source ID set must match exactly');
  }
  if (!exactStringSet((evidence.claims ?? []).map((claim) => claim?.claimId), EXPECTED_CLAIM_IDS)) {
    errors.push('required claim ID set must match exactly');
  }
  for (const duplicate of duplicateIds(evidence.sources, 'sourceId')) errors.push(`duplicate source ID: ${duplicate}`);
  for (const duplicate of duplicateIds(evidence.claims, 'claimId')) errors.push(`duplicate claim ID: ${duplicate}`);

  const sources = new Map();
  for (const source of evidence.sources ?? []) {
    if (!isRecord(source) || !IDENTIFIER.test(source.sourceId ?? '')) {
      errors.push('source ID is missing or malformed');
      continue;
    }
    sources.set(source.sourceId, source);
    if (source.primary !== true) errors.push(`source ${source.sourceId} must be a primary source or immutable local hash chain`);
    if (!ALLOWED_SOURCE_TYPES.has(source.sourceType)) errors.push(`source ${source.sourceId} source type is not on the closed primary-source allowlist`);
    if (!isAllowedOfficialSourceUrl(source.sourceType, source.url)) errors.push(`source ${source.sourceId} source URL is not allowed for its official source type`);
    if (!SHA256.test(source.responseSha256 ?? '')) errors.push(`source ${source.sourceId} response SHA-256 is missing or malformed`);
    if (!Number.isSafeInteger(source.responseBytes) || source.responseBytes < 0) errors.push(`source ${source.sourceId} response byte count is invalid`);
    if (typeof source.url !== 'string') errors.push(`source ${source.sourceId} URL is missing`);
    if (source.immutableRefRequired === true) {
      if (!IMMUTABLE_REF.test(source.immutableRef ?? '') || !source.url?.includes(source.immutableRef)) {
        errors.push(`source ${source.sourceId} immutable ref is missing from its URL`);
      }
      if (!IMMUTABLE_REF.test(source.gitBlobSha1 ?? '') || source.gitBlobIdentityVerifiedFromScratchBytes !== true) {
        errors.push(`source ${source.sourceId} Git blob identity is missing or was not verified from scratch bytes`);
      }
      if (source.immutableRefType === 'GIT_BLOB_SHA1' && source.gitBlobSha1 !== source.immutableRef) {
        errors.push(`source ${source.sourceId} Git blob ref does not match its byte-derived blob identity`);
      }
      if (!['GIT_BLOB_SHA1', 'GIT_COMMIT_SHA1'].includes(source.immutableRefType)) {
        errors.push(`source ${source.sourceId} immutable ref type is invalid`);
      }
      if (hasMutableGithubDefaultBranch(source.url ?? '')) errors.push(`source ${source.sourceId} uses a mutable GitHub main/master URL`);
    }
    let binding;
    if (source.scratchPath === 'scratch-inventory.json') binding = scratchManifest?.inventory;
    else binding = scratchFiles.get(source.scratchPath);
    if (!binding) errors.push(`source ${source.sourceId} scratch path is not bound by the scratch inventory`);
    else if (binding.sha256 !== source.responseSha256 || binding.bytes !== source.responseBytes) {
      errors.push(`source ${source.sourceId} response hash/size does not match scratch binding`);
    }
  }

  for (const claim of evidence.claims ?? []) {
    if (!isRecord(claim) || !IDENTIFIER.test(claim.claimId ?? '')) {
      errors.push('claim ID is missing or malformed');
      continue;
    }
    if (!Array.isArray(claim.sourceIds) || claim.sourceIds.length === 0) errors.push(`claim ${claim.claimId} has no source IDs`);
    for (const sourceId of claim.sourceIds ?? []) {
      const source = sources.get(sourceId);
      if (!source) errors.push(`claim ${claim.claimId} references unknown source ID ${sourceId}`);
      else if (claim.loadBearing === true
        && (source.primary !== true || !ACTIVATION_PRIMARY_SOURCE_TYPES.has(source.sourceType))) {
        errors.push(`load-bearing claim ${claim.claimId} must use an allowed primary source type`);
      }
    }
    if (!Array.isArray(claim.evidencePointers) || claim.evidencePointers.length === 0) errors.push(`claim ${claim.claimId} has no evidence pointers`);
    for (const pointer of claim.evidencePointers ?? []) {
      if (resolvePointer(evidence, pointer) === undefined) errors.push(`claim ${claim.claimId} evidence pointer does not exist: ${pointer}`);
    }
    if (!Array.isArray(claim.documentRefs) || claim.documentRefs.length === 0) errors.push(`claim ${claim.claimId} has no document references`);
    for (const ref of claim.documentRefs ?? []) {
      if (!isRecord(ref) || typeof ref.path !== 'string' || !Object.prototype.hasOwnProperty.call(documents ?? {}, ref.path)) {
        errors.push(`claim ${claim.claimId} document does not exist: ${ref?.path ?? 'missing'}`);
        continue;
      }
      const text = documents[ref.path];
      const occurrences = typeof ref.marker === 'string' ? text.split(ref.marker).length - 1 : 0;
      if (occurrences !== 1) errors.push(`claim ${claim.claimId} claim marker must appear exactly once`);
    }
  }

  const referenced = new Set((evidence.claims ?? []).flatMap((claim) => claim.sourceIds ?? []));
  for (const sourceId of referenced) if (!sources.has(sourceId)) errors.push(`referenced source ID is missing: ${sourceId}`);

  for (const [path, text] of Object.entries(documents ?? {})) {
    if (/(?:Pilot A|bandwidth(?:-cap)? preflight)\s*:\s*(?:authorized|approved|GO)\b/i.test(text)) {
      errors.push(`implicit Pilot A or bandwidth preflight authorization found in ${path}`);
    }
  }

  if (!isRecord(evidence.citationContract)
    || evidence.citationContract.documentPath !== 'docs/PHASE7A_PUMP_ACTIVATION_EVIDENCE.md'
    || evidence.citationContract.scratchManifestRequired !== true
    || evidence.citationContract.allLoadBearingClaimsRequirePrimarySources !== true
    || evidence.citationContract.mutableGithubDefaultBranchUrlsForbiddenWhenImmutableRefRequired !== true) {
    errors.push('citation contract is missing or stale');
  }

  return [...new Set(errors)].sort();
}

export function validateResearchCitationFiles({
  root = process.cwd(),
  evidencePath = DEFAULT_EVIDENCE,
  scratchPath = DEFAULT_SCRATCH,
} = {}) {
  const policyErrors = [
    ...validateCitationModuleGraph({ root }),
    ...validateTrackedResearchPaths(collectResearchPaths(root)),
  ];
  try {
    const absoluteEvidence = resolve(root, evidencePath);
    const absoluteScratch = resolve(root, scratchPath);
    if (!existsSync(absoluteEvidence)) return [...policyErrors, `activation evidence document does not exist: ${evidencePath}`].sort();
    if (!existsSync(absoluteScratch)) return [...policyErrors, `scratch manifest document does not exist: ${scratchPath}`].sort();
    const evidenceText = readFileSync(absoluteEvidence, 'utf8');
    const scratchText = readFileSync(absoluteScratch, 'utf8');
    const evidence = JSON.parse(evidenceText);
    const scratchManifest = JSON.parse(scratchText);
    const documentPath = evidence?.citationContract?.documentPath ?? DEFAULT_DOCUMENT;
    const absoluteDocument = resolve(root, documentPath);
    const documents = existsSync(absoluteDocument) ? { [documentPath]: readFileSync(absoluteDocument, 'utf8') } : {};
    const errors = [...policyErrors, ...validateResearchCitationData({ evidence, scratchManifest, documents })];
    if (sha256(evidenceText) !== TRUSTED_EVIDENCE_FILE_SHA256) errors.push('trusted evidence file digest mismatch');
    if (sha256(scratchText) !== TRUSTED_SCRATCH_FILE_SHA256) errors.push('trusted scratch file digest mismatch');
    return [...new Set(errors)].sort();
  } catch (error) {
    return [...new Set([...policyErrors, `research citation input failed closed: ${error instanceof Error ? error.message : String(error)}`])].sort();
  }
}

function runCli() {
  const errors = validateResearchCitationFiles();
  if (errors.length > 0) {
    console.error('Research citation gate FAILED');
    for (const error of errors) console.error(`- ${error}`);
    process.exit(1);
  }
  console.log('Research citation gate PASS (offline exact claim/source/scratch/HOLD contract)');
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath && invokedPath === fileURLToPath(import.meta.url)) runCli();
