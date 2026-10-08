# Source this file to use the pinned project toolchain for lab/ builds on the VPS.
T="${SOLANA_TOOLCHAIN_ROOT:-$HOME/.local/share/solana-quant/toolchains}"
export CARGO_HOME="$T/cargo" RUSTUP_HOME="$T/rustup" RUSTUP_TOOLCHAIN=1.97.1
export PATH="$T/cargo/bin:$T/node-v22.23.2-linux-x64/bin:$PATH"
export LAB_DATA_ROOT="${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}"
# rocksdb (pulled in by Jetstreamer via solana-ledger) runs bindgen. The VPS has libclang 18 only as a
# versioned file and no clang resource headers, so point bindgen at a local symlink and gcc's headers.
export LIBCLANG_PATH="$T/libclang"
export LD_LIBRARY_PATH="$T/libclang${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
GCC_INC=$(ls -d /usr/lib/gcc/x86_64-linux-gnu/*/include 2>/dev/null | sort -V | tail -1)
export BINDGEN_EXTRA_CLANG_ARGS="-I$GCC_INC"
# Python with DuckDB for checks and analysis (existing project-local venv).
export LAB_PY="${LAB_PY:-$T/columnar-query-313-duckdb155/bin/python}"
