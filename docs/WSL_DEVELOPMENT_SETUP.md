# Linux / WSL development setup and read-only doctor

> **Document status: ACTIVE.** The historical filename is retained for links. The user approved project-local VPS setup on 2026-09-20. The doctor and wrapper never install software.

## Supported development boundary

- WSL2 Ubuntu or an explicitly approved native Linux VPS. The earlier WSL reference is Ubuntu 26.04; the current VPS and GitHub CI use Ubuntu 24.04. The doctor reports the actual distro and kernel.
- Repository and datasets on native ext2/ext3/ext4. Reject Windows/shared mounts such as drvfs/9p and `/mnt/*`; locking, atomic rename and `fsync` are correctness requirements. Passing a filesystem check is not power-loss proof.
- On this VPS the checkout is `/home/chupa/Solana-project/Solana-bot`. **All OF1 data** belongs under `/home/chupa/Solana-project/data-old-faithful-one`, including Raw, derived layers, manifests, receipts, plans, execution evidence and research results. This existing directory is outside Git.
- Other hosts must supply their own explicit absolute dataset root. Existing roots and path components must not be symlinks; this intentionally stricter rule prevents escape/alias ambiguity. Missing roots are reported without creation.
- Existing Hyperliquid data captures must never be interrupted. Do not signal/restart their processes, change their environments, mutate shared defaults or reboot the server for Solana work.
- Linux VPS **development** does not activate the later production shadow/paper/live runtime. Acquisition still requires its own exact approved lease.

## Pinned tools and native dependencies

| Tool | Contract |
|---|---|
| Node.js | Exact `22.23.2`, matching CI |
| npm | Bundled with the approved Node archive; doctor records the exact version. Lockfile v3 remains authoritative; no independent npm pin is claimed |
| Rust/Cargo | Named rustup toolchain `1.97.1`, including rustfmt and clippy; record full `rustc -Vv` |
| Native compiler | Ubuntu build-essential (C/C++ and make), pkg-config, git, curl and CA certificates |
| Query Python / DuckDB | CPython `3.13` ABI and the hash-locked DuckDB `1.5.5` wheel for the bounded Parquet reader. The existing VPS venv uses CPython `3.13.15`. No full Gold/Python workspace pin follows |
| uv | Informational existing tool; no global Python or uv configuration change |
| Clang/CMake/protoc/system zstd | Not required by the current reviewed graphs. Bronze compiles bundled zstd C; protobuf projection is checked in |

`fs-ext` is native: install/rebuild it with the selected Node version and that version's headers. Never copy `node_modules` from WSL or another project. Keep all Cargo locks and `package-lock.json` unchanged during setup.

## Project-local toolchain layout

The Linux x86_64 wrapper uses this default layout outside the checkout:

```text
~/.local/share/solana-quant/toolchains/
  node-v22.23.2-linux-x64/
  cargo/                         # rustup proxies + isolated registry cache
  rustup/                        # isolated named Rust toolchain
  npm-cache/
  columnar-query-313-duckdb155/    # isolated, already installed query venv
```

An absolute `SOLANA_TOOLCHAIN_ROOT` may select another installation with the same layout. The wrapper checks the installed versions and Rust proxies before starting a child. It changes only that child's environment; it never sources a profile, runs `nvm use`, changes a shared default or downloads missing tools.

```bash
node scripts/with-toolchain.mjs -- node --version
node scripts/with-toolchain.mjs -- cargo +1.97.1 -V
node scripts/with-toolchain.mjs -- rustc +1.97.1 -Vv
node scripts/with-toolchain.mjs -- node scripts/doctor.mjs \
  --dataset-root /home/chupa/Solana-project/data-old-faithful-one

# Explicit data root applies only to this command and its children:
SOLANA_QUANT_DATA_ROOT=/home/chupa/Solana-project/data-old-faithful-one \
  node scripts/with-toolchain.mjs -- python --version
```

The initial `node` is only the dependency-free wrapper bootstrap (tested with the host's Node 24 and pinned Node 22). Child Node/npm commands use the project's Node 22. The wrapper fixes paper/live safety defaults, `RUSTUP_AUTO_INSTALL=0`, Cargo build jobs to two, and npm/Cargo offline defaults. Those defaults prevent implicit package fetching in normal development; **they are not an OS network sandbox**. Existing seccomp gates provide syscall denial for replay, with separately source-pinned loopback fixtures where required.

The wrapper also selects the existing query venv, verifies its CPython ABI and
installed DuckDB metadata, and sets `COLUMNAR_QUERY_PYTHON` for the Parquet gate.
An explicit absolute `COLUMNAR_QUERY_PYTHON` may select another isolated venv
with the same contract. Bare `python`/`python3` in the child use that venv;
inherited `PYTHONHOME`/`PYTHONPATH` and user-site packages cannot select another
project's modules. Bytecode writes and implicit pip/uv fetching are disabled.
Metadata/version checks do not replace actual Parquet-query tests.
`SOLANA_QUANT_DATA_ROOT`, when supplied, must pass the read-only external-root
check. Dataset commands still take explicit paths; no archived WSL plan is
rewritten or resumed. No shell profile, shared interpreter or migration wrapper
needs modification. Run commands from the intended worktree.

If a future service needs Node, its start command must also explicitly select the pinned binary/environment. No service or shell-profile change is part of this setup.

## Read-only doctor

`node scripts/doctor.mjs --dataset-root <absolute-path>` emits JSON on stdout, a short summary on stderr, and exits nonzero on required failures. Alternatively supply `SOLANA_QUANT_DATA_ROOT`; `npm run doctor -- --dataset-root ...` is a convenience entrypoint.

The doctor checks each prerequisite independently:

- Linux/WSL, architecture, distro, kernel and reference environments;
- canonical checkout path, branch, HEAD, dirty boolean and remote **names**, never credential-bearing URLs;
- explicit external dataset path, existence and permission bits, without a write probe or directory creation;
- native filesystem type and available bytes at the checkout and the dataset's nearest existing parent;
- exact Node/Rust/Cargo, npm, rustup, rustfmt/clippy, native build tools and CA-bundle presence;
- native-addon presence (ABI/behavior still requires the tests);
- available Python/uv and a separate required query-reader ABI/DuckDB metadata check; actual query behavior remains a separate gate;
- kernel seccomp/AppArmor metadata, explicitly marking an execution probe as **not run**.

It does not read `.env`, credentials, wallets, dataset contents or arbitrary environment values. It has no network client or installer. It lists installed Rust toolchains before invoking the pin, and disables rustup auto-install. Missing tools do not hide later checks. Free space is reported without pretending that an acquisition budget has been approved. The separate seccomp/full test gates must pass before claiming execution isolation works.

## Explicitly approved installation and dependency preparation

Only perform these steps under an explicit installation instruction. The 2026-09-20 instruction covers this VPS's isolated development setup; it does not grant standing permission to upgrade shared tools.

1. Retrieve the Node `22.23.2` Linux x64 archive and `SHASUMS256.txt` from `https://nodejs.org/download/release/v22.23.2/`. Verify SHA-256 before extraction into the isolated toolchain root.
2. Retrieve a pinned rustup installer and its checksum from `https://static.rust-lang.org/rustup/archive/<version>/x86_64-unknown-linux-gnu/`. Record the installer identity. Set **both** project-local `CARGO_HOME` and `RUSTUP_HOME`, then use `--no-modify-path --profile minimal --default-toolchain 1.97.1 --component rustfmt,clippy`. This setup used rustup `1.29.1`.
3. Run the four static dependency gates before fetching Cargo graphs:

```bash
node scripts/assert-pump-protocol-v2-offline.mjs --static
node scripts/assert-of1-planner-offline.mjs --static
node scripts/assert-of1-bronze-offline.mjs --static
node scripts/assert-of1-parquet-offline.mjs --static
```

4. Fetch only locked package graphs. Network exceptions are explicit **per dependency-preparation command**, for example wrapper + `npm ci --offline=false --ignore-scripts --no-audit --no-fund`, or wrapper + `env CARGO_NET_OFFLINE=false cargo +1.97.1 fetch --locked --manifest-path <manifest>`. Include Pump, OF1 recorder, Bronze and retained reducer manifests. Registry/toolchain downloads are not Solana-provider calls or canonical data evidence.
5. Review install hooks; rebuild locked native Node dependencies offline under the existing seccomp launcher with `allowLocalProcessSpawn: true`, passing `--nodedir=<toolchain-root>/node-v22.23.2-linux-x64` to use the already verified headers. Keep installation receipts/logs outside Git. No forced dependency upgrades.

Official installation and environment references: [Node release files](https://nodejs.org/download/release/v22.23.2/), [rustup custom installation roots](https://rust-lang.github.io/rustup/installation/index.html#choosing-where-to-install), [rustup auto-install control](https://rust-lang.github.io/rustup/environment-variables.html). An archive checksum binds the downloaded bytes; it is not a separately verified signing-key attestation.

## Protect other projects during builds and tests

Start heavy commands in a dedicated, temporary user scope. These are development caps, not acquisition budgets or permanent service settings:

```bash
systemd-run --user --scope --quiet \
  -p CPUQuota=200% -p MemoryHigh=5G -p MemoryMax=6G -p TasksMax=256 \
  -p CPUWeight=25 -p IOWeight=25 \
  nice -n 10 node scripts/with-toolchain.mjs -- npm test -- --maxWorkers=2 --minWorkers=1
```

Use the same scope limits for builds, native rebuilds and full Rust gates, and avoid concurrent heavy suites. Scope names may be supplied to make monitoring explicit. A scope is not a persistent service. If scope creation fails, stop heavy work instead of silently running without limits. Observe free memory, load, disk availability and the original Hyperliquid process identities read-only; pause/stop only Solana work if contention develops. Resource limits reduce contention but are not proof of capture continuity; do not claim full data continuity solely from living PIDs.

For full gates on this VPS, select `TMPDIR=/home/chupa/Solana-project/data-old-faithful-one/tmp-vps-integration` for the child command. This existing native directory has room for the Parquet fixture gate and keeps monitor Unix-socket paths short enough. Longer nested audit paths can exceed the Unix socket path limit. Do not edit global temporary-directory defaults.

The current unprivileged Bubblewrap/user-namespace probes failed while the project's existing seccomp launcher worked. No AppArmor or sysctl change is required by this development profile. Full gates include separate permitted local HTTP/TLS fixtures. Task-specific host results are recorded in [the VPS integration evidence](operations/VPS_WSL_INTEGRATION.md).

## Validation and evidence boundary

Run the doctor, policy/citation checks, full Node suite, typecheck/build and the current CI Rust gates under the selected environment. The [CI workflow](../.github/workflows/ci.yml) is the authoritative full list and includes **all four** isolated gates: Pump protocol, OF1 planner/recorder, Bronze decoder and Parquet/DuckDB, plus retained reducer/support formatting, clippy/test/build and patch integrity. Local gates use the installed query venv; the CI-only query installer is never invoked on the VPS.

A green environment and fixture suite does not establish authentic acquisition, Silver, research readiness or a trading edge. No provider request, collector, dashboard service or trade is started by the setup. Migration and existing Parquet-query reproduction have separate preserved evidence; fresh Raw processing and integrated CI require their own results.

## Historical observations

The 2026-09-01 WSL audit observed Ubuntu 26.04/ext4, roughly 945 GiB free, PATH Node `24.18.1`, npm `11.16.0`, uv Python `3.13.15`, system Python `3.14.4` and uv `0.12.5`; Rust/native build prerequisites were absent from PATH. The 2026-09-05 observation later found a Rust installation without rustup on PATH and a Node-native ABI mismatch. These are historical host observations, not portable setup instructions.

The 2026-09-20 VPS preflight observed Ubuntu 24.04.5, native ext4, eight vCPUs, approximately 13 GiB available RAM and 440 GiB free disk, system Python `3.12.3`, uv `0.12.5`, PATH Node `24.18.1`, native build tools, and no Rust toolchain. The user's subsequent instruction authorized the isolated installation described above, while preserving active Hyperliquid captures.
