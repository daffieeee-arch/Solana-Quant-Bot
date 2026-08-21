#![allow(clippy::too_many_lines)]

use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

use serde_json::{Value, json};
use sha2::{Digest as _, Sha256};

fn fixture_path() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/phase8a/bronze-runner-rich.json")
}

fn run(input: &Path, output: &Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_phase8a-bronze-runner"))
        .arg("--input")
        .arg(input)
        .arg("--output")
        .arg(output)
        .output()
        .unwrap()
}

#[allow(clippy::naive_bytecount)]
fn verdict(output: &std::process::Output) -> Value {
    assert_eq!(
        output.stdout.iter().filter(|byte| **byte == b'\n').count(),
        1
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

fn write_fixture(temp: &tempfile::TempDir, value: &Value, name: &str) -> PathBuf {
    let path = temp.path().join(name);
    let mut bytes = serde_json::to_vec(value).unwrap();
    bytes.push(b'\n');
    fs::write(&path, bytes).unwrap();
    path
}

fn semantic_files(output: &Path) -> Vec<(String, Vec<u8>)> {
    let mut paths = vec![
        "coverage.json".to_owned(),
        "eligibility.json".to_owned(),
        "event-observations.ndjson".to_owned(),
        "metrics-snapshot.json".to_owned(),
        "quarantines.ndjson".to_owned(),
        "retry-duplicate-conflicts.json".to_owned(),
    ];
    for slot in [432_000_100_u64, 432_000_101, 432_000_102] {
        paths.push(format!("slots/{slot}.bronze.json"));
    }
    paths
        .into_iter()
        .map(|path| {
            let bytes = fs::read(output.join(&path)).unwrap();
            (path, bytes)
        })
        .collect()
}

fn regular_files(root: &Path) -> Vec<(String, Vec<u8>)> {
    fn walk(root: &Path, directory: &Path, files: &mut Vec<(String, Vec<u8>)>) {
        for entry in fs::read_dir(directory).unwrap() {
            let entry = entry.unwrap();
            let file_type = entry.file_type().unwrap();
            if file_type.is_dir() {
                walk(root, &entry.path(), files);
            } else if file_type.is_file() {
                files.push((
                    entry
                        .path()
                        .strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                    fs::read(entry.path()).unwrap(),
                ));
            } else {
                panic!("unexpected published file type");
            }
        }
    }
    let mut files = Vec::new();
    walk(root, root, &mut files);
    files.sort_by(|left, right| left.0.cmp(&right.0));
    files
}

#[cfg(target_os = "linux")]
fn build_fault_preload(temp: &tempfile::TempDir) -> PathBuf {
    let source = temp.path().join("phase8a-fault.c");
    let library = temp.path().join("phase8a-fault.so");
    fs::write(
        &source,
        r#"#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static ssize_t (*real_read_fn)(int, void *, size_t);
static int (*real_fsync_fn)(int);
static int (*real_chmod_fn)(const char *, mode_t);
static int (*real_fchmod_fn)(int, mode_t);
static int (*real_fchmodat_fn)(int, const char *, mode_t, int);
static unsigned long long target_read_bytes;
static int append_injected;
static int overwrite_injected;
static int matches_fd(int fd, const char *env_name) {
  const char *target = getenv(env_name);
  if (!target || !*target) return 0;
  char link_path[64]; char resolved[PATH_MAX];
  snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", fd);
  ssize_t n = readlink(link_path, resolved, sizeof(resolved) - 1);
  if (n < 0) return 0; resolved[n] = '\0';
  return strcmp(resolved, target) == 0;
}
static int path_has_prefix(const char *path, const char *env_name) {
  const char *prefix = getenv(env_name);
  return prefix && *prefix && path && strncmp(path, prefix, strlen(prefix)) == 0;
}
static int fd_has_prefix(int fd, const char *env_name) {
  const char *prefix = getenv(env_name);
  if (!prefix || !*prefix) return 0;
  char link_path[64]; char resolved[PATH_MAX];
  snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", fd);
  ssize_t n = readlink(link_path, resolved, sizeof(resolved) - 1);
  if (n < 0) return 0; resolved[n] = '\0';
  return strncmp(resolved, prefix, strlen(prefix)) == 0;
}
ssize_t read(int fd, void *buf, size_t count) {
  if (!real_read_fn) real_read_fn = dlsym(RTLD_NEXT, "read");
  if (!append_injected && matches_fd(fd, "PHASE8A_APPEND_TARGET")) {
    const char *target = getenv("PHASE8A_APPEND_TARGET");
    int append_fd = open(target, O_WRONLY | O_APPEND);
    if (append_fd >= 0) {
      (void)write(append_fd, " ", 1);
      (void)fsync(append_fd);
      (void)close(append_fd);
      append_injected = 1;
    }
  }
  if (!overwrite_injected && matches_fd(fd, "PHASE8A_OVERWRITE_TARGET")) {
    const char *target = getenv("PHASE8A_OVERWRITE_TARGET");
    int overwrite_fd = open(target, O_WRONLY);
    if (overwrite_fd >= 0) {
      (void)usleep(1100000);
      (void)pwrite(overwrite_fd, "X", 1, 0);
      (void)fsync(overwrite_fd);
      (void)close(overwrite_fd);
      overwrite_injected = 1;
    }
  }
  if (matches_fd(fd, "PHASE8A_FAIL_READ_PATH") && target_read_bytes >= 3ULL * 1024ULL * 1024ULL) { errno = EIO; return -1; }
  ssize_t result = real_read_fn(fd, buf, count);
  if (result > 0 && matches_fd(fd, "PHASE8A_FAIL_READ_PATH")) target_read_bytes += (unsigned long long)result;
  return result;
}
int fsync(int fd) {
  if (!real_fsync_fn) real_fsync_fn = dlsym(RTLD_NEXT, "fsync");
  if (matches_fd(fd, "PHASE8A_FAIL_FSYNC_PATH")) { errno = EIO; return -1; }
  return real_fsync_fn(fd);
}
int chmod(const char *path, mode_t mode) {
  if (!real_chmod_fn) real_chmod_fn = dlsym(RTLD_NEXT, "chmod");
  if (path_has_prefix(path, "PHASE8A_IGNORE_CHMOD_PREFIX")) return 0;
  return real_chmod_fn(path, mode);
}
int fchmod(int fd, mode_t mode) {
  if (!real_fchmod_fn) real_fchmod_fn = dlsym(RTLD_NEXT, "fchmod");
  if (fd_has_prefix(fd, "PHASE8A_IGNORE_CHMOD_PREFIX")) return 0;
  return real_fchmod_fn(fd, mode);
}
int fchmodat(int dirfd, const char *path, mode_t mode, int flags) {
  if (!real_fchmodat_fn) real_fchmodat_fn = dlsym(RTLD_NEXT, "fchmodat");
  if (path_has_prefix(path, "PHASE8A_IGNORE_CHMOD_PREFIX")) return 0;
  return real_fchmodat_fn(dirfd, path, mode, flags);
}
"#,
    )
    .unwrap();
    let status = Command::new("cc")
        .args(["-shared", "-fPIC", "-O2"])
        .arg(&source)
        .args(["-ldl", "-o"])
        .arg(&library)
        .status()
        .unwrap();
    assert!(status.success());
    library
}

#[cfg(unix)]
fn make_writable_tree(path: &Path) {
    use std::os::unix::fs::PermissionsExt as _;
    if path.is_dir() {
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        for entry in fs::read_dir(path).unwrap() {
            make_writable_tree(&entry.unwrap().path());
        }
    } else if path.exists() {
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }
}

#[test]
fn rich_fixture_succeeds_with_bronze_only_authoritative_outputs() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("run");
    let result = run(&fixture_path(), &output);

    assert_eq!(
        result.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(result.stderr.is_empty());
    let value = verdict(&result);
    assert_eq!(value["schemaVersion"], "PHASE8A_BRONZE_RUNNER_VERDICT_1");
    assert_eq!(value["status"], "SUCCEEDED");
    assert_eq!(value["sourceClass"], "SYNTHETIC_FIXTURE_ONLY");
    assert_eq!(value["evidenceBadge"], "SYNTHETIC");
    assert_eq!(value["realData"], false);
    assert_eq!(value["acceptedSilver"], false);
    assert_eq!(value["researchReady"], false);
    assert_eq!(value["outputPublished"], true);
    assert!(
        value["runId"]
            .as_str()
            .unwrap()
            .starts_with("phase8a-fixture-")
    );

    for path in [
        "run-manifest.json",
        "eligibility.json",
        "coverage.json",
        "provenance.json",
        "metrics-snapshot.json",
        "event-observations.ndjson",
        "quarantines.ndjson",
        "retry-duplicate-conflicts.json",
        "cockpit-snapshot.json",
        "aggregate-content-hash.txt",
        "reducer/coverage.ndjson",
    ] {
        assert!(output.join(path).is_file(), "missing {path}");
    }
    assert!(output.join("reducer/checkpoints").is_dir());
    assert!(output.join("reducer/slots").is_dir());
    assert!(!output.join("silver.json").exists());
    assert!(!output.join("silver.ndjson").exists());
    assert!(
        regular_files(&output)
            .iter()
            .all(|(path, _)| !path.to_ascii_lowercase().contains("silver"))
    );
    assert!(fs::metadata(&output).unwrap().permissions().readonly());
    assert!(
        fs::metadata(output.join("run-manifest.json"))
            .unwrap()
            .permissions()
            .readonly()
    );

    let metrics: Value =
        serde_json::from_slice(&fs::read(output.join("metrics-snapshot.json")).unwrap()).unwrap();
    assert_eq!(metrics["slotsCommitted"], 3);
    assert_eq!(metrics["legacyTransactions"], 4);
    assert_eq!(metrics["v0Transactions"], 1);
    assert_eq!(metrics["topLevelPumpInstructions"], 4);
    assert_eq!(metrics["innerCpiPumpInstructions"], 1);
    assert_eq!(metrics["createDiscriminatorObservations"], 1);
    assert_eq!(metrics["buyDiscriminatorObservations"], 2);
    assert_eq!(metrics["sellDiscriminatorObservations"], 1);
    assert_eq!(metrics["failedPumpTransactions"], 1);
    assert_eq!(metrics["unknownDiscriminators"], 2);
    assert_eq!(metrics["exactRetryDuplicates"], 1);
    assert_eq!(metrics["provisionalSkips"], 1);

    let observations = fs::read_to_string(output.join("event-observations.ndjson")).unwrap();
    assert!(observations.contains("181ec828051c0777"));
    assert!(observations.contains("66063d1201daebea"));
    assert!(observations.contains("33e685a4017f83ad"));
    assert!(observations.contains("SHADOW_STRUCTURAL_OBSERVATION"));
    assert!(observations.contains("UNPROVEN_ACTIVATION"));
    assert!(observations.contains("NOT_ACCEPTED_SILVER"));
    assert!(observations.contains("NOT_STRATEGY_INPUT"));
    for line in observations.lines() {
        let row: Value = serde_json::from_str(line).unwrap();
        assert_eq!(row["evidenceBadge"], "SYNTHETIC");
        assert_eq!(row["realData"], false);
        assert_eq!(row["acceptedSilver"], false);
        assert_eq!(row["researchReady"], false);
    }

    for slot in [432_000_100_u64, 432_000_101, 432_000_102] {
        assert_eq!(
            fs::read(output.join(format!("slots/{slot}.bronze.json"))).unwrap(),
            fs::read(output.join(format!("reducer/slots/{slot}.json"))).unwrap(),
        );
    }
}

#[test]
fn same_input_produces_byte_identical_retained_artifact_set() {
    let temp = tempfile::tempdir().unwrap();
    let first = temp.path().join("first");
    let second = temp.path().join("second");
    assert!(run(&fixture_path(), &first).status.success());
    assert!(run(&fixture_path(), &second).status.success());
    assert_eq!(regular_files(&first), regular_files(&second));
}

#[test]
fn callback_permutation_preserves_published_semantic_outputs() {
    let temp = tempfile::tempdir().unwrap();
    let original: Value = serde_json::from_slice(&fs::read(fixture_path()).unwrap()).unwrap();
    let mut permuted = original.clone();
    let callbacks = permuted["callbacks"].as_array_mut().unwrap();
    let order = [9_usize, 7, 5, 8, 2, 4, 6, 3, 0, 1];
    let old = callbacks.clone();
    *callbacks = order.into_iter().map(|index| old[index].clone()).collect();
    let input = write_fixture(&temp, &permuted, "permuted.json");
    let first = temp.path().join("first");
    let second = temp.path().join("second");
    assert!(run(&fixture_path(), &first).status.success());
    assert!(run(&input, &second).status.success());
    assert_eq!(semantic_files(&first), semantic_files(&second));
}

#[test]
fn conflicting_duplicate_aborts_without_successful_publication() {
    let temp = tempfile::tempdir().unwrap();
    let mut input: Value = serde_json::from_slice(&fs::read(fixture_path()).unwrap()).unwrap();
    input["callbacks"][2]["transaction"]["feeLamports"] = json!("5001");
    let path = write_fixture(&temp, &input, "conflict.json");
    let output = temp.path().join("output");
    let result = run(&path, &output);
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(
        result.stderr,
        b"phase8a-bronze-runner: policy or input rejected\n"
    );
    assert_eq!(verdict(&result)["reasonCode"], "CONFLICTING_DUPLICATE");
    assert!(!output.exists());
}

#[test]
fn rejects_overwrite_bad_sources_and_worktree_output() {
    let temp = tempfile::tempdir().unwrap();
    let existing = temp.path().join("existing");
    fs::create_dir(&existing).unwrap();
    let overwrite = run(&fixture_path(), &existing);
    assert_eq!(overwrite.status.code(), Some(2));
    assert_eq!(verdict(&overwrite)["reasonCode"], "OUTPUT_EXISTS");

    for source in [
        "REAL",
        "REAL_UNAPPROVED",
        "OLD_FAITHFUL_REMOTE",
        "HTTP_RANGE",
        "JETSTREAMER_LIVE",
        "TRITON",
        "RPC",
    ] {
        let mut value: Value = serde_json::from_slice(&fs::read(fixture_path()).unwrap()).unwrap();
        value["sourceClass"] = json!(source);
        let input = write_fixture(&temp, &value, &format!("{source}.json"));
        let output = temp.path().join(format!("out-{source}"));
        let result = run(&input, &output);
        assert_eq!(result.status.code(), Some(2), "source {source}");
        assert_eq!(verdict(&result)["reasonCode"], "SOURCE_CLASS_REJECTED");
        assert!(!output.exists());
    }

    let forbidden =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("phase8a-forbidden-output-do-not-create");
    let result = run(&fixture_path(), &forbidden);
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "WORKTREE_OUTPUT_REJECTED");
    assert!(!forbidden.exists());
}

#[cfg(unix)]
#[test]
fn rejects_symlink_input_output_and_ancestors() {
    use std::os::unix::fs::symlink;

    let temp = tempfile::tempdir().unwrap();
    let input_link = temp.path().join("input-link.json");
    symlink(fixture_path(), &input_link).unwrap();
    let result = run(&input_link, &temp.path().join("input-link-output"));
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "UNSAFE_INPUT_PATH");

    let target = temp.path().join("target");
    fs::create_dir(&target).unwrap();
    let output_link = temp.path().join("output-link");
    symlink(&target, &output_link).unwrap();
    let result = run(&fixture_path(), &output_link);
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "OUTPUT_EXISTS");

    let real_parent = temp.path().join("real-parent");
    fs::create_dir(&real_parent).unwrap();
    let linked_parent = temp.path().join("linked-parent");
    symlink(&real_parent, &linked_parent).unwrap();
    let result = run(&fixture_path(), &linked_parent.join("output"));
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "UNSAFE_OUTPUT_PATH");
    assert!(!real_parent.join("output").exists());
}

#[test]
fn rejects_unknown_fields_malformed_arrays_and_hard_bounds() {
    let temp = tempfile::tempdir().unwrap();
    let base: Value = serde_json::from_slice(&fs::read(fixture_path()).unwrap()).unwrap();
    let cases = [
        ("unknown", {
            let mut v = base.clone();
            v["unexpected"] = json!(true);
            v
        }),
        ("array", {
            let mut v = base.clone();
            v["callbacks"] = json!({});
            v
        }),
        ("slots", {
            let mut v = base.clone();
            v["expectedSlotRange"]["endExclusive"] = json!(432_000_117_u64);
            v
        }),
        ("runtime", {
            let mut v = base.clone();
            v["config"]["maxRuntimeSeconds"] = json!(31);
            v
        }),
        ("records", {
            let mut v = base.clone();
            v["callbacks"] = Value::Array(vec![base["callbacks"][0].clone(); 129]);
            v
        }),
        ("tx-per-slot", {
            let mut v = base.clone();
            v["callbacks"][4]["block"]["executedTransactionCount"] = json!(33);
            v
        }),
    ];
    for (name, value) in cases {
        let input = write_fixture(&temp, &value, &format!("{name}.json"));
        let output = temp.path().join(format!("out-{name}"));
        let result = run(&input, &output);
        assert_eq!(
            result.status.code(),
            Some(2),
            "case {name}: {}",
            String::from_utf8_lossy(&result.stdout)
        );
        assert!(!output.exists());
    }
}

#[test]
fn rejects_input_larger_than_two_mibibytes_and_noncanonical_integer() {
    let temp = tempfile::tempdir().unwrap();
    let oversized = temp.path().join("oversized.json");
    fs::write(&oversized, vec![b' '; 2 * 1024 * 1024 + 1]).unwrap();
    let result = run(&oversized, &temp.path().join("oversized-output"));
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "INPUT_TOO_LARGE");

    let bytes = fs::read(fixture_path()).unwrap();
    let text = String::from_utf8(bytes).unwrap().replacen(
        "\"feeLamports\": \"5000\"",
        "\"feeLamports\": \"05000\"",
        1,
    );
    let input = temp.path().join("noncanonical.json");
    fs::write(&input, text).unwrap();
    let result = run(&input, &temp.path().join("noncanonical-output"));
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "NONCANONICAL_INTEGER");
}

#[cfg(target_os = "linux")]
#[test]
fn oversized_input_is_rejected_before_reading_past_the_bound() {
    let temp = tempfile::tempdir().unwrap();
    let preload = build_fault_preload(&temp);
    let oversized = temp.path().join("sparse-64m.json");
    let file = fs::File::create(&oversized).unwrap();
    file.set_len(64 * 1024 * 1024).unwrap();
    drop(file);
    let output = temp.path().join("output");
    let result = Command::new(env!("CARGO_BIN_EXE_phase8a-bronze-runner"))
        .args(["--input"])
        .arg(&oversized)
        .args(["--output"])
        .arg(&output)
        .env("LD_PRELOAD", &preload)
        .env("PHASE8A_FAIL_READ_PATH", &oversized)
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "INPUT_TOO_LARGE");
    assert!(!output.exists());
}

#[cfg(target_os = "linux")]
#[test]
fn bounded_append_after_initial_metadata_is_rejected_as_input_changed() {
    let temp = tempfile::tempdir().unwrap();
    let preload = build_fault_preload(&temp);
    let input = temp.path().join("input.json");
    fs::copy(fixture_path(), &input).unwrap();
    let initial_size = fs::metadata(&input).unwrap().len();
    let output = temp.path().join("output");
    let result = Command::new(env!("CARGO_BIN_EXE_phase8a-bronze-runner"))
        .args(["--input"])
        .arg(&input)
        .args(["--output"])
        .arg(&output)
        .env("LD_PRELOAD", &preload)
        .env("PHASE8A_APPEND_TARGET", &input)
        .output()
        .unwrap();
    assert_eq!(fs::metadata(&input).unwrap().len(), initial_size + 1);
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "INPUT_CHANGED");
    assert!(!output.exists());
}

#[cfg(target_os = "linux")]
#[test]
fn same_size_overwrite_after_initial_metadata_is_rejected_as_input_changed() {
    let temp = tempfile::tempdir().unwrap();
    let preload = build_fault_preload(&temp);
    let input = temp.path().join("input.json");
    fs::copy(fixture_path(), &input).unwrap();
    let initial_size = fs::metadata(&input).unwrap().len();
    let output = temp.path().join("output");
    let result = Command::new(env!("CARGO_BIN_EXE_phase8a-bronze-runner"))
        .args(["--input"])
        .arg(&input)
        .args(["--output"])
        .arg(&output)
        .env("LD_PRELOAD", &preload)
        .env("PHASE8A_OVERWRITE_TARGET", &input)
        .output()
        .unwrap();
    assert_eq!(fs::metadata(&input).unwrap().len(), initial_size);
    assert_eq!(result.status.code(), Some(2));
    assert_eq!(verdict(&result)["reasonCode"], "INPUT_CHANGED");
    assert!(!output.exists());
}

#[cfg(target_os = "linux")]
#[test]
fn parent_fsync_failure_rolls_back_the_final_output() {
    let temp = tempfile::tempdir().unwrap();
    let preload = build_fault_preload(&temp);
    let output = temp.path().join("output");
    let result = Command::new(env!("CARGO_BIN_EXE_phase8a-bronze-runner"))
        .args(["--input"])
        .arg(fixture_path())
        .args(["--output"])
        .arg(&output)
        .env("LD_PRELOAD", &preload)
        .env("PHASE8A_FAIL_FSYNC_PATH", temp.path())
        .output()
        .unwrap();
    let final_exists = output.exists();
    if final_exists {
        make_writable_tree(&output);
        fs::remove_dir_all(&output).unwrap();
    }
    assert_eq!(result.status.code(), Some(1));
    assert_eq!(verdict(&result)["reasonCode"], "OUTPUT_SYNC_FAILED");
    assert!(
        !final_exists,
        "failed publication left a visible final output"
    );
}

#[cfg(target_os = "linux")]
#[test]
fn ignored_chmod_is_detected_and_publication_is_rolled_back() {
    let temp = tempfile::tempdir().unwrap();
    let preload = build_fault_preload(&temp);
    let output = temp.path().join("output");
    let result = Command::new(env!("CARGO_BIN_EXE_phase8a-bronze-runner"))
        .args(["--input"])
        .arg(fixture_path())
        .args(["--output"])
        .arg(&output)
        .env("LD_PRELOAD", &preload)
        .env("PHASE8A_IGNORE_CHMOD_PREFIX", temp.path())
        .output()
        .unwrap();
    let final_exists = output.exists();
    if final_exists {
        make_writable_tree(&output);
        fs::remove_dir_all(&output).unwrap();
    }
    assert_eq!(result.status.code(), Some(1));
    assert_eq!(verdict(&result)["reasonCode"], "IMMUTABILITY_FAILED");
    assert!(
        !final_exists,
        "mutable publication was exposed as final output"
    );
}

#[test]
fn aggregate_hash_binds_sorted_relative_paths_and_sha256_values() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("run");
    assert!(run(&fixture_path(), &output).status.success());
    let mut bindings = regular_files(&output)
        .into_iter()
        .filter(|(path, _)| path != "aggregate-content-hash.txt" && path != "cockpit-snapshot.json")
        .map(|(path, bytes)| format!("{path}\0{:x}\n", Sha256::digest(bytes)))
        .collect::<Vec<_>>();
    bindings.sort();
    let expected = format!("{:x}\n", Sha256::digest(bindings.concat().as_bytes()));
    assert_eq!(
        fs::read_to_string(output.join("aggregate-content-hash.txt")).unwrap(),
        expected
    );
    let snapshot: Value =
        serde_json::from_slice(&fs::read(output.join("cockpit-snapshot.json")).unwrap()).unwrap();
    assert_eq!(
        snapshot["provenance"]["aggregateOutputSha256"],
        expected.trim()
    );
    assert_ne!(
        snapshot["provenance"]["aggregateOutputSha256"],
        snapshot["provenance"]["rerunSha256"]
    );
}

#[test]
#[allow(clippy::too_many_lines)]
fn cockpit_snapshot_matches_provider_contract_with_fixture_pinned_resources() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("run");
    let result = run(&fixture_path(), &output);
    assert!(result.status.success());
    let command_verdict = verdict(&result);
    let snapshot: Value =
        serde_json::from_slice(&fs::read(output.join("cockpit-snapshot.json")).unwrap()).unwrap();

    assert_eq!(snapshot["schemaVersion"], "PHASE8A_COCKPIT_SNAPSHOT_1");
    assert_eq!(snapshot["sourceClass"], "SYNTHETIC_FIXTURE_ONLY");
    assert_eq!(snapshot["runId"], command_verdict["runId"]);
    assert_eq!(snapshot["observedAt"], "2026-08-20T20:00:00.000Z");
    assert_eq!(snapshot["evidenceClass"], "SYNTHETIC");
    assert_eq!(snapshot["realData"], false);
    assert_eq!(snapshot["acceptedSilver"], false);
    assert_eq!(snapshot["researchReady"], false);
    assert_eq!(snapshot["activationVerdict"], "HOLD_UNPROVEN_ACTIVATION");
    assert_eq!(snapshot["eligibility"]["pilotEligible"], false);
    assert_eq!(snapshot["eligibility"]["transportPilot"]["eligible"], false);
    assert_eq!(snapshot["eligibility"]["acceptedSilver"]["eligible"], false);
    assert_eq!(snapshot["eligibility"]["research"]["researchReady"], false);
    assert_eq!(
        snapshot["progress"],
        json!({
            "requestedSlots": 3,
            "reconciledSlots": 3,
            "skippedSlots": 1,
            "provisionalSlots": 1,
            "resolvedSlots": 1,
            "currentSlot": 432_000_102,
            "lastCompletedSlot": 432_000_102,
            "coveragePercent": 100,
            "deterministicRerun": "MATCH"
        })
    );
    assert_eq!(
        snapshot["dataflow"],
        json!({
            "callbacks": 10,
            "blocks": 3,
            "transactions": 5,
            "topLevelInstructions": 5,
            "innerInstructions": 1,
            "pumpCandidates": 5,
            "failedPumpTransactions": 1,
            "unknownDiscriminators": 2,
            "quarantines": 3,
            "exactRetries": 1,
            "duplicateConflicts": 0
        })
    );
    let events = snapshot["events"].as_array().unwrap();
    assert_eq!(events.len(), 5);
    for row in events {
        assert_eq!(row["schemaVersion"], "PHASE8A_EVENT_ROW_1");
        assert_eq!(row["rawDetail"]["source"], "SYNTHETIC");
        assert_eq!(row["rawDetail"]["realData"], false);
        assert_eq!(row["rawDetail"]["acceptedSilver"], false);
        assert_eq!(row["rawDetail"]["researchReady"], false);
        assert_eq!(row["rawDetail"]["strategyStatus"], "NOT_STRATEGY_INPUT");
    }
    let quarantines = snapshot["quarantines"].as_array().unwrap();
    assert_eq!(quarantines.len(), 3);
    assert_eq!(
        fs::read_to_string(output.join("quarantines.ndjson"))
            .unwrap()
            .lines()
            .count(),
        quarantines.len()
    );
    assert!(
        quarantines
            .iter()
            .all(|row| row["schemaVersion"] == "PHASE8A_QUARANTINE_ROW_1")
    );
    assert_eq!(
        snapshot["provenance"]["schemaVersion"],
        "PHASE8A_PROVENANCE_1"
    );
    assert_eq!(
        snapshot["provenance"]["approvalStatus"],
        "CANDIDATE_UNAPPROVED"
    );
    assert_eq!(snapshot["provenance"]["completeness"], "FIXTURE_COMPLETE");
    assert_eq!(snapshot["provenance"]["uncertainty"], "UNPROVEN_ACTIVATION");
    assert_eq!(
        snapshot["provenance"]["aggregateOutputSha256"],
        fs::read_to_string(output.join("aggregate-content-hash.txt"))
            .unwrap()
            .trim()
    );
    assert_eq!(
        snapshot["resources"],
        json!({
            "bytesRead": 18000,
            "bytesWritten": 42000,
            "queueDepth": 2,
            "peakRssBytes": 24_000_000,
            "outputBytes": 42000,
            "stageDurationsMs": {"validate": 2, "reduce": 5, "publish": 3},
            "walStatus": "CLEAN",
            "checkpointStatus": "PUBLISHED",
            "quarantineByReason": {"UNKNOWN_DISCRIMINATOR": 2, "FAILED_TRANSACTION": 1},
            "evidenceBasis": "FIXTURE_PINNED_NOT_MEASURED"
        })
    );
}
