# Reproducible lab Rust builds (LAB review's nit on C4-5: a binary's sha256 named its checkout, /lab/code/repo-<sha7>,
# so two commits' binaries compared only when built at one path). Source this file, then before each build:
#
#   rust_repro "$REPO"          # REPO: the checkout's root (the directory that holds lab/)
#   cargo build --release --locked --manifest-path "$LAB_RUST/live/Cargo.toml"     # never `cd` + plain cargo
#
# Two things put the checkout's path into a binary:
# - rustc writes source paths into it (panic locations, file!()): the crate's own, its target dir (OUT_DIR) and the
#   registry's under CARGO_HOME. RUSTFLAGS --remap-path-prefix maps them to /src and /cargo; CFLAGS -ffile-prefix-map
#   does the same for the C code the -sys crates compile.
# - cargo's -C metadata, the crates' symbol hashes, holds the absolute path of a path dependency outside the workspace
#   root: lab-live's ../core (core-py's too). One workspace over lab/ would merge six lock files. Instead every build
#   reaches the checkout through one fixed path: $LAB_RUST_ROOT/lab (default /tmp/lab-rust/lab), a symlink to
#   $REPO/lab. Cargo keeps --manifest-path's path as given (a `cd` would hand it the resolved one).
#
# The symlink is shared, so rust_repro takes a lock ($LAB_RUST_ROOT/.lock) on its first call in a shell and holds it
# until that shell exits (a subshell runs under its parent's). Hosts that must agree use the same LAB_RUST_ROOT.
# RUSTFLAGS and CFLAGS are extended from their values at the first call, not replaced. A path with a space is refused
# (RUSTFLAGS splits on spaces).
#
# Measured 2026-10-19 on a two-crate copy (a binary with ../core and zstd-sys) at two paths: remap alone gave two
# sha256 (the ../core path in -C metadata); through the link they were equal, with no path left in either binary.

rust_repro_map() {  # flag (e.g. --remap-path-prefix=), its target, then the paths: one mapping per distinct path
  local flag=$1 to=$2 seen=" " p q
  shift 2
  for p in "$@"; do
    [ -n "$p" ] || continue
    for q in "$p" "$( (cd "$p" 2>/dev/null && pwd -P) || echo "$p")"; do
      case "$seen" in *" $q "*) continue ;; esac
      seen="$seen$q "
      printf ' %s%s=%s' "$flag" "$q" "$to"
    done
  done
}

rust_repro() {
  local repo=${1:?rust_repro: give the checkout root} root=${LAB_RUST_ROOT:-/tmp/lab-rust} home p
  home=${CARGO_HOME:-${HOME:?rust_repro: neither CARGO_HOME nor HOME is set}/.cargo}
  for p in "$repo" "$root" "$home"; do
    case "$p" in *[[:space:]]*) echo "rust_repro: a path with a space: '$p'" >&2; return 1 ;; esac
  done
  [ -d "$repo/lab" ] || { echo "rust_repro: no lab/ in $repo" >&2; return 1; }
  mkdir -p "$root"
  if [ -z "${RUST_REPRO_FD:-}" ]; then
    exec {RUST_REPRO_FD}>"$root/.lock"
    flock "$RUST_REPRO_FD"
    export RUST_REPRO_FD RUST_REPRO_RUSTFLAGS="${RUSTFLAGS:-}" RUST_REPRO_CFLAGS="${CFLAGS:-}"
  fi
  ln -sfn "$repo/lab" "$root/lab"
  export LAB_RUST="$root/lab"
  # only the link's root and CARGO_HOME: cargo and rustc see the checkout through the link, so the flags are the same
  # for every checkout (they are in cargo's fingerprints: a target dir copied from another checkout is reused)
  RUSTFLAGS="$RUST_REPRO_RUSTFLAGS$(rust_repro_map --remap-path-prefix= /src "$root")$(rust_repro_map --remap-path-prefix= /cargo "$home")"
  CFLAGS="$RUST_REPRO_CFLAGS$(rust_repro_map -ffile-prefix-map= /src "$root")$(rust_repro_map -ffile-prefix-map= /cargo "$home")"
  export RUSTFLAGS="${RUSTFLAGS# }" CFLAGS="${CFLAGS# }"
}
