#!/usr/bin/env python3
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import zipfile

MAX_FILES = 64
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 1_048_576
MAX_COMPRESSED_BYTES = 1_048_576
ALLOWED_SUFFIXES = {'.json', '.txt', '.sha256'}

def fail(code):
    raise RuntimeError(code)

def validate_name(name, is_directory):
    if not name or '\\' in name or name.startswith('/'):
        fail('ARTIFACT_PATH_UNSAFE')
    if is_directory:
        if not name.endswith('/') or name.endswith('//'):
            fail('ARTIFACT_PATH_UNSAFE')
        name = name[:-1]
    parts = name.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        fail('ARTIFACT_PATH_UNSAFE')
    path = PurePosixPath(*parts)
    if path.as_posix() != name:
        fail('ARTIFACT_PATH_UNSAFE')
    return path

def member_type(info):
    if info.create_system != 3:
        return stat.S_IFDIR if info.is_dir() else stat.S_IFREG
    return stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)

def inspect(archive):
    if archive.stat().st_size > MAX_COMPRESSED_BYTES:
        fail('ARTIFACT_COMPRESSED_BYTES_EXCEEDED')
    with zipfile.ZipFile(archive, 'r') as bundle:
        members = bundle.infolist()
        files = [entry for entry in members if not entry.is_dir()]
        if not files or len(files) > MAX_FILES:
            fail('ARTIFACT_ENTRY_SET_INVALID')
        seen = set()
        regular_paths = set()
        total = 0
        validated = []
        for entry in members:
            path = validate_name(entry.filename, entry.is_dir())
            normalized = path.as_posix()
            if normalized in seen:
                fail('ARTIFACT_DUPLICATE_PATH')
            seen.add(normalized)
            kind = member_type(entry)
            if entry.is_dir():
                if kind not in {0, stat.S_IFDIR}:
                    fail('ARTIFACT_NONREGULAR_TYPE')
                continue
            if kind not in {0, stat.S_IFREG}:
                fail('ARTIFACT_NONREGULAR_TYPE')
            if entry.flag_bits & 0x1:
                fail('ARTIFACT_ENCRYPTION_FORBIDDEN')
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                fail('ARTIFACT_IMAGE_PAYLOAD_FORBIDDEN')
            if entry.file_size > MAX_FILE_BYTES or entry.compress_size > MAX_COMPRESSED_BYTES:
                fail('ARTIFACT_BYTES_EXCEEDED')
            regular_paths.add(normalized)
            total += entry.file_size
            if total > MAX_TOTAL_BYTES:
                fail('ARTIFACT_BYTES_EXCEEDED')
            validated.append((entry, path))
        for path in regular_paths:
            parts = path.split('/')
            if any('/'.join(parts[:index]) in regular_paths for index in range(1, len(parts))):
                fail('ARTIFACT_PATH_COLLISION')
        return validated

def extract(archive, destination, validated):
    if destination.exists():
        fail('ARTIFACT_DESTINATION_EXISTS')
    destination.mkdir(parents=True, mode=0o700)
    try:
        with zipfile.ZipFile(archive, 'r') as bundle:
            rows = []
            for entry, relative in validated:
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, 'O_NOFOLLOW'):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, 0o600)
                written = 0
                try:
                    with bundle.open(entry, 'r') as source, os.fdopen(descriptor, 'wb', closefd=False) as sink:
                        while True:
                            chunk = source.read(65_536)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > entry.file_size or written > MAX_FILE_BYTES:
                                fail('ARTIFACT_BYTES_EXCEEDED')
                            sink.write(chunk)
                        sink.flush()
                        os.fsync(sink.fileno())
                finally:
                    os.close(descriptor)
                if written != entry.file_size:
                    fail('ARTIFACT_SIZE_MISMATCH')
                rows.append({'path': relative.as_posix(), 'bytes': written, 'compressedBytes': entry.compress_size})
            return rows
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

def main():
    if len(sys.argv) != 3:
        fail('INVALID_ARTIFACT_ZIP_COMMAND')
    archive = Path(sys.argv[1]).resolve()
    destination = Path(sys.argv[2]).resolve()
    if not archive.is_file():
        fail('ARTIFACT_ZIP_MISSING')
    validated = inspect(archive)
    rows = extract(archive, destination, validated)
    print(json.dumps({'schemaVersion': 'PHASE8D1_ARTIFACT_ZIP_INSPECTION_1', 'files': rows}, separators=(',', ':')))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else 'ARTIFACT_ZIP_VALIDATION_FAILED', file=sys.stderr)
        raise SystemExit(1)
