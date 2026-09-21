// Historical source bytes, not a reconstruction of the old executable or a new run.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { inflateRawSync } from 'node:zlib';

export const sourceHash = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
export const historicalSourceReceipt = JSON.parse(readFileSync('schemas/acquisition/of1/retained-source-bytes.json', 'utf8'));

export function frameSources(files: Buffer[]): Buffer {
  return Buffer.concat(files.flatMap(bytes => {
    const length = Buffer.alloc(8); length.writeBigUInt64LE(BigInt(bytes.length));
    return [length, bytes];
  }));
}

export function retainedSources(receipt = historicalSourceReceipt): Buffer[] {
  if (receipt.schema !== 'OF1_RETAINED_SOURCE_BYTES_1' || receipt.encoding !== 'DEFLATE_RAW_BASE64_OF_U64LE_LENGTH_PREFIXED_FILES') throw Error('unsupported historical source receipt');
  const bytes = inflateRawSync(Buffer.from(receipt.compressed_frames_base64, 'base64'), { maxOutputLength: 1024 * 1024 });
  if (bytes.length !== receipt.framed_bytes || sourceHash(bytes) !== receipt.framed_sha256) throw Error('historical source frame hash mismatch');
  const result: Buffer[] = [];
  const names = new Set<string>();
  let at = 0;
  for (const file of receipt.files) {
    if (names.has(file.path) || !file.path.startsWith('rust/of1-range-recorder/') || file.path.includes('..')) throw Error('historical source inventory mismatch');
    names.add(file.path);
    if (at + 8 > bytes.length) throw Error('historical source truncated length');
    const length = bytes.readBigUInt64LE(at); at += 8;
    if (length > BigInt(bytes.length - at)) throw Error('historical source truncated bytes');
    const content = bytes.subarray(at, at + Number(length)); at += Number(length);
    const blob = createHash('sha1').update(Buffer.from(`blob ${content.length}\0`)).update(content).digest('hex');
    if (content.length !== file.bytes || sourceHash(content) !== file.sha256 || blob !== file.git_blob) throw Error('historical source content identity mismatch');
    result.push(content);
  }
  if (at !== bytes.length) throw Error('historical source trailing bytes');
  return result;
}
