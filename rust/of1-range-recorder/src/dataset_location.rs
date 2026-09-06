//! Read-only location admission shared by proposal preflight and initialization.
use std::{
    fs, io,
    path::{Path, PathBuf},
};

/// Resolve the dataset location and reject repository/worktree markers.
///
/// Only a readable, ordinary (not symlinked), empty `.git` directory is ignored.
/// A new root needs an existing parent; this function never creates directories.
/// Passing this check grants no acquisition authority or store/resume readiness.
///
/// # Errors
/// Returns an error for non-absolute/non-directory paths, inaccessible markers,
/// or any `.git` entry that is not demonstrably an empty ordinary directory.
pub fn validate_dataset_location(root: &Path) -> io::Result<PathBuf> {
    if !root.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "dataset root must be absolute",
        ));
    }
    let canonical = match fs::symlink_metadata(root) {
        Ok(_) => root.canonicalize()?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let leaf = root.file_name().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "dataset root needs a final name",
                )
            })?;
            let parent = root
                .parent()
                .ok_or_else(|| {
                    io::Error::new(io::ErrorKind::InvalidInput, "dataset root needs a parent")
                })?
                .canonicalize()?;
            if !parent.is_dir() {
                return Err(io::Error::new(
                    io::ErrorKind::NotADirectory,
                    "dataset parent must be a directory",
                ));
            }
            parent.join(leaf)
        }
        Err(error) => return Err(error),
    };
    // Include an existing root itself, not only its parent. Canonicalization
    // must precede ancestor inspection (not lexical removal of symlink/..).
    if canonical.try_exists()? && !canonical.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotADirectory,
            "dataset root must be a directory",
        ));
    }
    for ancestor in canonical.ancestors() {
        let marker = ancestor.join(".git");
        match fs::symlink_metadata(&marker) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
            Ok(metadata) if metadata.file_type().is_dir() => {
                if fs::read_dir(&marker)?.next().transpose()?.is_none() {
                    continue;
                }
            }
            Ok(_) => {}
        }
        // Includes ordinary checkout directories, linked-worktree gitfiles,
        // symlinks (even dangling/empty targets) and unknown marker types.
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "dataset root must be outside Git",
        ));
    }
    Ok(canonical)
}
