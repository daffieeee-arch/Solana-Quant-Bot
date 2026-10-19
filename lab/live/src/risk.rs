//! The owner's risk files (paper/risk.yaml, risk-h.yaml) read as paper/risk.py `load` reads them, for M1 C3
//! (research/m1-c3-risk-spec-2026-10-19.md §2.1, D1, D2). `load` gives the same `Risk`, or the same reason text, as
//! `risk.load` for every file of a strict YAML subset; a file outside the subset is refused with this reader's own
//! reason ([`OWN`]): fail closed, never interpreted.
//!
//! - **The subset.** `key: value` lines at column 0, blank lines and `#` comments (at a line's start, or after a space),
//!   LF or CRLF line ends and a leading BOM. A value is one of what PyYAML 1.1 resolves it to:
//!   - an int `[-+]?(0|[1-9][0-9]*)` (no octal-looking `010`, no `_`, no `0x`);
//!   - a decimal `[-+]?[0-9]+\.[0-9]*` or `\.[0-9]+` (no exponent: PyYAML resolves `1e9` to a string, `1e+9` to a
//!     float, and Python's int() treats them differently; no signed `-.5`, a string to PyYAML);
//!   - a YAML 1.1 bool word (`yes`, `No`, `ON`, `off`, `true`, …);
//!   - a plain word `[A-Za-z][A-Za-z0-9_]*` that is not a bool or null word (a string);
//!   - a flow list `[a, b]` of plain words and quoted words (`'…'`, `"…"` over `[A-Za-z0-9_]*`).
//!
//!   Refused: anything else (an empty or null value, a quoted scalar, an exponent, a block list, a flow mapping, a
//!   trailing comma, a list item that is not a string, an indented or duplicate key, a key over 64 characters (PyYAML
//!   refuses one over 1024), a document marker, a tab, a control character, a lone CR, NEL, LS or PS, a file that is
//!   not UTF-8). Also refused, though written in the subset: a list for a number, an int beyond 128 bits, a decimal
//!   that is infinite as a float, a decimal for max_open_positions at or beyond ±9.2e18, and a size_sol whose
//!   lamports reach 9.2e18 (Python carries these as big ints or infinities, which its engine cannot use). Python accepts some of these (`1e9`, a duplicate key:
//!   the last wins, a block list), refuses others with a YAML error, and raises on two (a list mixing a number and a word;
//!   a file that is not UTF-8). The golden table `paper/tests/data/c3_risk_load_cases.json` records each.
//! - **Inside the subset, Python's steps.** Keys are looked up in risk.py's argument order (a missing one is
//!   `KeyError '<key>'`), each value cast as `float()`, `int()`, `bool()` or `tuple()` casts it (an int is a float,
//!   a bool is 1 or 0, a decimal for an int truncates, a word for a number is the ValueError Python writes, a word for
//!   the families is its characters, a number for them is the TypeError); unknown keys are ignored; then the limits,
//!   the families and the kill switch are checked in that order, with risk.py's texts. A word Python's float() reads as
//!   infinity or NaN (`inf`, `NaN`) is refused.
//! - **Unreadable files** name Python's exception: FileNotFoundError, PermissionError, IsADirectoryError,
//!   NotADirectoryError; any other I/O error is OSError (Python may name a subclass there).
#![allow(dead_code)] // C3-4 calls `load` from `lab-live paper`; until then its tests do.

use lab_core::risk::{Risk, FAMILIES};
use std::collections::BTreeSet;
use std::path::Path;

/// The start of this reader's own reason for a file outside its subset. An unreadable file, as Python's YAML errors are.
pub const OWN: &str = "risk config unreadable: outside lab-live's YAML subset";
const INVALID: &str = "risk config invalid:";
const LIMITS: &str = "risk config invalid: sizes and limits must be positive and size <= max exposure";
const BOOLS: [&str; 18] = ["yes", "Yes", "YES", "no", "No", "NO", "true", "True", "TRUE", "false", "False", "FALSE", "on", "On",
                           "ON", "off", "Off", "OFF"];
const NULLS: [&str; 3] = ["null", "Null", "NULL"];

/// A value of the subset, typed as PyYAML 1.1 resolves it.
#[derive(Clone, Debug, PartialEq)]
enum Val {
    Int(i128),
    Float(f64),
    Bool(bool),
    Word(String),
    List(Vec<String>),
}

/// risk.py `load(path)`: the Risk, or the reason the engine journals.
pub fn load(path: &Path) -> Result<Risk, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("risk config unreadable: {}", os_error(&e)))?;
    let text = std::str::from_utf8(&bytes).map_err(|_| format!("{OWN} (not UTF-8)"))?;
    parse(text)
}

/// The Python exception `open()` and `read()` raise for this error.
fn os_error(e: &std::io::Error) -> &'static str {
    use std::io::ErrorKind::*;
    match e.kind() {
        NotFound => "FileNotFoundError",
        PermissionDenied => "PermissionError",
        IsADirectory => "IsADirectoryError",
        NotADirectory => "NotADirectoryError",
        _ => "OSError",
    }
}

/// PyYAML's printable characters, less the line breaks it knows besides LF and CR-LF and the tab (refused here).
fn allowed(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\u{20}'..='\u{7E}' | '\u{A0}'..='\u{D7FF}' | '\u{E000}'..='\u{FFFD}' | '\u{10000}'..)
        && !matches!(c, '\u{2028}' | '\u{2029}')
}

/// risk.py `load` on a file's text (after its UTF-8 decoding).
pub fn parse(text: &str) -> Result<Risk, String> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut map: Vec<(&str, Val)> = Vec::new();
    for (n, raw) in text.split('\n').enumerate() {
        let refuse = |why: &str| Err(format!("{OWN} (line {}: {why})", n + 1));
        let line = raw.strip_suffix('\r').unwrap_or(raw);
        if let Some(c) = line.chars().find(|&c| !allowed(c) || c == '\r') {
            return refuse(&format!("the character U+{:04X}", c as u32));
        }
        let body = line.trim_start_matches(' ');
        if body.is_empty() || body.starts_with('#') {
            continue;
        }
        if body.len() != line.len() {
            return refuse("an indented line");
        }
        let Some((key, rest)) = line.split_once(':') else { return refuse("not `key: value`") };
        if !is_key(key) {
            return refuse("a key that is not a plain name");
        }
        if !(rest.is_empty() || rest.starts_with(' ')) {
            return refuse("no space after the colon");
        }
        if map.iter().any(|(k, _)| *k == key) {
            return refuse(&format!("{key} a second time"));
        }
        let value = without_comment(rest).trim_matches(' ');
        match value_of(value) {
            Some(v) => map.push((key, v)),
            None => return refuse(&format!("the value of {key}")),
        }
    }
    build(&map)
}

/// The longest key this reader takes (PyYAML drops a simple key over 1024 characters; no risk key is near 64).
const MAX_KEY: usize = 64;

fn is_key(s: &str) -> bool {
    if s.len() > MAX_KEY {
        return false;
    }
    let mut c = s.chars();
    c.next().is_some_and(|f| f.is_ascii_alphabetic() || f == '_') && c.all(|x| x.is_ascii_alphanumeric() || x == '_')
}

/// The value part up to a comment: a `#` that follows a space (`rest` starts with the space after the colon).
fn without_comment(rest: &str) -> &str {
    match rest.match_indices('#').find(|(i, _)| rest[..*i].ends_with(' ')) {
        Some((i, _)) => &rest[..i],
        None => rest,
    }
}

fn is_word(s: &str) -> bool {
    let mut c = s.chars();
    c.next().is_some_and(|f| f.is_ascii_alphabetic()) && c.all(|x| x.is_ascii_alphanumeric() || x == '_')
}

/// A plain word PyYAML resolves to a string.
fn is_str_word(s: &str) -> bool {
    is_word(s) && !BOOLS.contains(&s) && !NULLS.contains(&s)
}

fn is_int(s: &str) -> bool {
    let d = s.strip_prefix(['+', '-']).unwrap_or(s);
    d == "0" || (d.starts_with(|c: char| ('1'..='9').contains(&c)) && d.bytes().all(|b| b.is_ascii_digit()))
}

/// PyYAML 1.1's float forms without an exponent: `[-+]?[0-9]+\.[0-9]*` and the unsigned `\.[0-9]+`. A signed `-.5`
/// is a string to PyYAML (LAB review on #383), so it is refused here, not read as a float.
fn is_decimal(s: &str) -> bool {
    let signed = s.starts_with(['+', '-']);
    let d = s.strip_prefix(['+', '-']).unwrap_or(s);
    let Some((a, b)) = d.split_once('.') else { return false };
    a.bytes().all(|x| x.is_ascii_digit()) && b.bytes().all(|x| x.is_ascii_digit()) && !(a.is_empty() && (signed || b.is_empty()))
}

/// A list item: a plain or quoted word, as the string PyYAML makes of it.
fn item(s: &str) -> Option<String> {
    for q in ['\'', '"'] {
        if let Some(inner) = s.strip_prefix(q).and_then(|t| t.strip_suffix(q)) {
            return inner.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_').then(|| inner.to_owned());
        }
    }
    is_str_word(s).then(|| s.to_owned())
}

fn value_of(s: &str) -> Option<Val> {
    if let Some(inner) = s.strip_prefix('[').and_then(|t| t.strip_suffix(']')) {
        if inner.trim_matches(' ').is_empty() {
            return Some(Val::List(vec![]));
        }
        return inner.split(',').map(|i| item(i.trim_matches(' '))).collect::<Option<Vec<_>>>().map(Val::List);
    }
    if let Some(i) = BOOLS.iter().position(|b| *b == s) {
        return Some(Val::Bool(i / 3 % 2 == 0)); // BOOLS in threes: yes, no, true, false, on, off
    }
    if is_int(s) {
        return s.parse::<i128>().ok().map(Val::Int);
    }
    if is_decimal(s) {
        return s.parse::<f64>().ok().filter(|f| f.is_finite()).map(Val::Float); // 400 digits are infinite as a float
    }
    is_str_word(s).then(|| Val::Word(s.to_owned()))
}

/// `float(v)` for `key`.
fn float(key: &str, v: &Val) -> Result<f64, String> {
    match v {
        Val::Int(i) => Ok(*i as f64), // Python's int-to-float and Rust's `as` both round half to even
        Val::Float(f) => Ok(*f),
        Val::Bool(b) => Ok(f64::from(u8::from(*b))),
        Val::Word(w) if ["inf", "infinity", "nan"].contains(&w.to_ascii_lowercase().as_str()) => {
            Err(format!("{OWN} ({key}: {w}, which float() reads as infinity or NaN)"))
        }
        Val::Word(w) => Err(format!("{INVALID} ValueError could not convert string to float: '{w}'")),
        Val::List(_) => Err(format!("{OWN} ({key}: a list for a number)")),
    }
}

/// `int(v)` for `key`.
fn int(key: &str, v: &Val) -> Result<i64, String> {
    let big = || format!("{OWN} ({key}: beyond 64 bits)");
    match v {
        Val::Int(i) => i64::try_from(*i).map_err(|_| big()),
        Val::Float(f) => {
            let t = f.trunc();
            if (-9.2e18..9.2e18).contains(&t) { Ok(t as i64) } else { Err(big()) }
        }
        Val::Bool(b) => Ok(i64::from(*b)),
        Val::Word(w) => Err(format!("{INVALID} ValueError invalid literal for int() with base 10: '{w}'")),
        Val::List(_) => Err(format!("{OWN} ({key}: a list for a number)")),
    }
}

/// `tuple(v)`.
fn tuple(v: &Val) -> Result<Vec<String>, String> {
    let not = |t: &str| Err(format!("{INVALID} TypeError '{t}' object is not iterable"));
    match v {
        Val::List(items) => Ok(items.clone()),
        Val::Word(w) => Ok(w.chars().map(String::from).collect()),
        Val::Int(_) => not("int"),
        Val::Float(_) => not("float"),
        Val::Bool(_) => not("bool"),
    }
}

/// risk.py `load` after `yaml.safe_load`: the casts in the argument order of `Risk(...)`, then the three checks.
fn build(map: &[(&str, Val)]) -> Result<Risk, String> {
    if map.is_empty() {
        return Err(format!("{INVALID} TypeError 'NoneType' object is not subscriptable")); // safe_load gave None
    }
    let get = |k: &str| map.iter().find(|(key, _)| *key == k).map(|(_, v)| v);
    let need = |k: &str| get(k).ok_or_else(|| format!("{INVALID} KeyError '{k}'"));
    let size_sol = float("size_sol", need("size_sol")?)?;
    let max_open_positions = int("max_open_positions", need("max_open_positions")?)?;
    let max_exposure_sol = float("max_exposure_sol", need("max_exposure_sol")?)?;
    let daily_loss_limit_sol = float("daily_loss_limit_sol", need("daily_loss_limit_sol")?)?;
    let kill = need("kill_switch")?; // bool() of anything succeeds; the type is checked last
    let families = tuple(need("families")?)?;
    let pool_cooldown_h = match get("pool_cooldown_h") {
        Some(v) => float("pool_cooldown_h", v)?,
        None => 0.0,
    };
    if !(0.0 < size_sol && size_sol <= max_exposure_sol) || max_open_positions < 1 || daily_loss_limit_sol <= 0.0 || pool_cooldown_h < 0.0 {
        return Err(LIMITS.to_owned());
    }
    let unknown: BTreeSet<&str> = families.iter().map(String::as_str).filter(|f| !FAMILIES.contains(f)).collect();
    if !unknown.is_empty() {
        let names: Vec<String> = unknown.iter().map(|f| format!("'{f}'")).collect();
        return Err(format!("{INVALID} unknown families [{}]", names.join(", ")));
    }
    let Val::Bool(kill_switch) = *kill else { return Err(format!("{INVALID} kill_switch must be true or false")) };
    if (size_sol * 1e9).abs() >= 9.2e18 {
        return Err(format!("{OWN} (size_sol: {size_sol}, beyond 64 bits of lamports)")); // Python's file would pass here
    }
    Ok(Risk { size_sol, max_open_positions, max_exposure_sol, daily_loss_limit_sol, kill_switch, families, pool_cooldown_h })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value as J;

    const CASES: &str = include_str!("../../paper/tests/data/c3_risk_load_cases.json");

    fn tmp(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("lab-live-risk-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn f(v: &J) -> f64 {
        v.as_f64().unwrap()
    }

    #[test]
    fn every_risk_load_case_gives_pythons_result_or_is_refused() {
        let doc: J = serde_json::from_str(CASES).unwrap();
        assert_eq!(doc["own_reason_prefix"], OWN);
        let dir = tmp("cases");
        let (mut same, mut refused) = (0, 0);
        for c in doc["cases"].as_array().unwrap() {
            let name = c["name"].as_str().unwrap();
            let got = match c["kind"].as_str().unwrap() {
                "text" => {
                    let p = dir.join(format!("{name}.yaml"));
                    std::fs::write(&p, c["text"].as_str().unwrap()).unwrap();
                    load(&p)
                }
                "missing" => load(&dir.join("no-such-file.yaml")),
                "dir" => load(&dir),
                "notdir" => {
                    let f = dir.join("a-file");
                    std::fs::write(&f, "").unwrap();
                    load(&f.join("risk.yaml"))
                }
                k => panic!("{name}: kind {k}"),
            };
            if c["rust"] == "refused" {
                refused += 1;
                assert!(got.as_ref().is_err_and(|e| e.starts_with(OWN)), "{name}: not refused: {got:?}");
                continue;
            }
            same += 1;
            let py = &c["python"];
            match (&got, py.get("risk")) {
                (Ok(r), Some(want)) => {
                    // floats to the bit (-0.0 included)
                    let bits = |x: f64| x.to_bits();
                    assert_eq!(bits(r.size_sol), bits(f(&want["size_sol"])), "{name}");
                    assert_eq!(bits(r.max_exposure_sol), bits(f(&want["max_exposure_sol"])), "{name}");
                    assert_eq!(bits(r.daily_loss_limit_sol), bits(f(&want["daily_loss_limit_sol"])), "{name}");
                    assert_eq!(bits(r.pool_cooldown_h), bits(f(&want["pool_cooldown_h"])), "{name}");
                    assert_eq!(r.max_open_positions, want["max_open_positions"].as_i64().unwrap(), "{name}");
                    assert_eq!(r.kill_switch, want["kill_switch"].as_bool().unwrap(), "{name}");
                    let fams: Vec<&str> = want["families"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
                    assert_eq!(r.families, fams, "{name}");
                    assert_eq!(r.size_lamports(), i128::from(want["size_lamports"].as_i64().unwrap()), "{name}");
                }
                (Err(e), None) => assert_eq!(e, py["reason"].as_str().unwrap(), "{name}"),
                _ => panic!("{name}: lab-live {got:?}, Python {py}"),
            }
        }
        assert!(same >= 70 && refused >= 30, "{same} same, {refused} refused");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_file_that_is_not_utf8_is_refused() {
        let dir = tmp("latin1");
        let p = dir.join("risk.yaml");
        std::fs::write(&p, b"size_sol: 0.5\n# caf\xe9\n").unwrap(); // Python's risk.load raises UnicodeDecodeError here
        assert_eq!(load(&p).unwrap_err(), format!("{OWN} (not UTF-8)"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn values_are_typed_as_pyyaml_types_them() {
        assert_eq!(value_of("0.5"), Some(Val::Float(0.5)));
        assert_eq!(value_of("+0.5"), Some(Val::Float(0.5)));
        assert_eq!(value_of("2."), Some(Val::Float(2.0)));
        assert_eq!(value_of(".5"), Some(Val::Float(0.5)));
        assert_eq!(value_of("-0"), Some(Val::Int(0)));
        assert_eq!(value_of("24"), Some(Val::Int(24)));
        assert_eq!(value_of("No"), Some(Val::Bool(false)));
        assert_eq!(value_of("ON"), Some(Val::Bool(true)));
        assert_eq!(value_of("F7"), Some(Val::Word("F7".into())));
        assert_eq!(value_of("[F7, 'a', \"\"]"), Some(Val::List(vec!["F7".into(), "a".into(), String::new()])));
        for refused in ["", ".", "-.5", "+.5", "1e9", "1.5e+3", "010", "0x10", "1_000", "~", "null", "'x'", "[F7,]", "[yes]", "[7]", "[[F7]]", "{a: 1}",
                        "_x", "[\"a b\"]", "'"] {
            assert_eq!(value_of(refused), None, "{refused:?}");
        }
        for (w, b) in BOOLS.iter().zip([true, true, true, false, false, false, true, true, true, false, false, false, true, true, true,
                                        false, false, false]) {
            assert_eq!(value_of(w), Some(Val::Bool(b)), "{w}");
        }
    }
}
