#!/usr/bin/env bash
# Cursor Cloud Agent bootstrap for the Solana Quant Platform V2.
#
# Scope: this script configures the disposable Cloud Agent VM only. It does NOT
# apply to the WSL2 development host, whose read-only doctor contract in
# docs/WSL_DEVELOPMENT_SETUP.md still forbids implicit toolchain installation.
#
# It is idempotent and safe to re-run: it pins the exact CI toolchains, installs
# locked dependencies, and compiles the Research Cockpit.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# --- Pinned Node.js 22.23.2 via nvm (exact CI contract) ---
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
nvm install 22.23.2 >/dev/null
nvm alias default 22.23.2 >/dev/null
node_bin="$NVM_DIR/versions/node/v22.23.2/bin"
export PATH="$node_bin:$PATH"

# The base image ships its own newer `node` earlier in PATH than nvm. Symlink the
# pinned binaries into the first writable PATH entry so every shell (interactive
# terminals, tests, and the app) resolves Node 22.23.2 without sourcing nvm.
if [ -d /usr/local/cargo/bin ] && [ -w /usr/local/cargo/bin ]; then
  for b in node npm npx; do ln -sf "$node_bin/$b" "/usr/local/cargo/bin/$b"; done
fi

# --- Pinned Rust 1.97.1 with rustfmt + clippy (exact CI contract) ---
rustup toolchain install 1.97.1 --profile minimal --component clippy,rustfmt

# --- Locked Pump protocol crate dependencies (mirrors the CI pre-fetch) ---
cargo +1.97.1 fetch --manifest-path rust/pump-protocol-v2/Cargo.toml --locked

# --- Node dependencies (rebuilds the native fs-ext addon via node-gyp) ---
npm ci

# --- Compile the backend and the Research Cockpit frontend ---
npm run build

echo "Cloud Agent environment ready: node $(node --version), $(cargo +1.97.1 --version)"
