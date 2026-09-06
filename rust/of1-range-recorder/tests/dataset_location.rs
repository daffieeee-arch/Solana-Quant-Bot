use of1_range_recorder::dataset_location::validate_dataset_location;
use std::{fs, io::ErrorKind, os::unix::fs::symlink};
use tempfile::tempdir;

#[test]
fn empty_ordinary_marker_is_allowed_and_left_untouched() {
    let temp = tempdir().unwrap();
    let marker = temp.path().join(".git");
    fs::create_dir(&marker).unwrap();
    let root = temp.path().join("run");
    assert_eq!(validate_dataset_location(&root).unwrap(), root);
    assert!(!root.exists());
    assert!(fs::symlink_metadata(&marker).unwrap().file_type().is_dir());
    assert_eq!(fs::read_dir(&marker).unwrap().count(), 0);
}

#[test]
fn nonempty_marker_is_rejected_including_at_the_root_itself() {
    let temp = tempdir().unwrap();
    fs::create_dir(temp.path().join(".git")).unwrap();
    fs::write(temp.path().join(".git/HEAD"), b"ref: refs/heads/main\n").unwrap();
    for root in [temp.path().to_path_buf(), temp.path().join("run")] {
        assert_eq!(
            validate_dataset_location(&root).unwrap_err().kind(),
            ErrorKind::PermissionDenied
        );
    }
    assert!(!temp.path().join("run").exists());
}

#[test]
fn gitfiles_are_never_treated_as_empty_markers() {
    let temp = tempdir().unwrap();
    let marker = temp.path().join(".git");
    for content in [b"gitdir: /missing/worktrees/linked\n".as_slice(), b""] {
        fs::write(&marker, content).unwrap();
        assert!(validate_dataset_location(&temp.path().join("run")).is_err());
        assert_eq!(fs::read(&marker).unwrap(), content);
    }
}

#[test]
fn symlinked_markers_are_rejected_even_with_empty_or_missing_targets() {
    let temp = tempdir().unwrap();
    fs::create_dir(temp.path().join("empty")).unwrap();
    for (case, target) in [("empty-target", "empty"), ("dangling", "missing")] {
        let parent = temp.path().join(case);
        fs::create_dir(&parent).unwrap();
        symlink(temp.path().join(target), parent.join(".git")).unwrap();
        assert!(validate_dataset_location(&parent.join("run")).is_err());
        assert!(
            fs::symlink_metadata(parent.join(".git"))
                .unwrap()
                .is_symlink()
        );
    }
}

#[test]
fn symlinked_parent_and_parent_components_follow_filesystem_canonicalization() {
    let temp = tempdir().unwrap();
    let repository = temp.path().join("repository");
    fs::create_dir_all(repository.join("nested")).unwrap();
    fs::write(repository.join(".git"), b"gitdir: elsewhere\n").unwrap();
    let alias = temp.path().join("alias");
    symlink(repository.join("nested"), &alias).unwrap();
    assert!(validate_dataset_location(&alias.join("run")).is_err());
    // Lexically reducing alias/.. would miss the repository marker.
    assert!(validate_dataset_location(&alias.join("../run")).is_err());
    symlink(&repository, temp.path().join("root-alias")).unwrap();
    assert!(validate_dataset_location(&temp.path().join("root-alias")).is_err());
}

#[test]
fn canonical_outside_paths_agree_without_creating_a_run() {
    let temp = tempdir().unwrap();
    let parent = temp.path().join("data");
    fs::create_dir_all(parent.join("nested")).unwrap();
    symlink(&parent, temp.path().join("alias")).unwrap();
    let expected = parent.canonicalize().unwrap().join("run");
    for root in [
        parent.join("run"),
        parent.join("nested/../run"),
        temp.path().join("alias/run"),
    ] {
        assert_eq!(validate_dataset_location(&root).unwrap(), expected);
    }
    assert!(!expected.exists());
}

#[test]
fn empty_inner_marker_does_not_hide_repository_ancestor() {
    let temp = tempdir().unwrap();
    fs::write(temp.path().join(".git"), b"gitdir: actual-repository\n").unwrap();
    fs::create_dir_all(temp.path().join("child/.git")).unwrap();
    assert!(validate_dataset_location(&temp.path().join("child/run")).is_err());
}

#[test]
fn later_nonempty_marker_is_rejected_on_revalidation() {
    let temp = tempdir().unwrap();
    fs::create_dir(temp.path().join(".git")).unwrap();
    let root = temp.path().join("run");
    assert!(validate_dataset_location(&root).is_ok());
    fs::write(temp.path().join(".git/HEAD"), b"new repository\n").unwrap();
    assert!(validate_dataset_location(&root).is_err());
    assert!(!root.exists());
}

#[test]
fn invalid_paths_fail_without_creating_intermediate_directories() {
    let temp = tempdir().unwrap();
    fs::write(temp.path().join("file"), b"not a directory").unwrap();
    symlink(temp.path().join("missing"), temp.path().join("dangling")).unwrap();
    for root in [
        temp.path().join("missing/run"),
        temp.path().join("file"),
        temp.path().join("file/run"),
        temp.path().join("dangling"),
        "relative/run".into(),
    ] {
        assert!(validate_dataset_location(&root).is_err());
    }
    assert!(!temp.path().join("missing").exists());
}
