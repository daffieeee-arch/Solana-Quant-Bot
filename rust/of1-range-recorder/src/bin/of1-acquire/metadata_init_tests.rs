// Source-pinned, test-only subprocess harness for the actual CLI dispatcher.
// Fixture authority cannot initialize/open the fixed production campaign.
use super::*;
use of1_range_recorder::{b7, clock_contract::ApprovalAnchor};
use std::{
    fs,
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

#[test]
fn cli_child() {
    let Ok(file) = std::env::var("OF1_METADATA_INIT_TEST_ARGS") else {
        return;
    };
    let args: Vec<String> = read(&file).unwrap();
    if let Err(error) = run(&args) {
        eprintln!("OF1_STOP: {error}");
        std::process::exit(1);
    }
}

fn invoke(dir: &Path, args: &[String]) -> std::process::Output {
    let input = dir.join("cli-args.json");
    fs::write(&input, serde_json::to_vec(args).unwrap()).unwrap();
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "metadata_init_tests::cli_child", "--nocapture"])
        .env("OF1_METADATA_INIT_TEST_ARGS", &input)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(15);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("metadata-init child exceeded 15 seconds");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    child.wait_with_output().unwrap()
}

struct Case {
    dir: tempfile::TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    lease: MetadataLease,
}
impl Case {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap().join("campaign");
        let plan = AggregatePlan {
            schema: AGGREGATE_SCHEMA.into(),
            epoch: 978,
            format_source: FormatSource::pinned(),
            code_sha: "a".repeat(40),
            toolchain_fingerprint: "b".repeat(64),
            executable_sha256: current_executable_sha256().unwrap(),
            sample_identity: Some(b7::sample(0, &root).unwrap()),
            clock_policy: Some(ClockPolicy::standard()),
            download_rate: Some(of1_range_recorder::rate::DownloadRate::standard()),
            budget: AggregateBudget {
                max_slots: 16,
                max_plan_bytes: 1_048_576,
                max_requests: 60,
                max_response_entity_bytes: 16_777_216,
                max_total_response_entity_bytes: 134_217_728,
                max_disk_bytes: 268_435_456,
                required_free_disk_bytes: 1,
                max_memory_bytes: 536_870_912,
                max_runtime_ms: 1_800_000,
                response_timeout_ms: 30_000,
                request_retries: 2,
            },
        };
        let lease = MetadataLease {
            schema: "OF1_METADATA_LEASE_1".into(),
            authority: Authority::Fixture,
            budget: metadata_budget(false),
        };
        Self {
            dir,
            root,
            plan,
            lease,
        }
    }
    fn run(&self, root: &Path) -> std::process::Output {
        let plan = self.dir.path().join("plan.json");
        let lease = self.dir.path().join("lease.json");
        fs::write(&plan, serde_json::to_vec(&self.plan).unwrap()).unwrap();
        fs::write(&lease, serde_json::to_vec(&self.lease).unwrap()).unwrap();
        invoke(
            self.dir.path(),
            &[
                "metadata-init".into(),
                root.display().to_string(),
                plan.display().to_string(),
                lease.display().to_string(),
            ],
        )
    }
    fn anchor(&self) -> PathBuf {
        self.root.with_file_name("campaign.anchor.json")
    }
    fn rejected_without_state(&self, root: &Path) {
        let result = self.run(root);
        assert_eq!(result.status.code(), Some(1), "{result:?}");
        assert!(String::from_utf8_lossy(&result.stderr).contains("OF1_STOP:"));
        assert!(!self.root.exists());
        assert!(!self.anchor().exists());
    }
}

#[test]
fn metadata_init_cli_creates_missing_campaign_once_without_requests() {
    let case = Case::new();
    let run = case.root.join("runs/w00");
    assert!(!case.root.exists());
    assert!(!case.anchor().exists());
    let result = case.run(&run);
    assert!(result.status.success(), "{result:?}");
    let manifest: serde_json::Value = read(run.join("run.json").to_str().unwrap()).unwrap();
    assert_eq!(
        manifest["plan"]["sample_identity"],
        serde_json::to_value(&case.plan.sample_identity).unwrap()
    );
    let header: serde_json::Value =
        read(case.root.join("campaign.json").to_str().unwrap()).unwrap();
    assert_eq!(header["fixture"], true);
    assert_eq!(header["campaign"], b7::CAMPAIGN_ID);
    assert_eq!(
        header,
        read::<serde_json::Value>(case.anchor().to_str().unwrap()).unwrap()
    );
    for name in ["journal", "runs", "work"] {
        assert!(case.root.join(name).is_dir());
    }
    for name in ["attempts", "published", "pending"] {
        assert_eq!(fs::read_dir(run.join(name)).unwrap().count(), 0);
    }
    let status = of1_range_recorder::campaign::Guard::status(&case.root).unwrap();
    assert_eq!(status["attempts_reserved"], 0);
    assert_eq!(status["entity_bytes_reserved"], 0);
    assert_eq!(status["selected_slots_registered"], 16);
    let before = fs::read(case.root.join("head.json")).unwrap();
    assert_eq!(case.run(&run).status.code(), Some(1));
    assert_eq!(fs::read(case.root.join("head.json")).unwrap(), before);
    // Missing campaign state cannot erase its create-once external anchor.
    fs::remove_dir_all(&case.root).unwrap();
    let anchor = fs::read(case.anchor()).unwrap();
    assert_eq!(case.run(&run).status.code(), Some(1));
    assert!(!case.root.exists());
    assert_eq!(fs::read(case.anchor()).unwrap(), anchor);
}

#[test]
fn metadata_init_cli_rejects_wrong_location_identity_and_authority() {
    let case = Case::new();
    for root in [
        case.root.join("runs/w01"),
        case.root.join("elsewhere/w00"),
        PathBuf::from("relative/runs/w00"),
    ] {
        case.rejected_without_state(&root);
    }
    fs::write(case.dir.path().join(".git"), b"gitdir: fixture\n").unwrap();
    case.rejected_without_state(&case.root.join("runs/w00"));
    fs::remove_file(case.dir.path().join(".git")).unwrap();
    let alias = case.dir.path().join("alias");
    std::os::unix::fs::symlink(case.dir.path(), &alias).unwrap();
    let mut changed = Case::new();
    changed
        .plan
        .sample_identity
        .as_mut()
        .unwrap()
        .b7
        .as_mut()
        .unwrap()
        .selection_sha256 = "0".repeat(64);
    changed.rejected_without_state(&changed.root.join("runs/w00"));
    let mut changed = Case::new();
    changed.plan.sample_identity = Some(b7::sample(0, &alias.join("campaign")).unwrap());
    changed.rejected_without_state(&alias.join("campaign/runs/w00"));
    assert!(!case.root.exists());
    assert!(!case.anchor().exists());
    let mut changed = Case::new();
    changed.plan.budget.required_free_disk_bytes = of1_range_recorder::campaign::FREE_BYTES;
    let t0 = SystemClock.sample().unwrap();
    changed.lease.authority = Authority::Approved {
        approval_id: "offline-invalid-authority".into(),
        operator: "fixture".into(),
        approved_at_ms: t0.wall_ms,
        not_after_ms: t0.wall_ms + 60_000,
        approved_plan_sha256: "0".repeat(64),
        cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
        clock_anchor: Some(ApprovalAnchor {
            initialize_by_boot_ms: t0.boot_ms + 30_000,
            expires_at_boot_ms: t0.boot_ms + 60_000,
            t0,
        }),
    };
    changed.rejected_without_state(&changed.root.join("runs/w00"));
    // Even well-formed Approved authority cannot use a fixture campaign root.
    let target = metadata_proposal_sha256(&changed.plan, &changed.lease.budget).unwrap();
    if let Authority::Approved {
        approved_plan_sha256,
        ..
    } = &mut changed.lease.authority
    {
        *approved_plan_sha256 = target;
    }
    changed.rejected_without_state(&changed.root.join("runs/w00"));
}

#[test]
fn metadata_init_cli_does_not_repair_incomplete_campaign() {
    for runs in [false, true] {
        let case = Case::new();
        fs::create_dir(&case.root).unwrap();
        if runs {
            fs::create_dir(case.root.join("runs")).unwrap();
        }
        let before = fs::read_dir(&case.root).unwrap().count();
        assert_eq!(case.run(&case.root.join("runs/w00")).status.code(), Some(1));
        assert_eq!(fs::read_dir(&case.root).unwrap().count(), before);
        assert!(!case.anchor().exists());
    }
}

#[test]
fn metadata_init_cli_validates_lease_and_binary_before_initialization() {
    let mut case = Case::new();
    case.lease.schema = "WRONG".into();
    case.rejected_without_state(&case.root.join("runs/w00"));
    case.lease.schema = "OF1_METADATA_LEASE_1".into();
    case.lease.budget.max_requests = 13;
    case.rejected_without_state(&case.root.join("runs/w00"));
    case.lease.budget.max_requests = 12;
    case.plan.executable_sha256 = "0".repeat(64);
    case.rejected_without_state(&case.root.join("runs/w00"));
    // Exercise Approved lease validation independently of the B7 fixed-root
    // refusal. No authentic root or initialized campaign is needed for denial.
    case.plan.executable_sha256 = current_executable_sha256().unwrap();
    case.plan.sample_identity = None;
    let t0 = SystemClock.sample().unwrap();
    case.lease.authority = Authority::Approved {
        approval_id: "invalid-offline-lease".into(),
        operator: "fixture".into(),
        approved_at_ms: t0.wall_ms,
        not_after_ms: t0.wall_ms + 60_000,
        approved_plan_sha256: "0".repeat(64),
        cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
        clock_anchor: Some(ApprovalAnchor {
            initialize_by_boot_ms: t0.boot_ms + 30_000,
            expires_at_boot_ms: t0.boot_ms + 60_000,
            t0,
        }),
    };
    case.rejected_without_state(&case.root);
}
