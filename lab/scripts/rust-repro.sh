# Reproducible lab Rust builds (LAB review's nit on C4-5: a binary's sha256 named its checkout, /lab/code/repo-<sha7>,
# so two commits' binaries compared only when built at one path). Source this file, then before each build:
#
#   rust_repro "$REPO" || exit 1   # REPO: the checkout's root (the directory that holds lab/)
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
# until that shell exits; a subshell runs under its parent's (it inherits the open lock). The same sha256 needs the same
# LAB_RUST_ROOT and the same toolchain (rustc, cargo, the C compiler). RUSTFLAGS and CFLAGS are extended from their
# values at the first call, not replaced.
#
# rust_repro fails (non-zero, nothing built) when:
# - a path has a space (RUSTFLAGS splits on spaces);
# - LAB_RUST_ROOT is not a directory this user owns and can write (a shared /tmp);
# - $LAB_RUST_ROOT/lab is something other than a symlink (a real directory there would make ln create lab/lab, and an
#   old tree would be built silently; it is never deleted here);
# - the link does not point at $REPO/lab afterwards.
# A RUST_REPRO_FD inherited without its open lock (an environment passed on by systemd-run or ssh) takes the lock again.
# lab/scripts/test-rust-repro.sh tests these cases.
#
# Measured 2026-10-19 on a two-crate copy (a binary with ../core and zstd-sys) at two paths: remap alone gave two
# sha256 (the ../core path in -C metadata); through the link they were equal, with no path left in either binary. On the
# NAS (#410): three builds of lab-live from three checkouts of two commits gave one sha256.

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

rust_repro_locked() {  # is RUST_REPRO_FD an open fd on <root>/.lock in this shell?
  [ -n "${RUST_REPRO_FD:-}" ] && [ -e "/dev/fd/$RUST_REPRO_FD" ] &&
    [ "$(readlink -f "/dev/fd/$RUST_REPRO_FD")" = "$(cd "$1" && pwd -P)/.lock" ]
}

rust_repro() {
  local repo=${1:?rust_repro: give the checkout root} root=${LAB_RUST_ROOT:-/tmp/lab-rust} home p
  home=${CARGO_HOME:-${HOME:?rust_repro: neither CARGO_HOME nor HOME is set}/.cargo}
  for p in "$repo" "$root" "$home"; do
    case "$p" in *[[:space:]]*) echo "rust_repro: a path with a space: '$p'" >&2; return 1 ;; esac
  done
  [ -d "$repo/lab" ] || { echo "rust_repro: no lab/ in $repo" >&2; return 1; }
  mkdir -p "$root" || return 1
  if [ ! -d "$root" ] || [ -L "$root" ] || [ ! -O "$root" ] || [ ! -w "$root" ]; then
    echo "rust_repro: $root is not a directory this user owns and can write" >&2
    return 1
  fi
  if ! rust_repro_locked "$root"; then
    exec {RUST_REPRO_FD}>"$root/.lock" || return 1
    flock "$RUST_REPRO_FD" || return 1
    export RUST_REPRO_FD
    # the flags' base: the first call's, also when an inherited environment brings them along with a stale fd
    export RUST_REPRO_RUSTFLAGS="${RUST_REPRO_RUSTFLAGS-${RUSTFLAGS:-}}" RUST_REPRO_CFLAGS="${RUST_REPRO_CFLAGS-${CFLAGS:-}}"
  fi
  if [ -e "$root/lab" ] && [ ! -L "$root/lab" ]; then
    echo "rust_repro: $root/lab is not a symlink (left as it is): move it away, then build again" >&2
    return 1
  fi
  ln -sfn "$repo/lab" "$root/lab" && [ "$(readlink "$root/lab")" = "$repo/lab" ] ||
    { echo "rust_repro: $root/lab does not point at $repo/lab" >&2; return 1; }
  export LAB_RUST="$root/lab"
  # only the link's root and CARGO_HOME: cargo and rustc see the checkout through the link, so the flags are the same
  # for every checkout (they are in cargo's fingerprints: a target dir copied from another checkout is reused)
  RUSTFLAGS="$RUST_REPRO_RUSTFLAGS$(rust_repro_map --remap-path-prefix= /src "$root")$(rust_repro_map --remap-path-prefix= /cargo "$home")"
  CFLAGS="$RUST_REPRO_CFLAGS$(rust_repro_map -ffile-prefix-map= /src "$root")$(rust_repro_map -ffile-prefix-map= /cargo "$home")"
  export RUSTFLAGS="${RUSTFLAGS# }" CFLAGS="${CFLAGS# }"
}
