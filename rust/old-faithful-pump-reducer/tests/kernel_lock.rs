#![allow(clippy::default_trait_access, clippy::too_many_lines)]

use std::{
    collections::HashMap,
    os::unix::{ffi::OsStrExt as _, fs::MetadataExt as _},
    path::{Path, PathBuf},
    process::{Child, Command},
    thread,
    time::Duration,
};

use old_faithful_pump_reducer::{
    AdapterProvenance, OldFaithfulSourceManifest, Phase5Reducer, ReducerConfig, ReducerLimits,
    ReducerProvenance, SlotRange,
};
use sha2::{Digest as _, Sha256};

const START: u64 = 432_000_000;

fn config(output_dir: PathBuf) -> ReducerConfig {
    ReducerConfig {
        output_dir,
        source_manifest: OldFaithfulSourceManifest {
            schema_version: "OLD_FAITHFUL_EPOCH_SOURCE_1".into(),
            epoch: 1_000,
            epoch_cid: "bafyreihvgnloaiilehijbe42cloousmwvl2z666zr7wvqprs6wnqfeqo4q".into(),
            car_sha256: "3c9727378e617f5cba8b5e206bce8fc6ae5df34d3a4408eeb69aea4d62ac7218".into(),
            car_file_size_bytes: "767389334224".into(),
            slots_file_sha256: "b04cec20c168d256fadcebc8626b711ac12558b207142dd6872d0deb382e8930"
                .into(),
            slots_file_size_bytes: 4_317_820,
            slots_file_entry_count: 431_782,
            slots_first: 431_999_999,
            slots_last: 432_431_999,
            slot_range: SlotRange {
                start_inclusive: START,
                end_exclusive: START + 432_000,
            },
        },
        adapter_provenance: AdapterProvenance {
            schema_version: "JETSTREAMER_ADAPTER_PROVENANCE_1".into(),
            jetstreamer_git_sha: old_faithful_pump_reducer::JETSTREAMER_V0_7_0_GIT_SHA.into(),
            plugin_git_sha: "1".repeat(40),
            plugin_source_sha256: "2".repeat(64),
        },
        reducer_provenance: ReducerProvenance {
            schema_version: "OLD_FAITHFUL_RUST_REDUCER_PROVENANCE_1".into(),
            reducer_git_sha: "3".repeat(40),
            reducer_source_sha256: "4".repeat(64),
            jetstreamer_git_sha: old_faithful_pump_reducer::JETSTREAMER_V0_7_0_GIT_SHA.into(),
        },
        limits: ReducerLimits::test_defaults(),
    }
}

fn wait_for(path: &Path) {
    for _ in 0..1_000 {
        if path.exists() {
            return;
        }
        thread::sleep(Duration::from_millis(5));
    }
    panic!("timed out waiting for {}", path.display());
}

fn set_nondumpable() {
    linux_kernel_namespace_lock::make_current_process_nondumpable().unwrap();
}

fn child_process(mode: &str, output: &Path, ready: &Path, release: &Path) {
    match mode {
        "unrelated" => {
            set_nondumpable();
            std::fs::write(ready, b"ready").unwrap();
            wait_for(release);
        }
        "writer" => {
            let writer = Phase5Reducer::open(config(output.to_path_buf()));
            std::fs::write(
                ready,
                if writer.is_ok() {
                    b"ok".as_slice()
                } else {
                    b"err".as_slice()
                },
            )
            .unwrap();
            if let Ok(writer) = writer {
                wait_for(release);
                drop(writer);
            }
        }
        "nondumpable-writer" => {
            let writer = Phase5Reducer::open(config(output.to_path_buf())).unwrap();
            set_nondumpable();
            let parent = output.parent().unwrap();
            let external = std::fs::read_dir(parent)
                .unwrap()
                .map(|entry| entry.unwrap().path())
                .find(|path| {
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .is_some_and(|name| {
                            name.starts_with(".old-faithful-pump-reducer-")
                                && Path::new(name)
                                    .extension()
                                    .is_some_and(|extension| extension.eq_ignore_ascii_case("lock"))
                        })
                })
                .unwrap();
            std::fs::rename(output, parent.join("moved-output")).unwrap();
            std::fs::rename(external, parent.join("moved-external.lock")).unwrap();
            std::fs::write(ready, b"ready").unwrap();
            wait_for(release);
            drop(writer);
        }
        _ => panic!("unknown child mode"),
    }
}

fn spawn_child(mode: &str, output: &Path, ready: &Path, release: &Path, test_name: &str) -> Child {
    Command::new(std::env::current_exe().unwrap())
        .arg("--exact")
        .arg(test_name)
        .arg("--nocapture")
        .arg("--test-threads=1")
        .env("PHASE5_LOCK_CHILD_MODE", mode)
        .env("PHASE5_LOCK_CHILD_OUTPUT", output)
        .env("PHASE5_LOCK_CHILD_READY", ready)
        .env("PHASE5_LOCK_CHILD_RELEASE", release)
        .spawn()
        .unwrap()
}

fn maybe_run_child() -> bool {
    let Ok(mode) = std::env::var("PHASE5_LOCK_CHILD_MODE") else {
        return false;
    };
    child_process(
        &mode,
        Path::new(&std::env::var("PHASE5_LOCK_CHILD_OUTPUT").unwrap()),
        Path::new(&std::env::var("PHASE5_LOCK_CHILD_READY").unwrap()),
        Path::new(&std::env::var("PHASE5_LOCK_CHILD_RELEASE").unwrap()),
    );
    true
}

#[test]
fn unrelated_nondumpable_same_uid_process_does_not_block_writer() {
    if maybe_run_child() {
        return;
    }
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let ready = temp.path().join("ready");
    let release = temp.path().join("release");
    let mut child = spawn_child(
        "unrelated",
        &output,
        &ready,
        &release,
        "unrelated_nondumpable_same_uid_process_does_not_block_writer",
    );
    wait_for(&ready);
    let writer = Phase5Reducer::open(config(output)).unwrap();
    std::fs::write(release, b"release").unwrap();
    assert!(child.wait().unwrap().success());
    drop(writer);
}

#[test]
fn nondumpable_writer_remains_exclusive_after_all_pathnames_move() {
    if maybe_run_child() {
        return;
    }
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let ready = temp.path().join("ready");
    let release = temp.path().join("release");
    let mut child = spawn_child(
        "nondumpable-writer",
        &output,
        &ready,
        &release,
        "nondumpable_writer_remains_exclusive_after_all_pathnames_move",
    );
    wait_for(&ready);
    let error = Phase5Reducer::open(config(output)).unwrap_err().to_string();
    assert!(error.contains("active writer"), "unexpected error: {error}");
    std::fs::write(release, b"release").unwrap();
    assert!(child.wait().unwrap().success());
}

#[test]
fn killed_writer_releases_kernel_namespace_immediately() {
    if maybe_run_child() {
        return;
    }
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let ready = temp.path().join("ready");
    let release = temp.path().join("release");
    let mut child = spawn_child(
        "writer",
        &output,
        &ready,
        &release,
        "killed_writer_releases_kernel_namespace_immediately",
    );
    wait_for(&ready);
    assert_eq!(std::fs::read(&ready).unwrap(), b"ok");
    child.kill().unwrap();
    let _ = child.wait().unwrap();
    let replacement = Phase5Reducer::open(config(output)).unwrap();
    drop(replacement);
}

#[test]
fn distinct_namespaces_with_the_same_primary_key_do_not_alias() {
    let temp = tempfile::tempdir().unwrap();
    let effective_uid = std::fs::metadata(temp.path()).unwrap().uid();
    let mut seen = HashMap::<i32, PathBuf>::new();
    let mut collision = None;
    for index in 0..200_000_u32 {
        let namespace = temp.path().join(format!("namespace-{index}"));
        let mut hasher = Sha256::new();
        hasher.update(effective_uid.to_be_bytes());
        hasher.update([0]);
        hasher.update(namespace.as_os_str().as_bytes());
        let digest = hasher.finalize();
        let key = i32::from_be_bytes(digest[..4].try_into().unwrap());
        if key == 0 {
            continue;
        }
        if let Some(other) = seen.insert(key, namespace.clone()) {
            collision = Some((other, namespace));
            break;
        }
    }
    let (first_namespace, second_namespace) = collision.expect("expected birthday collision");
    let first =
        linux_kernel_namespace_lock::NamespaceLock::acquire(first_namespace.as_os_str().as_bytes())
            .unwrap();
    let second = linux_kernel_namespace_lock::NamespaceLock::acquire(
        second_namespace.as_os_str().as_bytes(),
    )
    .expect("distinct full namespace digests must not alias");
    drop(first);
    let duplicate_second = linux_kernel_namespace_lock::NamespaceLock::acquire(
        second_namespace.as_os_str().as_bytes(),
    )
    .unwrap_err();
    assert_eq!(duplicate_second.kind(), std::io::ErrorKind::WouldBlock);
    let replacement_first =
        linux_kernel_namespace_lock::NamespaceLock::acquire(first_namespace.as_os_str().as_bytes())
            .expect("collision mapping must remain stable while another slot is active");
    drop(replacement_first);
    drop(second);
}

#[test]
fn uninitialized_incompatible_registry_is_not_removed_or_bypassed() {
    let temp = tempfile::tempdir().unwrap();
    let namespace = temp.path().join("reserved-namespace");
    let reservation = linux_kernel_namespace_lock::reserve_uninitialized_registry_for_test(
        namespace.as_os_str().as_bytes(),
    )
    .unwrap();
    let acquisition =
        linux_kernel_namespace_lock::NamespaceLock::acquire(namespace.as_os_str().as_bytes());
    assert!(acquisition.is_err(), "uninitialized registry was bypassed");
    assert!(
        reservation.is_present(),
        "acquisition removed a foreign uninitialized registry"
    );
}

#[test]
fn simultaneous_writers_elect_exactly_one_owner() {
    if maybe_run_child() {
        return;
    }
    for trial in 0..20 {
        let temp = tempfile::tempdir().unwrap();
        let output = temp.path().join(format!("output-{trial}"));
        let release = temp.path().join("release");
        let ready_a = temp.path().join("a.ready");
        let ready_b = temp.path().join("b.ready");
        let mut child_a = spawn_child(
            "writer",
            &output,
            &ready_a,
            &release,
            "simultaneous_writers_elect_exactly_one_owner",
        );
        let mut child_b = spawn_child(
            "writer",
            &output,
            &ready_b,
            &release,
            "simultaneous_writers_elect_exactly_one_owner",
        );
        wait_for(&ready_a);
        wait_for(&ready_b);
        let owners = [ready_a, ready_b]
            .iter()
            .filter(|path| std::fs::read(path).unwrap() == b"ok")
            .count();
        assert_eq!(owners, 1, "trial {trial} did not elect exactly one owner");
        std::fs::write(&release, b"release").unwrap();
        assert!(child_a.wait().unwrap().success());
        assert!(child_b.wait().unwrap().success());
    }
}
