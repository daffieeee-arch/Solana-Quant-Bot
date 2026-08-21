#!/usr/bin/env node
import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const BPF_LD_W_ABS = 0x20;
const BPF_JMP_JEQ_K = 0x15;
const BPF_RET_K = 0x06;
const SECCOMP_RET_KILL_PROCESS = 0x80000000;
const SECCOMP_RET_ERRNO_EPERM = 0x00050001;
const SECCOMP_RET_ALLOW = 0x7fff0000;
const DEFINITIONS = {
  x64: { auditArch: 0xc000003e, syscalls: [42, 44, 46, 307] }, // connect, sendto, sendmsg, sendmmsg
  arm64: { auditArch: 0xc00000b7, syscalls: [203, 206, 211, 269] },
};

function instruction(code, jumpTrue, jumpFalse, value) {
  const output = Buffer.alloc(8);
  output.writeUInt16LE(code, 0);
  output.writeUInt8(jumpTrue, 2);
  output.writeUInt8(jumpFalse, 3);
  output.writeUInt32LE(value >>> 0, 4);
  return output;
}

export function cockpitOutboundDenyFilter(architecture = process.arch) {
  const definition = DEFINITIONS[architecture];
  if (!definition) throw new Error(`unsupported seccomp architecture: ${architecture}`);
  const filters = [
    instruction(BPF_LD_W_ABS, 0, 0, 4),
    instruction(BPF_JMP_JEQ_K, 1, 0, definition.auditArch),
    instruction(BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS),
    instruction(BPF_LD_W_ABS, 0, 0, 0),
  ];
  for (const syscall of definition.syscalls) {
    filters.push(instruction(BPF_JMP_JEQ_K, 0, 1, syscall));
    filters.push(instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO_EPERM));
  }
  filters.push(instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW));
  return Buffer.concat(filters);
}

export async function writeCockpitOutboundDenyFilter(path, architecture = process.arch) {
  await writeFile(path, cockpitOutboundDenyFilter(architecture), { mode: 0o600 });
}

const invoked = process.argv[1] ? pathToFileURL(process.argv[1]).href : null;
if (invoked === import.meta.url) {
  if (process.argv.length !== 3) {
    process.stderr.write('usage: write-cockpit-seccomp-filter.mjs <output-path>\n');
    process.exit(2);
  }
  writeCockpitOutboundDenyFilter(process.argv[2]).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
