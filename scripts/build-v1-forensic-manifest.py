#!/usr/bin/env python3
"""Build a read-only forensic manifest for the immutable v1 ClickHouse table."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

TABLE_UUID = "17c42dd2-2249-496e-8558-c6f41bedd731"
EXPECTED_DDL_SHA256 = "dde1157bbc706ba26c5af5409b6ef15fae4fb5559de80d643e96846c9d21cf74"
MAX_SMALL_TEXT_BYTES = 1 * 1024 * 1024
MAX_CHECKSUM_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_BINARY_BYTES = 2 * 1024 * 1024 * 1024
CANONICAL_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
CHECKSUMS_HEADER = b"checksums format version: 4\n"
EXPECTED_COLUMNS = b"""columns format version: 1
10 columns:
`slot` UInt32
`timestamp` DateTime('UTC')
`program_id` String
`signature` String
`mint` String
`kind` String
`price_lamports_per_token` Float64
`base_raw_delta` UInt64
`wsol_raw_delta` UInt64
`base_decimals` UInt8
"""
MARK_EXTENSIONS = (".cmrk2", ".cmrk4", ".mrk", ".mrk2", ".mrk3")


class StableFile(NamedTuple):
    sha256: str
    size: int
    content: bytes | None


def metadata(stat_result: os.stat_result) -> tuple[int, ...]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_size,
        stat_result.st_blocks,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def ensure_no_symlink_components(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        observed = current.lstat()
        if stat.S_ISLNK(observed.st_mode):
            raise ValueError(f"symlink path component is forbidden: {current}")
    return absolute


def stable_file(path: Path, max_bytes: int, capture: bool = False) -> StableFile:
    path = ensure_no_symlink_components(path)
    before_path = path.lstat()
    if stat.S_ISLNK(before_path.st_mode):
        raise ValueError(f"symlink input is forbidden: {path}")
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"regular file required: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if metadata(before_path) != metadata(before):
            raise ValueError(f"input changed before open: {path}")
        if before.st_size > max_bytes:
            raise ValueError(f"input exceeds byte limit: {path}")
        digest = hashlib.sha256()
        chunks: list[bytes] | None = [] if capture else None
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"input exceeds byte limit: {path}")
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        if total != before.st_size or metadata(after) != metadata(before):
            raise ValueError(f"input changed while being read: {path}")
        return StableFile(digest.hexdigest(), total, b"".join(chunks) if chunks is not None else None)
    finally:
        os.close(descriptor)


def stable_text(path: Path, encoding: str = "utf8", max_bytes: int = MAX_SMALL_TEXT_BYTES) -> tuple[str, str]:
    snapshot = stable_file(path, max_bytes, capture=True)
    assert snapshot.content is not None
    return snapshot.content.decode(encoding), snapshot.sha256


def ensure_directory(path: Path) -> None:
    path = ensure_no_symlink_components(path)
    observed = path.lstat()
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise ValueError(f"real directory required: {path}")


def tree_inventory(
    path: Path,
    include_directories: bool = False,
    exclude_top_level: frozenset[str] = frozenset(),
) -> tuple[dict[str, tuple[int, ...]], int, int]:
    ensure_directory(path)
    inventory: dict[str, tuple[int, ...]] = {}
    apparent = 0
    allocated = 0

    def visit(directory: Path) -> None:
        nonlocal apparent, allocated
        directory_stat = directory.lstat()
        relative_directory = directory.relative_to(path).as_posix() or "."
        inventory[f"d:{relative_directory}"] = metadata(directory_stat)
        if include_directories:
            allocated += directory_stat.st_blocks * 512
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            if directory == path and entry.name in exclude_top_level:
                continue
            entry_path = Path(entry.path)
            entry_stat = entry_path.lstat()
            relative = entry_path.relative_to(path).as_posix()
            if stat.S_ISLNK(entry_stat.st_mode):
                raise ValueError(f"symlink in snapshot tree is forbidden: {entry_path}")
            if stat.S_ISDIR(entry_stat.st_mode):
                visit(entry_path)
            elif stat.S_ISREG(entry_stat.st_mode):
                inventory[f"f:{relative}"] = metadata(entry_stat)
                apparent += entry_stat.st_size
                allocated += entry_stat.st_blocks * 512
            else:
                raise ValueError(f"special file in snapshot tree is forbidden: {entry_path}")

    visit(path)
    return inventory, apparent, allocated


def require_unchanged(label: str, before: dict[str, tuple[int, ...]], after: dict[str, tuple[int, ...]]) -> None:
    if before != after:
        raise ValueError(f"{label} changed during observation")


def regular_path_metadata(path: Path) -> tuple[int, ...]:
    path = ensure_no_symlink_components(path)
    observed = path.lstat()
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise ValueError(f"regular non-symlink file required: {path}")
    return metadata(observed)


def require_path_unchanged(label: str, path: Path, before: tuple[int, ...]) -> None:
    if regular_path_metadata(path) != before:
        raise ValueError(f"{label} changed during observation")


def parse_epoch_expression(value: str) -> list[int]:
    epochs: set[int] = set()
    for item in (part.strip() for part in value.split(",")):
        if not item:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", item)
        if match is None:
            raise ValueError(f"invalid epoch expression item: {item}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            raise ValueError(f"descending epoch range: {item}")
        epochs.update(range(start, end + 1))
    return sorted(epochs)


def provenance_complete_epochs(path: Path) -> tuple[list[int], str]:
    text, digest = stable_text(path)
    match = re.search(r"^\s*-\s*Epochs volledig:\s*(.+?)\s*$", text, re.MULTILINE)
    if match is None:
        raise ValueError("PROVENANCE.md has no 'Epochs volledig' claim")
    claim = re.sub(r"\s*\([^)]*\)\s*$", "", match.group(1))
    return parse_epoch_expression(claim), digest


def supervisor_statuses(status_dir: Path) -> tuple[dict[int, str], dict[str, str]]:
    statuses: dict[int, str] = {}
    hashes: dict[str, str] = {}
    for path in sorted(status_dir.iterdir()):
        observed = path.lstat()
        if stat.S_ISLNK(observed.st_mode):
            raise ValueError(f"symlink status input is forbidden: {path}")
        if not stat.S_ISREG(observed.st_mode) or path.suffix != ".status":
            raise ValueError(f"unexpected status-directory entry: {path}")
        text, digest = stable_text(path, encoding="ascii")
        hashes[path.name] = digest
        for line in text.splitlines():
            match = re.fullmatch(r"EP\s+(\d+)\s+(done|failed)", line.strip())
            if match is None:
                raise ValueError(f"invalid supervisor status line in {path}: {line!r}")
            epoch = int(match.group(1))
            status_value = match.group(2)
            previous = statuses.get(epoch)
            if previous is not None and previous != status_value:
                raise ValueError(f"conflicting supervisor status for epoch {epoch}")
            statuses[epoch] = status_value
    return statuses, hashes


def git_state(repository: Path) -> tuple[str, str]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    commit = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("invalid parser provenance commit")
    status_output = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    ).stdout
    if status_output:
        raise ValueError("parser provenance repository is dirty")
    return commit, "clean"


def canonical_observed_at(value: str) -> str:
    if CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError("--observed-at must be canonical UTC with millisecond precision")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if canonical != value:
        raise ValueError("--observed-at is not canonical")
    return value


def ddl_contract(path: Path) -> tuple[str, list[str], str, str]:
    if path.name != "memecoin_swaps.sql":
        raise ValueError("DDL filename must identify memecoin_swaps")
    text, digest = stable_text(path)
    if digest != EXPECTED_DDL_SHA256:
        raise ValueError("DDL bytes do not match the pinned memecoin_swaps definition")
    compact = re.sub(r"\s+", " ", text).strip()
    attach = re.search(r"\bATTACH\s+TABLE\s+_\s+UUID\s+'([^']+)'", compact, re.IGNORECASE)
    if attach is None or attach.group(1) != TABLE_UUID:
        raise ValueError("DDL table UUID does not match the pinned table")
    if re.search(r"\bENGINE\s*=\s*ReplacingMergeTree\s*\(\s*slot\s*\)", compact, re.IGNORECASE) is None:
        raise ValueError("DDL engine is not ReplacingMergeTree(slot)")
    if re.search(r"\bORDER\s+BY\s+(?:\(\s*)?signature(?:\s*\))?(?:\s|$)", compact, re.IGNORECASE) is None:
        raise ValueError("DDL ORDER BY is not signature")
    if re.search(r"\bPARTITION\s+BY\b", compact, re.IGNORECASE):
        raise ValueError("DDL unexpectedly declares explicit partitioning")
    return "ReplacingMergeTree(slot)", ["signature"], "single all partition", digest


def active_parts(table_root: Path) -> tuple[list[Path], list[str]]:
    parsed_parts: list[tuple[int, int, int, int, Path]] = []
    detached_entries: list[str] = []
    for entry in sorted(table_root.iterdir()):
        observed = entry.lstat()
        if stat.S_ISLNK(observed.st_mode):
            raise ValueError(f"symlink table entry is forbidden: {entry}")
        if not stat.S_ISDIR(observed.st_mode):
            continue
        if entry.name == "detached":
            detached_entries = sorted(child.name for child in entry.iterdir())
            continue
        part_match = re.fullmatch(r"all_(\d+)_(\d+)_(\d+)(?:_(\d+))?", entry.name)
        if part_match is None:
            raise ValueError(f"noncanonical active part name for unpartitioned table: {entry.name}")
        minimum, maximum, level = (int(part_match.group(index)) for index in range(1, 4))
        mutation = int(part_match.group(4) or 0)
        canonical_name = f"all_{minimum}_{maximum}_{level}"
        if part_match.group(4) is not None:
            if mutation <= 0:
                raise ValueError(f"active part mutation suffix must be positive: {entry.name}")
            canonical_name += f"_{mutation}"
        if entry.name != canonical_name:
            raise ValueError(f"active part name does not round-trip canonically: {entry.name}")
        if minimum > maximum:
            raise ValueError(f"invalid active part block range: {entry.name}")
        count = entry / "count.txt"
        checksums = entry / "checksums.txt"
        columns = entry / "columns.txt"
        try:
            count_stat = count.lstat()
            checksums_stat = checksums.lstat()
            columns_stat = columns.lstat()
        except FileNotFoundError as error:
            raise ValueError(f"unclassified table directory: {entry}") from error
        if (not stat.S_ISREG(count_stat.st_mode)
                or not stat.S_ISREG(checksums_stat.st_mode)
                or not stat.S_ISREG(columns_stat.st_mode)):
            raise ValueError(f"active part metadata must be regular files: {entry}")
        checksums_snapshot = stable_file(checksums, MAX_CHECKSUM_BYTES, capture=True)
        columns_snapshot = stable_file(columns, MAX_SMALL_TEXT_BYTES, capture=True)
        if (checksums_snapshot.content is None
                or not checksums_snapshot.content.startswith(CHECKSUMS_HEADER)
                or len(checksums_snapshot.content) <= len(CHECKSUMS_HEADER)):
            raise ValueError(f"invalid ClickHouse checksums metadata: {entry}")
        if columns_snapshot.content != EXPECTED_COLUMNS:
            raise ValueError(f"part columns do not match the pinned DDL: {entry}")
        part_entries = [child for child in entry.iterdir() if child.is_file()]
        payload_stems = {
            child.name[:-len(".bin")]
            for child in part_entries
            if child.name.endswith(".bin")
        }
        mark_stems = {
            child.name[:-len(extension)]
            for child in part_entries
            for extension in MARK_EXTENSIONS
            if child.name.endswith(extension)
        }
        if not payload_stems or payload_stems != mark_stems:
            raise ValueError(f"active part has no ClickHouse payload/mark pair: {entry}")
        parsed_parts.append((minimum, maximum, level, mutation, entry))
    if not parsed_parts:
        raise ValueError("no active parts found")
    parsed_parts.sort(key=lambda part: (part[0], part[1], part[2], part[3], part[4].name))
    previous_maximum = -1
    for minimum, maximum, _level, _mutation, path in parsed_parts:
        if minimum <= previous_maximum:
            raise ValueError(f"overlapping active part block range: {path.name}")
        previous_maximum = maximum
    return [part[4] for part in parsed_parts], detached_entries


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-root", type=Path, required=True)
    parser.add_argument("--ddl", type=Path, required=True)
    parser.add_argument("--provenance-root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)

    parser.add_argument("--supervisor-start", type=int, default=976)
    parser.add_argument("--supervisor-end", type=int, default=990)
    parser.add_argument("--requested-start", type=int, default=976)
    parser.add_argument("--requested-end", type=int, default=1014)
    parser.add_argument("--observed-at", required=True)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    observed_at = canonical_observed_at(args.observed_at)
    parser_source = args.provenance_root / "memecoin-backfill/src/main.rs"
    provenance_doc = args.provenance_root / "PROVENANCE.md"
    for directory in [args.table_root, args.provenance_root, args.status_dir]:
        ensure_directory(directory)
    if args.table_root.name != TABLE_UUID:
        raise ValueError("table-root basename must equal the pinned ClickHouse table UUID")
    for source in [args.ddl, parser_source, provenance_doc, args.binary]:
        source.lstat()
    if args.supervisor_end < args.supervisor_start or args.requested_end < args.requested_start:
        raise ValueError("invalid epoch bounds")

    ddl_before = regular_path_metadata(args.ddl)
    binary_before = regular_path_metadata(args.binary)

    table_before, table_root_apparent, table_root_allocated = tree_inventory(args.table_root, include_directories=True)
    provenance_before, _, _ = tree_inventory(
        args.provenance_root,
        include_directories=True,
        exclude_top_level=frozenset({".git"}),
    )
    status_before, _, _ = tree_inventory(args.status_dir, include_directories=True)

    engine, order_by, partitioning, ddl_sha256 = ddl_contract(args.ddl)
    part_paths, detached_entries = active_parts(args.table_root)
    parts = []
    for path in part_paths:
        count_text, count_hash = stable_text(path / "count.txt", encoding="ascii")
        if re.fullmatch(r"(?:0|[1-9]\d*)\n?", count_text) is None:
            raise ValueError(f"invalid count.txt in {path}")
        checksums = stable_file(path / "checksums.txt", MAX_CHECKSUM_BYTES)
        _, apparent, allocated = tree_inventory(path)
        parts.append({
            "name": path.name,
            "rows": int(count_text.strip()),
            "countTxtSha256": count_hash,
            "checksumsTxtSha256": checksums.sha256,
            "fileApparentBytes": apparent,
            "fileAllocatedBytes": allocated,
        })

    repository_commit, repository_state = git_state(args.provenance_root)
    parser_snapshot = stable_file(parser_source, MAX_SOURCE_BYTES)
    binary_snapshot = stable_file(args.binary, MAX_BINARY_BYTES)
    provenance_done, provenance_hash = provenance_complete_epochs(provenance_doc)
    statuses, status_hashes = supervisor_statuses(args.status_dir)

    table_after, _, _ = tree_inventory(args.table_root, include_directories=True)
    provenance_after, _, _ = tree_inventory(
        args.provenance_root,
        include_directories=True,
        exclude_top_level=frozenset({".git"}),
    )
    status_after, _, _ = tree_inventory(args.status_dir, include_directories=True)
    require_unchanged("table snapshot", table_before, table_after)
    require_unchanged("parser provenance", provenance_before, provenance_after)
    require_unchanged("supervisor status", status_before, status_after)
    require_path_unchanged("DDL input", args.ddl, ddl_before)
    require_path_unchanged("binary input", args.binary, binary_before)
    repository_after = git_state(args.provenance_root)
    if repository_after != (repository_commit, repository_state):
        raise ValueError("parser repository Git state changed during observation")

    supervisor_domain = set(range(args.supervisor_start, args.supervisor_end + 1))
    done = sorted(epoch for epoch, status_value in statuses.items() if status_value == "done")
    failed = sorted(epoch for epoch, status_value in statuses.items() if status_value == "failed")
    unrecorded = sorted(supervisor_domain - statuses.keys())

    manifest = {
        "schemaVersion": "V1_FORENSIC_MANIFEST_1",
        "datasetContract": "TRANSACTION_NET_SWAP_V1",
        "status": "SUPERSEDED_NOT_PUMP_OOS_EVIDENCE",
        "observedAt": observed_at,
        "observedAtSource": "OPERATOR_SUPPLIED_NOT_VERIFIED_BY_GENERATOR",
        "observationMode": "READ_ONLY_FILESYSTEM_SNAPSHOT",
        "writerState": "NOT_VERIFIED_BY_GENERATOR",
        "payloadVerification": "METADATA_ONLY_NOT_REHASHED",
        "generator": "scripts/build-v1-forensic-manifest.py",
        "table": {
            "database": "default",
            "name": "memecoin_swaps",
            "uuid": TABLE_UUID,
            "ddlSha256": ddl_sha256,
            "engine": engine,
            "orderBy": order_by,
            "partitioning": partitioning,
            "activePartCount": len(parts),
            "physicalRows": sum(part["rows"] for part in parts),
            "activePartFileApparentBytes": sum(part["fileApparentBytes"] for part in parts),
            "activePartFileAllocatedBytes": sum(part["fileAllocatedBytes"] for part in parts),
            "tableRootApparentFileBytes": table_root_apparent,
            "tableRootAllocatedBytes": table_root_allocated,
            "detachedEntries": detached_entries,
            "parts": parts,
        },
        "parserProvenance": {
            "repositoryCommit": repository_commit,
            "repositoryState": repository_state,
            "sourceSha256": parser_snapshot.sha256,
            "binarySha256": binary_snapshot.sha256,
            "binaryBytes": binary_snapshot.size,
            "provenanceDocumentSha256": provenance_hash,
        },
        "coverage": {
            "claim": "INCOMPLETE",
            "reconciliationStatus": "UNRESOLVED_CONFLICTING_PROVENANCE_AND_SUPERVISOR_STATE",
            "provenanceDocumentClaimedCompleteEpochs": provenance_done,
            "supervisorStatusRangeInclusive": [args.supervisor_start, args.supervisor_end],
            "supervisorDoneEpochs": done,
            "supervisorFailedEpochs": failed,
            "supervisorUnrecordedEpochs": unrecorded,
            "supervisorStatusFileSha256": status_hashes,
            "requestedEpochsInclusive": [args.requested_start, args.requested_end],
            "note": "Coverage sources conflict and were not reconciled by starting ClickHouse; no completeness claim is permitted.",
        },
        "knownLimitations": [
            "writer quiescence was not verified by this generator",
            "payload files were not independently rehashed against ClickHouse checksums metadata",
            "requires WSOL token-balance deltas and undercovers direct native-SOL Pump trades",
            "omits loaded-address resolution and inner-event parity",
            "emits at most one dominant transaction-net swap per signature",
            "has no canonical Pump launch, curve event coordinates, or causal gate snapshots",
            "price_lamports_per_token stores an unscaled raw-unit ratio and is mislabeled",
            "physical rows include exact retry duplicates across active parts",
            "epoch coverage is incomplete and source status claims conflict",
        ],
        "allowedUses": [
            "forensic parser baseline",
            "v1-to-v2 differential coverage checks",
            "infrastructure and storage benchmarking",
            "negative capability-control fixture provenance",
        ],
        "forbiddenUses": [
            "Pump strategy optimization",
            "profitability or expectancy claims",
            "chronological out-of-sample evidence",
            "live/paper configuration promotion",
        ],
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
