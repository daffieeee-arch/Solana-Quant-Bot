use std::path::{Path, PathBuf};

use pump_protocol_v2::{
    EVIDENCE_JSON_PATH, EVIDENCE_MARKDOWN_PATH, VECTOR_MANIFEST_PATH, generated_evidence_json,
    generated_evidence_markdown, generated_vector_manifest_json,
};

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("crate must remain under rust/pump-protocol-v2")
        .to_path_buf()
}

fn check(path: &Path, expected: &str) -> Result<(), String> {
    let observed = std::fs::read_to_string(path)
        .map_err(|error| format!("read {}: {error}", path.display()))?;
    if observed == expected {
        Ok(())
    } else {
        Err(format!(
            "generated evidence drift at {}; rerun with --write and review the diff",
            path.display()
        ))
    }
}

fn write(path: &Path, value: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("missing parent for {}", path.display()))?;
    std::fs::create_dir_all(parent)
        .map_err(|error| format!("create {}: {error}", parent.display()))?;
    std::fs::write(path, value).map_err(|error| format!("write {}: {error}", path.display()))
}

fn run() -> Result<(), String> {
    let mode = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "--check".to_owned());
    if std::env::args().nth(2).is_some() {
        return Err("usage: pump-protocol-evidence [--check|--write]".to_owned());
    }
    let root = repository_root();
    let json_path = root.join(EVIDENCE_JSON_PATH);
    let markdown_path = root.join(EVIDENCE_MARKDOWN_PATH);
    let vector_path = root.join(VECTOR_MANIFEST_PATH);
    let json = generated_evidence_json();
    let markdown = generated_evidence_markdown();
    let vectors = generated_vector_manifest_json();
    match mode.as_str() {
        "--check" => {
            check(&json_path, &json)?;
            check(&markdown_path, &markdown)?;
            check(&vector_path, &vectors)?;
        }
        "--write" => {
            write(&json_path, &json)?;
            write(&markdown_path, &markdown)?;
            write(&vector_path, &vectors)?;
        }
        _ => return Err("usage: pump-protocol-evidence [--check|--write]".to_owned()),
    }
    println!("Pump protocol evidence matrix {mode} PASS");
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
