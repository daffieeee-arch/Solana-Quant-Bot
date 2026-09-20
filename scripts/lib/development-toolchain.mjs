import { homedir } from 'node:os';
import { delimiter, isAbsolute, join } from 'node:path';

export const NODE_VERSION = '22.23.2';
export const RUST_VERSION = '1.97.1';
export const RUST_HOST = 'x86_64-unknown-linux-gnu';

export function toolchainPaths(env = process.env) {
  const root = env.SOLANA_TOOLCHAIN_ROOT || join(homedir(), '.local/share/solana-quant/toolchains');
  if (!isAbsolute(root)) throw new Error('SOLANA_TOOLCHAIN_ROOT must be absolute');
  return {
    root,
    node: join(root, `node-v${NODE_VERSION}-linux-x64/bin/node`),
    nodeBin: join(root, `node-v${NODE_VERSION}-linux-x64/bin`),
    cargoHome: join(root, 'cargo'),
    rustupHome: join(root, 'rustup'),
    rustBin: join(root, 'rustup/toolchains', `${RUST_VERSION}-${RUST_HOST}`, 'bin'),
  };
}

export function developmentEnvironment(env = process.env) {
  const paths = toolchainPaths(env);
  return {
    ...env,
    PATH: [paths.nodeBin, join(paths.cargoHome, 'bin'), env.PATH || '/usr/bin:/bin'].join(delimiter),
    CARGO_HOME: paths.cargoHome,
    RUSTUP_HOME: paths.rustupHome,
    RUSTUP_TOOLCHAIN: RUST_VERSION,
    RUSTUP_AUTO_INSTALL: '0',
    CARGO_BUILD_JOBS: '2',
    CARGO_NET_OFFLINE: 'true',
    MODE: 'paper',
    TRITON_LIVE_ENABLED: 'false',
    ENTRY_SHADOW_MODE: 'true',
    npm_config_cache: join(paths.root, 'npm-cache'),
    npm_config_offline: 'true',
    npm_config_audit: 'false',
    npm_config_fund: 'false',
    npm_config_update_notifier: 'false',
  };
}
