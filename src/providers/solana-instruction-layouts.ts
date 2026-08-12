/**
 * Fail-closed Solana instruction-data layouts, pinned for Gate A review on 2026-07-29.
 *
 * Primary official sources:
 * - Pump public IDLs: https://github.com/pump-fun/pump-public-docs/tree/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl
 * - Pump Carbon decoders: https://github.com/pump-fun/carbon/tree/bcc206b4715a2f02025d398e865b0d2036e0419b
 * - Raydium CPMM program: https://github.com/raydium-io/raydium-cp-swap/tree/78f254e1023751e706df7dc15c453fc3e046697c
 * - Raydium SDK V2: https://github.com/raydium-io/raydium-sdk-V2/tree/fb2d829a559f9b6ca95922e4e6c69e3b5bddc95c
 * - Meteora DLMM SDK/IDL: https://github.com/MeteoraAg/dlmm-sdk/tree/fb02e51ae677bbd18e76543f702dae40632426db
 * - Moonit SDK V4 IDL: https://github.com/gomoonit/moonit-sdk/tree/95012b8935be0edfd1842c58cbfe46fafb9d8513/src/idl/v4
 * - Moonit official V1 IDL MigrationTarget tags 0/1: https://github.com/gomoonit/moonit-sdk/blob/87debaf79ef1178bcf17b3351015ff677eef35a0/src/solana/program/tokenLaunchpadIdlV1.ts
 * - Borsh encoding: https://github.com/near/borsh-rs/tree/7fc21fe52d39b3d3c7409b6ab272976a21b5f482
 *
 * Moonit provenance limit: no verified source-commit-to-current-deployed-BPF linkage is
 * public. V4 documents MigrationTarget tag 0 only, while the official V1 IDL documents
 * tags 0/1 and confirmed successful mainnet TokenMint signature
 * 65ZxBBgNPSJHtznPhJVWRFuAayCC2fDFAVTyruKRcoFRmX4iiqkAwxMx9prmySVQobMVhGZViFjtvH9trNRSdeJ3
 * uses tag 1. The release policy explicitly accepts that observed 0/1 union and rejects
 * every larger tag; this is not a claim of source-to-BPF equivalence.
 */
const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const MAX_BASE58_CHARACTERS = 5_600;
const MAX_INSTRUCTION_BYTES = 4_096;

export type SolanaInstructionLayout =
  | 'pumpFunCreate'
  | 'pumpFunCreateV2'
  | 'pumpSwapCreatePool'
  | 'raydiumCpmmInitialize'
  | 'raydiumCpmmInitializeWithPermission'
  | 'meteoraInitializeLbPair'
  | 'meteoraInitializePermissionLbPair'
  | 'meteoraInitializeCustomizablePermissionlessLbPair'
  | 'meteoraInitializeLbPair2'
  | 'meteoraInitializeCustomizablePermissionlessLbPair2'
  | 'moonitTokenMint';

const DISCRIMINATORS: Record<SolanaInstructionLayout, readonly number[]> = {
  pumpFunCreate: [24, 30, 200, 40, 5, 28, 7, 119],
  pumpFunCreateV2: [214, 144, 76, 236, 95, 139, 49, 180],
  pumpSwapCreatePool: [233, 146, 209, 142, 207, 104, 64, 188],
  raydiumCpmmInitialize: [175, 175, 109, 31, 13, 152, 155, 237],
  raydiumCpmmInitializeWithPermission: [63, 55, 254, 65, 49, 178, 89, 121],
  meteoraInitializeLbPair: [45, 154, 237, 210, 221, 15, 166, 92],
  meteoraInitializePermissionLbPair: [108, 102, 213, 85, 251, 3, 53, 21],
  meteoraInitializeCustomizablePermissionlessLbPair: [46, 39, 41, 135, 111, 183, 200, 64],
  meteoraInitializeLbPair2: [73, 59, 36, 120, 237, 83, 108, 198],
  meteoraInitializeCustomizablePermissionlessLbPair2: [243, 73, 129, 126, 51, 19, 241, 107],
  moonitTokenMint: [3, 44, 164, 184, 123, 13, 245, 179],
};

export function decodeBase58(value: string): Uint8Array | undefined {
  if (value.length > MAX_BASE58_CHARACTERS) return undefined;
  if (!value) return new Uint8Array();
  const bytes = [0];
  for (const character of value) {
    const digit = BASE58_ALPHABET.indexOf(character);
    if (digit < 0) return undefined;
    let carry = digit;
    for (let index = 0; index < bytes.length; index += 1) {
      carry += bytes[index]! * 58;
      bytes[index] = carry & 0xff;
      carry >>= 8;
    }
    while (carry > 0) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
    if (bytes.length > MAX_INSTRUCTION_BYTES) return undefined;
  }
  for (let index = 0; value[index] === '1' && index < value.length - 1; index += 1) bytes.push(0);
  if (bytes.length > MAX_INSTRUCTION_BYTES) return undefined;
  return Uint8Array.from(bytes.reverse());
}

class InstructionCursor {
  private offset = 0;
  private readonly utf8 = new TextDecoder('utf-8', { fatal: true });

  constructor(private readonly data: Uint8Array) {}

  get remaining(): number {
    return this.data.length - this.offset;
  }

  take(length: number): Uint8Array {
    if (!Number.isSafeInteger(length) || length < 0 || length > this.remaining) {
      throw new Error('truncated_instruction_data');
    }
    const output = this.data.subarray(this.offset, this.offset + length);
    this.offset += length;
    return output;
  }

  u8(): number {
    return this.take(1)[0]!;
  }

  u16(): number {
    const value = this.take(2);
    return value[0]! + value[1]! * 0x100;
  }

  u32(): number {
    const value = this.take(4);
    return (value[0]! + value[1]! * 0x100 + value[2]! * 0x1_0000 + value[3]! * 0x100_0000) >>> 0;
  }

  i32(): number {
    const value = this.u32();
    return value > 0x7fff_ffff ? value - 0x1_0000_0000 : value;
  }

  fixedBytes(length: number): void {
    this.take(length);
  }

  bool(): boolean {
    const value = this.u8();
    if (value !== 0 && value !== 1) throw new Error('invalid_borsh_bool');
    return value === 1;
  }

  optionU64(): void {
    const tag = this.u8();
    if (tag === 0) return;
    if (tag === 1) {
      this.take(8);
      return;
    }
    throw new Error('invalid_borsh_option');
  }

  string(maxBytes = MAX_INSTRUCTION_BYTES): void {
    const length = this.u32();
    if (length > maxBytes || length > this.remaining) throw new Error('invalid_borsh_string_length');
    this.utf8.decode(this.take(length));
  }

  discriminator(expected: readonly number[]): void {
    const actual = this.take(8);
    if (actual.length !== expected.length || expected.some((byte, index) => actual[index] !== byte)) {
      throw new Error('wrong_discriminator');
    }
  }

  finish(): void {
    if (this.remaining !== 0) throw new Error('trailing_instruction_data');
  }
}

function validatePumpCreate(cursor: InstructionCursor, withV2Fields: boolean): void {
  cursor.string();
  cursor.string();
  cursor.string();
  cursor.take(32);
  if (withV2Fields) {
    cursor.bool();
    if (cursor.remaining === 1) cursor.bool();
  }
  cursor.finish();
}

function validatePumpSwap(cursor: InstructionCursor): void {
  cursor.u16();
  cursor.take(8);
  cursor.take(8);
  cursor.take(32);
  cursor.bool();
  if (cursor.remaining === 1) cursor.bool();
  cursor.finish();
}

function validateMeteoraCustomizable(cursor: InstructionCursor): void {
  cursor.i32();
  cursor.u16();
  cursor.u16();
  const activationType = cursor.u8();
  if (activationType > 1) throw new Error('invalid_activation_type');
  cursor.bool();
  cursor.optionU64();
  cursor.bool();
  cursor.u8();
  const concreteFunctionType = cursor.u8();
  if (concreteFunctionType > 1) throw new Error('invalid_concrete_function_type');
  const collectFeeMode = cursor.u8();
  if (collectFeeMode > 1) throw new Error('invalid_collect_fee_mode');
  cursor.fixedBytes(60);
  cursor.finish();
}

/**
 * Official DLMM v0.12.0 `InitializeLbPair2Params` is exactly
 * `active_id: i32` followed by `padding: [u8; 96]`. The fixed padding bytes
 * have no Borsh length prefix or enum/bool domain.
 * https://github.com/MeteoraAg/dlmm-sdk/blob/fb02e51ae677bbd18e76543f702dae40632426db/idls/dlmm.json#L7024-L7042
 */
function validateMeteoraLbPair2(cursor: InstructionCursor): void {
  cursor.i32();
  cursor.fixedBytes(96);
  cursor.finish();
}

function validateMoonit(cursor: InstructionCursor): void {
  cursor.string(32);
  cursor.string(10);
  cursor.string(200);
  cursor.u8();
  const collateralCurrency = cursor.u8();
  if (collateralCurrency !== 0) throw new Error('invalid_collateral_currency');
  cursor.take(8);
  const curveType = cursor.u8();
  if (curveType > 4) throw new Error('invalid_curve_type');
  const migrationTarget = cursor.u8();
  if (migrationTarget > 1) throw new Error('invalid_migration_target');
  cursor.u16();
  cursor.take(8);
  cursor.finish();
}

export function validateSolanaInstructionData(layout: SolanaInstructionLayout, encodedData: string): boolean {
  const data = decodeBase58(encodedData);
  if (!data || data.length > MAX_INSTRUCTION_BYTES) return false;
  try {
    const cursor = new InstructionCursor(data);
    cursor.discriminator(DISCRIMINATORS[layout]);
    switch (layout) {
      case 'pumpFunCreate':
        validatePumpCreate(cursor, false);
        break;
      case 'pumpFunCreateV2':
        validatePumpCreate(cursor, true);
        break;
      case 'pumpSwapCreatePool':
        validatePumpSwap(cursor);
        break;
      case 'raydiumCpmmInitialize':
        cursor.take(8);
        cursor.take(8);
        cursor.take(8);
        cursor.finish();
        break;
      case 'raydiumCpmmInitializeWithPermission': {
        cursor.take(8);
        cursor.take(8);
        cursor.take(8);
        const creatorFeeOn = cursor.u8();
        if (creatorFeeOn > 2) throw new Error('invalid_creator_fee_on');
        cursor.finish();
        break;
      }
      case 'meteoraInitializeLbPair':
        cursor.i32();
        cursor.u16();
        cursor.finish();
        break;
      case 'meteoraInitializePermissionLbPair': {
        cursor.i32();
        cursor.u16();
        cursor.u16();
        cursor.u8();
        const activationType = cursor.u8();
        if (activationType > 1) throw new Error('invalid_activation_type');
        cursor.u16();
        const concreteFunctionType = cursor.u8();
        if (concreteFunctionType > 1) throw new Error('invalid_concrete_function_type');
        const collectFeeMode = cursor.u8();
        if (collectFeeMode > 1) throw new Error('invalid_collect_fee_mode');
        cursor.finish();
        break;
      }
      case 'meteoraInitializeCustomizablePermissionlessLbPair':
      case 'meteoraInitializeCustomizablePermissionlessLbPair2':
        validateMeteoraCustomizable(cursor);
        break;
      case 'meteoraInitializeLbPair2':
        validateMeteoraLbPair2(cursor);
        break;
      case 'moonitTokenMint':
        validateMoonit(cursor);
        break;
    }
    return true;
  } catch {
    return false;
  }
}
