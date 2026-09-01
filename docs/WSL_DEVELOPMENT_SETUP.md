# WSL_DEVELOPMENT_SETUP.md — V2 setup and doctor contract

> **Document status: ACTIVE.** This specifies prerequisites; it does not install or change them.

## Supported development boundary

- Windows 11 with WSL2 Ubuntu. The current local reference is Ubuntu `26.04 LTS`; GitHub CI remains `ubuntu-24.04`, and doctor reports rather than hides that distro difference.
- Repository stored on the WSL ext4 filesystem, for example `/home/<user>/code/Solana-Quant-Bot`; do not develop or run durability tests under `/mnt/c`.
- Dataset root stored outside the Git checkout on WSL ext4, for example `/home/<user>/solana-quant-data`, and supplied through a task-specific variable such as `SOLANA_QUANT_DATA_ROOT`.
- No automatic `sudo`, package-manager, `rustup`, `uv`, npm-global or shell-profile mutation. A failed doctor reports the missing prerequisite and stops.

Atomic rename, locking and `fsync` behavior are part of replay correctness; filesystem placement is therefore a correctness requirement, not only a performance preference.

## Exact contracts and candidate local versions

| Tool | Status | Version/value | Notes |
|---|---|---|---|
| Node.js | **EXACT EXISTING CI CONTRACT** | `22.23.2` | Matches `.github/workflows/ci.yml`; do not update the lockfile with an unreviewed Node major |
| npm | transitional CI environment | bundled/selected with pinned Node; record exact output | `package-lock.json` v3 is authoritative; a future repository toolchain file should pin npm before treating its version as enforced |
| Rust | **EXACT EXISTING CI CONTRACT** | release `1.97.1` | Use the named rustup toolchain; verify with `rustc +1.97.1 -Vv` |
| Cargo | **EXACT EXISTING CI CONTRACT** | `cargo +1.97.1` | Record `cargo +1.97.1 -V`; do not substitute the default toolchain |
| rustfmt/clippy | **EXACT EXISTING CI CONTRACT** | components of Rust `1.97.1` | Required by existing gates |
| Python | **CANDIDATE LOCAL VERSION** | uv-managed CPython `3.13.15` | Not a V2 contract until the Python-workspace PR tests dependencies and commits `pyproject.toml`/`uv.lock` |
| uv | **CANDIDATE LOCAL VERSION** | `0.12.5` | Not a V2 contract until that same reviewed workspace/compatibility decision |
| rustc LLVM | expected existing toolchain observation | `22.1.6`, verify with `rustc +1.97.1 -Vv` | Embedded Rust compiler backend expected by the existing freeze |
| system Clang/LLVM | current requirement | `NONE` / `NOT_REQUIRED` for PR 1 gates | Pin a version only when an owning native dependency proves the need, before installation |
| make/build-essential | current native prerequisite | Ubuntu `build-essential`; doctor records installed package and `make --version` | Required because locked `fs-ext` rebuilds through node-gyp; package version follows the approved Ubuntu image |

The repository contains conflicting historical Rust commit metadata for the same `1.97.1` label. V2 pins the official release/toolchain name and records the full `rustc -Vv` output; neither historical commit string is silently declared canonical.

## Native prerequisites

The doctor checks, but never installs:

- `git`, `ca-certificates` and `curl`;
- `build-essential`, including `cc`, `c++` and `make`;
- `pkg-config`;
- `rustup` with Rust `1.97.1`, `rustfmt` and `clippy`;
- Node `22.23.2` and its npm;
- the actually available Python and uv versions; CPython `3.13.15` and uv `0.12.5` are candidate local versions, not installation requirements or final pins.

Future PRs may prove a need for pinned Clang/LLVM, CMake, protobuf, OpenSSL, zlib, zstd or LZ4 development packages. They are not architecture requirements merely because they may be common in Arrow/gRPC stacks. Add each only with an owning dependency, exact supported version range and doctor check.

The current Ubuntu `build-essential`/`make` candidate version is deliberately not an architecture pin: the approved distro package supplies the native toolchain and doctor records its exact installed version. System Clang/LLVM has the explicit version value `NONE` until a real dependency owns a pin.

## Doctor specification

A future `doctor` command must be read-only, emit machine-readable JSON plus a short terminal summary, reveal no environment values/secrets and return non-zero for a required failure. PR 1 defines the checks but does not add an installer or executable.

Each check must report its own status so one missing binary does not hide later results. If the dataset root does not yet exist, inspect its nearest existing parent without creating anything.

Required report fields:

| Check | Required behavior |
|---|---|
| OS/WSL | report distro/release, kernel and WSL detection; identify drift from local reference Ubuntu `26.04 LTS`; fail if durability tests are attempted outside Linux |
| repository | resolve real path, branch, HEAD, dirty state and remote name without printing credentials |
| filesystem | use `findmnt -T <repo>` and `<dataset-root>`; require an ext-family WSL filesystem and reject `drvfs`, `9p` and `/mnt/*` |
| free space | report bytes available at repo and dataset roots; compare only with the separately approved run plan, never a universal cap |
| Node/npm | exact Node `22.23.2`; report npm version and lockfile version |
| Rust/Cargo | require toolchain `1.97.1`; report `rustc -Vv`, Cargo, rustfmt and clippy versions |
| native build | report `cc`, `c++`, `make` and `pkg-config` versions; fail current full local gates when absent |
| Python/uv | report actual versions and candidate drift without failing current gates. Require definitive versions only after the Python-workspace PR selects pins in `pyproject.toml`/`uv.lock` and tests the chosen Polars, DuckDB, PyArrow, API, marimo and MLflow stack |
| network posture | report requested mode only; never probe Triton/OF1/RPC as part of doctor |
| dataset root | require an absolute path outside the repository, reject symlinks escaping an approved root, report writability without retaining a test artifact |

Suggested read-only operator checks:

```bash
node --version
npm --version
rustup toolchain list
rustc +1.97.1 -Vv
cargo +1.97.1 -Vv
cargo +1.97.1 fmt --version
cargo +1.97.1 clippy --version
python3 --version
uv --version
cc --version
c++ --version
make --version
pkg-config --version
findmnt -T /home/<user>/code/Solana-Quant-Bot -o TARGET,FSTYPE,OPTIONS
df -B1 /home/<user>/code/Solana-Quant-Bot /home/<user>/solana-quant-data
```

These commands are diagnostic examples, not an installation script. Replace `<user>` explicitly; do not copy placeholders into automation.

## Dataset-root rules

- The root is outside Git and contains immutable run/dataset identities rather than mutable “latest” truth.
- Every acquisition plan records required free space and a high-water abort policy before approval.
- `.partial` output is verified before atomic publication; restart validates existing bytes and manifests.
- No run may silently raise its disk, byte, request or runtime budget.
- Deleting datasets is a separately authorized retention operation; the doctor never deletes or cleans.

## Observation on 2026-09-01

The PR 1 read-only audit observed an Ubuntu 26.04 WSL ext4 checkout with roughly 945 GiB free. Node was `24.18.1`, npm `11.16.0`, Python `3.13.15` via uv, system Python `3.14.4`, and uv `0.12.5`; Rust/Cargo, build-essential/make/pkg-config and system Clang/LLVM were not available on `PATH`. The observed Python/uv pair informed the candidate local versions above but does not establish final V2 pins.

This is an environment observation, not a request to mutate it. Consequently, checks requiring the missing pinned toolchain must be run in GitHub CI or after a separate explicit local-install approval.
