import { homedir } from 'node:os';
import { delimiter, dirname, isAbsolute, join } from 'node:path';

export const NODE_VERSION = '22.23.2';
export const RUST_VERSION = '1.97.1';
export const RUST_HOST = 'x86_64-unknown-linux-gnu';
// The bounded DuckDB reader only; these are not future Gold workspace pins.
export const QUERY_PYTHON_ABI = '3.13';
export const DUCKDB_VERSION = '1.5.5';
export const QUERY_VERSION_PROBE = "import sys,platform,json,importlib.metadata; print(json.dumps({'implementation':platform.python_implementation(),'abi':'.'.join(map(str,sys.version_info[:2])),'python':platform.python_version(),'duckdb':importlib.metadata.version('duckdb')}))";

export function validQueryVersion(output) {
  try {
    const value = JSON.parse(output);
    return value.implementation === 'CPython' && value.abi === QUERY_PYTHON_ABI && value.duckdb === DUCKDB_VERSION;
  } catch { return false; }
}

export function toolchainPaths(env = process.env) {
  const root = env.SOLANA_TOOLCHAIN_ROOT || join(homedir(), '.local/share/solana-quant/toolchains');
  if (!isAbsolute(root)) throw new Error('SOLANA_TOOLCHAIN_ROOT must be absolute');
  const queryPython = env.COLUMNAR_QUERY_PYTHON || join(root, 'columnar-query-313-duckdb155/bin/python');
  if (!isAbsolute(queryPython)) throw new Error('COLUMNAR_QUERY_PYTHON must be absolute');
  return {
    root,
    queryPython,
    node: join(root, `node-v${NODE_VERSION}-linux-x64/bin/node`),
    nodeBin: join(root, `node-v${NODE_VERSION}-linux-x64/bin`),
    cargoHome: join(root, 'cargo'),
    rustupHome: join(root, 'rustup'),
    rustBin: join(root, 'rustup/toolchains', `${RUST_VERSION}-${RUST_HOST}`, 'bin'),
  };
}

export function developmentEnvironment(env = process.env) {
  const paths = toolchainPaths(env);
  const selected = {
    ...env,
    PATH: [paths.nodeBin, join(paths.cargoHome, 'bin'), dirname(paths.queryPython), env.PATH || '/usr/bin:/bin'].join(delimiter),
    COLUMNAR_QUERY_PYTHON: paths.queryPython,
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONNOUSERSITE: '1',
    UV_OFFLINE: '1',
    PIP_NO_INDEX: '1',
    PIP_DISABLE_PIP_VERSION_CHECK: '1',
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
  delete selected.PYTHONHOME;
  delete selected.PYTHONPATH;
  return selected;
}
