//! The paper journal's SQLite schema, written from Rust: the same file, tables, columns, keys and pragmas as
//! paper/journal.py, so paper.journal_diff and paper.report read a lab-live journal as an engine journal
//! (lab/docs/rust-live-route-design-2026-10-16.md §3).
//!
//! The schema is not copied: paper/journal_schema.json is generated from journal.py's TABLES (a Python test
//! keeps it current) and compiled in here, and every table is created with journal.py's own statement text.
//! Writes are INSERT OR REPLACE of every column (absent ones NULL), in a transaction until `commit`, as there.

use anyhow::{anyhow, bail, Context, Result};
use rusqlite::types::Value;
use rusqlite::{params_from_iter, Connection};
use serde_json::Value as J;
use std::path::{Path, PathBuf};

pub const SCHEMA_JSON: &str = include_str!("../../paper/journal_schema.json");

/// One table of the schema: its name, its column declarations (journal.py's `cols`), its primary key and the
/// column names.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Table {
    pub name: String,
    pub decl: String,
    pub key: String,
    pub cols: Vec<String>,
}

pub fn schema() -> Result<Vec<Table>> {
    let v: J = serde_json::from_str(SCHEMA_JSON).context("paper/journal_schema.json")?;
    v["tables"]
        .as_array()
        .ok_or_else(|| anyhow!("journal_schema.json: no tables"))?
        .iter()
        .map(|t| {
            let field = |k: &str| t[k].as_str().map(str::to_owned).ok_or_else(|| anyhow!("journal_schema.json: a table without {k}"));
            let decl = field("cols")?;
            let cols = decl.split(", ").map(|c| c.split_whitespace().next().unwrap_or_default().to_owned()).collect();
            Ok(Table { name: field("name")?, decl, key: field("key")?, cols })
        })
        .collect()
}

pub struct Journal {
    pub dir: PathBuf,
    db: Connection,
    tables: Vec<Table>,
    in_tx: bool,
}

impl Journal {
    /// Open or create `<dir>/journal.sqlite` with every table of the schema; a journal from before a column was
    /// added gets it (ALTER TABLE ADD COLUMN), as journal.py does.
    pub fn open(dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(dir).with_context(|| format!("create {}", dir.display()))?;
        let db = Connection::open(dir.join("journal.sqlite")).with_context(|| format!("open {}/journal.sqlite", dir.display()))?;
        let mode: String = db.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
        if !mode.eq_ignore_ascii_case("wal") {
            bail!("journal_mode is {mode}, not wal");
        }
        db.execute_batch("PRAGMA synchronous=NORMAL")?;
        let tables = schema()?;
        for t in &tables {
            db.execute_batch(&format!("CREATE TABLE IF NOT EXISTS {} ({}, PRIMARY KEY ({}))", t.name, t.decl, t.key))?;
            let have: Vec<String> = db
                .prepare(&format!("PRAGMA table_info({})", t.name))?
                .query_map([], |r| r.get::<_, String>(1))?
                .collect::<rusqlite::Result<_>>()?;
            for c in t.decl.split(", ") {
                if !have.iter().any(|h| Some(h.as_str()) == c.split_whitespace().next()) {
                    db.execute_batch(&format!("ALTER TABLE {} ADD COLUMN {c}", t.name))?;
                }
            }
        }
        Ok(Self { dir: dir.to_owned(), db, tables, in_tx: false })
    }

    fn table(&self, name: &str) -> Result<&Table> {
        self.tables.iter().find(|t| t.name == name).ok_or_else(|| anyhow!("the journal has no table {name}"))
    }

    fn begin(&mut self) -> Result<()> {
        if !self.in_tx {
            self.db.execute_batch("BEGIN")?;
            self.in_tx = true;
        }
        Ok(())
    }

    /// INSERT OR REPLACE one row: every column of the table, NULL where `row` does not name it. A name the table
    /// does not have is an error (journal.py ignores it; here it is a bug in the caller).
    pub fn put(&mut self, table: &str, row: &[(&str, Value)]) -> Result<()> {
        let t = self.table(table)?.clone();
        if let Some((bad, _)) = row.iter().find(|(k, _)| !t.cols.iter().any(|c| c == k)) {
            bail!("{table} has no column {bad}");
        }
        let vals = t.cols.iter().map(|c| row.iter().find(|(k, _)| k == c).map_or(Value::Null, |(_, v)| v.clone()));
        let sql = format!("INSERT OR REPLACE INTO {} ({}) VALUES ({})", t.name, t.cols.join(", "), vec!["?"; t.cols.len()].join(", "));
        self.begin()?;
        self.db.execute(&sql, params_from_iter(vals))?;
        Ok(())
    }

    /// DELETE the rows whose key columns equal `key` (journal.py:88-89), in the open transaction as `put`'s writes. The
    /// key cells bind with the types `put` binds (an integer t as INTEGER), so `k = ?` finds the row `put` wrote. Refused:
    /// an empty key (Python would build `WHERE ` with nothing after it) and a column the table does not have.
    pub fn delete(&mut self, table: &str, key: &[(&str, Value)]) -> Result<()> {
        let t = self.table(table)?.clone();
        if key.is_empty() {
            bail!("a delete from {table} without a key");
        }
        if let Some((bad, _)) = key.iter().find(|(k, _)| !t.cols.iter().any(|c| c == k)) {
            bail!("{table} has no column {bad}");
        }
        let cond: Vec<String> = key.iter().map(|(k, _)| format!("{k} = ?")).collect();
        let sql = format!("DELETE FROM {} WHERE {}", t.name, cond.join(" AND "));
        self.begin()?;
        self.db.execute(&sql, params_from_iter(key.iter().map(|(_, v)| v.clone())))?;
        Ok(())
    }

    pub fn state(&mut self, k: &str, v: &str) -> Result<()> {
        self.put("state", &[("k", Value::Text(k.to_owned())), ("v", Value::Text(v.to_owned()))])
    }

    pub fn get_state(&self, k: &str) -> Result<Option<String>> {
        let mut st = self.db.prepare("SELECT v FROM state WHERE k = ?1")?;
        let mut rows = st.query([k])?;
        Ok(match rows.next()? {
            Some(r) => Some(r.get(0)?),
            None => None,
        })
    }

    /// The rows of a table (this connection's, uncommitted ones included).
    pub fn count(&self, table: &str) -> Result<u64> {
        let t = self.table(table)?;
        let n: i64 = self.db.query_row(&format!("SELECT count(*) FROM {}", t.name), [], |r| r.get(0))?;
        Ok(u64::try_from(n)?)
    }

    pub fn commit(&mut self) -> Result<()> {
        if self.in_tx {
            self.db.execute_batch("COMMIT")?;
            self.in_tx = false;
        }
        Ok(())
    }

    pub fn close(mut self) -> Result<()> {
        self.commit()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-live-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn the_schema_is_journal_pys() {
        let t = schema().unwrap();
        let names: Vec<&str> = t.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["signals", "orders", "fills", "exits", "heartbeat", "usage", "positions", "state", "learned"]);
        let state = t.iter().find(|t| t.name == "state").unwrap();
        assert_eq!((state.decl.as_str(), state.key.as_str(), state.cols.clone()), ("k TEXT, v TEXT", "k", vec!["k".to_owned(), "v".to_owned()]));
        assert_eq!(t.iter().find(|t| t.name == "fills").unwrap().key, "family, variant, key, t, side");
    }

    #[test]
    fn a_delete_removes_the_row_put_wrote_and_refuses_a_bad_key() {
        let dir = tmp("journal-delete");
        let mut j = Journal::open(&dir).unwrap();
        let key = |t: i64| vec![("family", Value::Text("F7".into())), ("variant", Value::Text("tp4_T600".into())),
                                ("key", Value::Text("P".into())), ("t", Value::Integer(t))];
        for t in [5, 6] {
            let mut row = key(t);
            row.push(("status", Value::Text("open".into())));
            j.put("positions", &row).unwrap();
        }
        j.delete("positions", &key(5)).unwrap();
        assert_eq!(j.count("positions").unwrap(), 1);
        j.delete("positions", &[("t", Value::Text("6".into()))]).unwrap(); // a text '6' is not the integer 6
        assert_eq!(j.count("positions").unwrap(), 1);
        assert!(j.delete("positions", &[]).unwrap_err().to_string().contains("without a key"));
        assert!(j.delete("positions", &[("slot", Value::Integer(1))]).unwrap_err().to_string().contains("no column slot"));
        j.close().unwrap();
        let j = Journal::open(&dir).unwrap(); // the delete was committed with the puts
        assert_eq!(j.count("positions").unwrap(), 1);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_journal_has_every_table_with_its_columns_and_keeps_its_rows() {
        let dir = tmp("journal");
        let mut j = Journal::open(&dir).unwrap();
        j.state("feed", r#"{"kind": "replay"}"#).unwrap();
        j.put("signals", &[("family", Value::Text("F7".into())), ("variant", Value::Text("tp4_T600".into())),
                           ("key", Value::Text("P".into())), ("t", Value::Integer(5)), ("status", Value::Text("entered".into()))]).unwrap();
        assert!(j.put("signals", &[("nope", Value::Null)]).unwrap_err().to_string().contains("no column nope"));
        j.close().unwrap();
        let j = Journal::open(&dir).unwrap(); // reopening keeps the rows and adds nothing
        assert_eq!(j.get_state("feed").unwrap().as_deref(), Some(r#"{"kind": "replay"}"#));
        let n: i64 = j.db.query_row("SELECT count(*) FROM signals WHERE status = 'entered' AND depth IS NULL", [], |r| r.get(0)).unwrap();
        assert_eq!((n, j.count("signals").unwrap(), j.count("fills").unwrap()), (1, 1, 0));
        assert!(j.count("nope").is_err());
        for t in schema().unwrap() {
            let cols: Vec<(String, String, i64)> = j.db.prepare(&format!("PRAGMA table_info({})", t.name)).unwrap()
                .query_map([], |r| Ok((r.get(1)?, r.get(2)?, r.get(5)?))).unwrap().collect::<rusqlite::Result<_>>().unwrap();
            assert_eq!(cols.iter().map(|c| c.0.clone()).collect::<Vec<_>>(), t.cols, "{}", t.name);
            let pk: Vec<&str> = t.key.split(", ").collect();
            for (name, _, k) in &cols {
                assert_eq!(*k > 0, pk.contains(&name.as_str()), "{}.{name}", t.name);
            }
        }
        let mode: String = j.db.query_row("PRAGMA journal_mode", [], |r| r.get(0)).unwrap();
        assert_eq!(mode, "wal");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
