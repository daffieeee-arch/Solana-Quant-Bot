# WSL_DEVELOPMENT_SETUP.md — V2 setup and doctor contract

> **Document status: ACTIVE.** This specifies prerequisites; it does not install or change them.

## Supported development boundary

- Windows 11 with WSL2 Ubuntu. The current local reference is Ubuntu `26.04 LTS`; GitHub CI remains `ubuntu-24.04`, and doctor reports rather than hides that distro difference.
- Repository stored on the WSL ext4 filesystem, for example `/home/<user>/code/Solana-Quant-Bot`; do not develop or run durability tests under `/mnt/c`.
- Dataset root stored outside the Git checkout on WSL ext4, for example `/home/<user>/solana-quant-data`, and supplied through a task-specific variable such as `SOLANA_QUANT_DATA_ROOT`.
- No automatic `sudo`, package-manager, `rustup`, `uv`, npm-global or shell-profile mutation. A failed doctor reports the missing prerequisite and stops.

Atomic rename, locking and `fsync` behavior are part of replay correctness; filesystem placement is therefore a correctness requirement, not only a performance preference.

## Version contract

| Tool | V2/CI contract | Notes |
|---|---|---|
| Node.js | `22.23.2` exactly | Matches `.github/workflows/ci.yml`; do not update the lockfile with an unreviewed Node major |
| npm | the npm bundled/selected with the pinned Node environment; record the exact output | `package-lock.json` v3 is authoritative; a future repository toolchain file should pin npm before treating its version as enforced |
| Rust | `1.97.1` release exactly | Use the named rustup toolchain; verify with `rustc +1.97.1 -Vv` |
| Cargo | `cargo +1.97.1` from the Rust `1.97.1` toolchain | Record `cargo +1.97.1 -V`; do not substitute the default toolchain |
| rustfmt/clippy | components of Rust `1.97.1` | Required by existing gates |
| Python | CPython `3.13.15` managed by uv | Becomes mechanically enforced when the Python workspace/lock lands; do not substitute the current system Python |
| uv | `0.12.5` for initial V2 setup | Record `uv --version`; any pin change is a reviewed dependency/tooling change |
| rustc LLVM | expected `22.1.6`; must be verified with `rustc +1.97.1 -Vv` once the pinned toolchain exists | This is the embedded Rust compiler backend expected by the existing freeze |
| system Clang/LLVM | version `NONE` for PR 1/current gates (`NOT_REQUIRED`) | Current manifests do not require it. Pin an exact system version only when an owning native dependency proves the need, before installation |
| make/build-essential | Ubuntu `build-essential` distribution package, exact installed package and `make --version` recorded by doctor | Required now because the locked `fs-ext` dependency rebuilds through node-gyp; package version follows the approved Ubuntu image |

The repository contains conflicting historical Rust commit metadata for the same `1.97.1` label. V2 pins the official release/toolchain name and records the full `rustc -Vv` output; neither historical commit string is silently declared canonical.

## Native prerequisites

The doctor checks, but never installs:

- `git`, `ca-certificates` and `curl`;
- `build-essential`, including `cc`, `c++` and `make`;
- `pkg-config`;
- `rustup` with Rust `1.97.1`, `rustfmt` and `clippy`;
- Node `22.23.2` and its npm;
- CPython `3.13.15` and uv `0.12.5` before the Python workspace is introduced.

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
| Python/uv | require/report CPython `3.13.15` and uv `0.12.5` once Python V2 gates are active |
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

The PR 1 read-only audit observed an Ubuntu 26.04 WSL ext4 checkout with roughly 945 GiB free. Node was `24.18.1`, npm `11.16.0`, Python `3.13.15` via uv, system Python `3.14.4`, and uv `0.12.5`; Rust/Cargo, build-essential/make/pkg-config and system Clang/LLVM were not available on `PATH`.

This is an environment observation, not a request to mutate it. Consequently, checks requiring the missing pinned toolchain must be run in GitHub CI or after a separate explicit local-install approval.
