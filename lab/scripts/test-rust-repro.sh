#!/bin/bash
# Tests of lab/scripts/rust-repro.sh (no cargo, no build): bash lab/scripts/test-rust-repro.sh. Each case runs in its
# own bash under a fresh LAB_RUST_ROOT inside one mktemp directory, removed at the end. The last line is
# "test-rust-repro: N passed" (exit 0) or the failed cases (exit 1). nas_m1c4.sh runs it before STEP=build and repro.
set -uo pipefail

export HELPER=$(cd "$(dirname "$0")" && pwd -P)/rust-repro.sh
TMP=$(mktemp -d /tmp/test-rust-repro.XXXXXX)
cleanup() { case "$TMP" in /tmp/test-rust-repro.??????) rm -rf "$TMP" ;; esac; }
trap cleanup EXIT
mkdir -p "$TMP/co-a/lab" "$TMP/co-b/deeper/lab" "$TMP/home"
export CARGO_HOME=$TMP/home
unset RUST_REPRO_FD RUST_REPRO_RUSTFLAGS RUST_REPRO_CFLAGS RUSTFLAGS CFLAGS LAB_RUST
passed=0 failed=()

check() {  # name, expected rc (0 or "nonzero"), then the case's bash code (rust-repro.sh sourced, R the root)
  local name=$1 want=$2 code=$3 rc
  LAB_RUST_ROOT=$TMP/root-$name bash -c "R=\$LAB_RUST_ROOT; T=$TMP; . '$HELPER'; $code" > "$TMP/$name.log" 2>&1
  rc=$?
  if { [ "$want" = 0 ] && [ $rc = 0 ]; } || { [ "$want" = nonzero ] && [ $rc != 0 ]; }; then
    passed=$((passed + 1))
  else
    failed+=("$name (rc $rc, want $want): $(tail -3 "$TMP/$name.log" | tr '\n' ' ')")
  fi
}

# the link, the flags and LAB_RUST; a second call stacks nothing; another checkout moves the link
check link 0 '
  rust_repro "$T/co-a" && [ "$(readlink "$R/lab")" = "$T/co-a/lab" ] && [ "$LAB_RUST" = "$R/lab" ] || exit 1
  f1=$RUSTFLAGS; rust_repro "$T/co-a" && [ "$RUSTFLAGS" = "$f1" ] || exit 2
  [ "$RUSTFLAGS" = "--remap-path-prefix=$R=/src --remap-path-prefix=$T/home=/cargo" ] || exit 3
  [ "$CFLAGS" = "-ffile-prefix-map=$R=/src -ffile-prefix-map=$T/home=/cargo" ] || exit 4
  rust_repro "$T/co-b/deeper" && [ "$(readlink "$R/lab")" = "$T/co-b/deeper/lab" ] && [ "$RUSTFLAGS" = "$f1" ] || exit 5'
# flags from before the first call are kept in front, once
check base 0 '
  export RUSTFLAGS="-C opt-level=3"; rust_repro "$T/co-a" && rust_repro "$T/co-b/deeper" || exit 1
  [ "$RUSTFLAGS" = "-C opt-level=3 --remap-path-prefix=$R=/src --remap-path-prefix=$T/home=/cargo" ] || exit 2'
# LAB review on #410: a real directory where the link goes is refused and left untouched (no lab/lab inside it)
check realdir nonzero '
  mkdir -p "$R/lab/live" && echo old > "$R/lab/live/old.rs"
  rust_repro "$T/co-a"; rc=$?
  [ -d "$R/lab" ] && [ ! -L "$R/lab" ] && [ -f "$R/lab/live/old.rs" ] && [ ! -e "$R/lab/lab" ] || exit 0  # untouched, else rc 0 = a failed test
  exit $rc'
# a file where the link goes: refused too
check realfile nonzero 'mkdir -p "$R" && echo x > "$R/lab" && rust_repro "$T/co-a"'
# a dangling symlink from an earlier checkout is replaced
check dangling 0 'mkdir -p "$R" && ln -s "$T/gone/lab" "$R/lab" && rust_repro "$T/co-a" && [ "$(readlink "$R/lab")" = "$T/co-a/lab" ]'
# refusals: no lab/ in the checkout, a space in a path, the root a symlink
check nolab nonzero 'mkdir -p "$T/empty" && rust_repro "$T/empty"'
check space nonzero 'mkdir -p "$T/a b/lab" && rust_repro "$T/a b"'
check rootlink nonzero 'mkdir -p "$T/elsewhere" && ln -s "$T/elsewhere" "$R" && rust_repro "$T/co-a"'
# a root this user cannot write (skipped as root, who can)
if [ "$(id -u)" != 0 ]; then
  check rootro nonzero 'mkdir -p "$R" && chmod 0555 "$R" && rust_repro "$T/co-a"; rc=$?; chmod 0755 "$R"; exit $rc'
fi
# the lock: held by the first call; a second shell waits; a subshell runs under it
check lock 0 '
  rust_repro "$T/co-a" || exit 1
  ( rust_repro "$T/co-a" ) || exit 2
  # another shell without the lock (no RUST_REPRO_FD, the fd closed for it) waits: killed by timeout, rc 124
  timeout 2 env -u RUST_REPRO_FD bash -c ". \"$HELPER\"; rust_repro \"$T/co-a\"" {RUST_REPRO_FD}>&-; [ $? = 124 ] || exit 3
  flock -n "$R/.lock" true && exit 4
  exit 0'
# LAB review nit: an inherited RUST_REPRO_FD without its open fd (systemd-run, ssh) takes the lock again
check stalefd 0 '
  export RUST_REPRO_FD=77 RUST_REPRO_RUSTFLAGS=""; rust_repro "$T/co-a" || exit 1
  [ "$RUST_REPRO_FD" != 77 ] && [ -e "/dev/fd/$RUST_REPRO_FD" ] || exit 2
  flock -n "$R/.lock" true && exit 3
  exit 0'
# an fd of that number open on another file is not the lock either
check otherfd 0 '
  exec 9>"$T/other"; export RUST_REPRO_FD=9; rust_repro "$T/co-a" || exit 1
  [ "$RUST_REPRO_FD" != 9 ] && [ -e "/dev/fd/$RUST_REPRO_FD" ] || exit 2
  flock -n "$R/.lock" true && exit 3
  exit 0'

if [ ${#failed[@]} = 0 ]; then
  echo "test-rust-repro: $passed passed"
  exit 0
fi
printf 'test-rust-repro: FAILED %s\n' "${failed[@]}"
echo "test-rust-repro: $passed passed, ${#failed[@]} failed"
exit 1
