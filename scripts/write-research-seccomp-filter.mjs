#!/usr/bin/env node
import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const BPF_LD_W_ABS = 0x20;
const BPF_JMP_JEQ_K = 0x15;
const BPF_RET_K = 0x06;
const SECCOMP_RET_KILL_PROCESS = 0x80000000;
const SECCOMP_RET_ERRNO_EPERM = 0x00050001;
const SECCOMP_RET_ALLOW = 0x7fff0000;
const SECCOMP_DATA_NR_OFFSET = 0;
const SECCOMP_DATA_ARCH_OFFSET = 4;

const ARCHITECTURES = {
  x64: {
    auditArch: 0xc000003e,
    localProcessSyscalls: [44, 45, 46, 47, 48, 53], // local fork/exec socketpair I/O
    syscalls: [
      41, // socket
      42, // connect
      43, // accept
      44, // sendto
      45, // recvfrom
      46, // sendmsg
      47, // recvmsg
      49, // bind
      50, // listen
      53, // socketpair
      48, // shutdown
      288, // accept4
      299, // recvmmsg
      307, // sendmmsg
    ],
  },
  arm64: {
    auditArch: 0xc00000b7,
    localProcessSyscalls: [199, 206, 207, 210, 211, 212], // local fork/exec socketpair I/O
    syscalls: [
      198, // socket
      199, // socketpair
      200, // bind
      201, // listen
      202, // accept
      203, // connect
      206, // sendto
      207, // recvfrom
      210, // shutdown
      211, // sendmsg
      212, // recvmsg
      242, // accept4
      243, // recvmmsg
      269, // sendmmsg
    ],
  },
};

function instruction(code, jumpTrue, jumpFalse, value) {
  const output = Buffer.alloc(8);
  output.writeUInt16LE(code, 0);
  output.writeUInt8(jumpTrue, 2);
  output.writeUInt8(jumpFalse, 3);
  output.writeUInt32LE(value >>> 0, 4);
  return output;
}

export function researchNetworkDenyFilter(
  architecture = process.arch,
  { allowLocalProcessSpawn = false } = {},
) {
  const definition = ARCHITECTURES[architecture];
  if (!definition) throw new Error(`unsupported seccomp architecture: ${architecture}`);
  const filters = [
    instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_ARCH_OFFSET),
    instruction(BPF_JMP_JEQ_K, 1, 0, definition.auditArch),
    instruction(BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS),
    instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_NR_OFFSET),
  ];
  const deniedSyscalls = allowLocalProcessSpawn
    ? definition.syscalls.filter((syscall) => !definition.localProcessSyscalls.includes(syscall))
    : definition.syscalls;
  for (const syscall of [...new Set(deniedSyscalls)].sort((left, right) => left - right)) {
    filters.push(
      instruction(BPF_JMP_JEQ_K, 0, 1, syscall),
      instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO_EPERM),
    );
  }
  filters.push(instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW));
  return Buffer.concat(filters);
}

export async function writeResearchNetworkDenyFilter(
  path,
  architecture = process.arch,
  options = {},
) {
  await writeFile(path, researchNetworkDenyFilter(architecture, options), { mode: 0o600 });
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : null;
if (invokedPath === import.meta.url) {
  if (process.argv.length !== 3) {
    process.stderr.write('usage: write-research-seccomp-filter.mjs <output-path>\n');
    process.exit(2);
  }
  try {
    await writeResearchNetworkDenyFilter(process.argv[2]);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
