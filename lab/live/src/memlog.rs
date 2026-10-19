//! `--mem-log FILE` of `lab-live paper` (M1 C4-2; the spec is research/m1-c4-plateau-spec-2026-10-19.md §3): one JSON
//! line per minute of chain time and one at the end, for C4's plateau gate (lab/live/plateau.py).
//!
//! - A minute is the paper engine's: the block time's minute of the last slot it ended (the heartbeat's). The engine ends
//!   a slot when the first event of a later slot arrives, so minute M's line is written after the push that ended minute
//!   M+1's first slot: `{minute, slot, events, segments, rss_bytes, heap_bytes, heap_allocs, sqlite_bytes, structures:
//!   {name: {entries, bytes_est, group}}, time_ns}`. `slot` is that push's event's, `events` the events before it,
//!   `segments` the segments read so far (null on chunks); `group` is snapshot, legs_buffer or state (`group`); the
//!   structures' keys come sorted by name (serde_json's map).
//! - The heap is the counting allocator's (mem.rs): the live **Rust** heap. The bundled SQLite's own memory is
//!   `sqlite_bytes`; libzstd's contexts are only in RSS. The structures are lab_core's `mem_report` (the snapshot's tables
//!   and the engines' state) and the replay's own (legs_read.rs `ReplayMem`: the interned keys, the 24 h farmer
//!   histories, the pool contexts and the legs buffer of the chunk or segment being emitted). `bytes_est` is capacity ×
//!   size_of, a lower bound (lab_core::mem).
//! - `time_ns`: the wall time of each event's `PaperEngine::push` plus the drain into the journal, in log buckets (`Hist`),
//!   for the pushes between two lines: n, p50, p90, p99, p999, max and the buckets `[[lower bound, count], ...]` (so hours
//!   can be summed). **Processing time in a replay, not live latency** (Triton's stamp to the decision is M3's, design
//!   §10).
//! - At the end (after `finish`): the last minute's line, a line for the minute `finish` started if it did (with no
//!   events), then the end line, `"end": true`, with the whole run's `time_ns` and the replay's structures after its last
//!   chunk or unit. Every line is flushed when written, so a killed run leaves whole lines.

use crate::mem;
use anyhow::{Context, Result};
use lab_core::mem::Mem;
use serde_json::{json, Map, Value};
use std::fs;
use std::io::{BufWriter, Write};
use std::path::Path;

/// Sub-buckets per power of two: 32, so each bucket above 64 ns is at most 1/32 (3.2 %) of its lower bound wide.
const SUB_BITS: u32 = 5;
const SUB: usize = 1 << SUB_BITS;
/// Times from 2^40 ns (about 18 minutes) on share the last bucket.
const TOP_BITS: u32 = 40;
const BUCKETS: usize = (TOP_BITS - SUB_BITS) as usize * SUB + SUB;

/// A bucket's index: one per nanosecond below 2 × SUB, then SUB per power of two.
fn index(v: u64) -> usize {
    let v = v.min((1 << TOP_BITS) - 1);
    if v < 2 * SUB as u64 {
        return v as usize;
    }
    let e = 63 - v.leading_zeros(); // > SUB_BITS
    let m = (v >> (e - SUB_BITS)) as usize; // in [SUB, 2 SUB)
    (e - SUB_BITS + 1) as usize * SUB + (m - SUB)
}

/// A bucket's values: [lower, upper).
fn bounds(i: usize) -> (u64, u64) {
    if i < 2 * SUB {
        return (i as u64, i as u64 + 1);
    }
    let e = (i / SUB) as u32 + SUB_BITS - 1;
    let m = (i % SUB + SUB) as u64;
    (m << (e - SUB_BITS), (m + 1) << (e - SUB_BITS))
}

/// Event times in log buckets, with their exact count and maximum.
#[derive(Clone)]
pub struct Hist {
    counts: Vec<u64>,
    n: u64,
    max: u64,
}

impl Default for Hist {
    fn default() -> Self {
        Hist { counts: vec![0; BUCKETS], n: 0, max: 0 }
    }
}

impl Hist {
    pub fn add(&mut self, ns: u64) {
        self.counts[index(ns)] += 1;
        self.n += 1;
        self.max = self.max.max(ns);
    }

    /// The p-quantile (0 < p <= 1): the highest value of the bucket holding the ceil(p × n)-th time, at most the maximum
    /// (exact below 64 ns, else at most 3.2 % above the true time). 0 without times.
    pub fn quantile(&self, p: f64) -> u64 {
        if self.n == 0 {
            return 0;
        }
        let rank = ((p * self.n as f64).ceil() as u64).clamp(1, self.n);
        let mut seen = 0;
        for (i, &c) in self.counts.iter().enumerate() {
            seen += c;
            if seen >= rank {
                return (bounds(i).1 - 1).min(self.max);
            }
        }
        self.max
    }

    pub fn json(&self) -> Value {
        let buckets: Vec<Value> = self.counts.iter().enumerate().filter(|(_, &c)| c > 0).map(|(i, &c)| json!([bounds(i).0, c])).collect();
        json!({"n": self.n, "p50": self.quantile(0.5), "p90": self.quantile(0.9), "p99": self.quantile(0.99), "p999": self.quantile(0.999),
               "max": self.max, "buckets": buckets})
    }
}

/// A structure's group in the breakdown the coordinator asked for (spec §3): what the snapshot loads (the universe, the
/// graduation info, the store's curve_hold, the interned keys, which the run's traders extend), the legs buffer of the
/// chunk or segment being emitted, and the engines' and the replay's state.
pub fn group(name: &str) -> &'static str {
    match name {
        "universe_tokens" | "universe_sol_markets" | "grad_tokens" | "grad_sol_markets" | "curve_hold" | "interned_keys" => "snapshot",
        "legs_buffer" => "legs_buffer",
        _ => "state",
    }
}

/// The structures as the line's `structures` object (keys sorted by name). A name reported twice is a bug: refused.
pub fn structures(mems: &[Mem]) -> Value {
    let mut m = Map::new();
    for x in mems {
        let prev = m.insert(x.name.to_owned(), json!({"entries": x.entries, "bytes_est": x.bytes, "group": group(x.name)}));
        assert!(prev.is_none(), "structure {} reported twice", x.name);
    }
    Value::Object(m)
}

/// The log's writer (module doc).
pub struct MemLog {
    out: BufWriter<fs::File>,
    minute: Option<i64>,
    events: u64,
    lines: u64,
    this_minute: Hist,
    all: Hist,
}

impl MemLog {
    pub fn create(path: &Path) -> Result<Self> {
        let f = fs::File::create(path).with_context(|| format!("create {}", path.display()))?;
        Ok(MemLog { out: BufWriter::new(f), minute: None, events: 0, lines: 0, this_minute: Hist::default(), all: Hist::default() })
    }

    /// One event: its time, and the engine's minute after its push. When the minute moved on, the line of the minute
    /// before is written first (its events end before this one); `mems` is asked only then.
    pub fn event(&mut self, ns: u64, minute: Option<i64>, slot: u64, segments: Option<u64>, mems: impl FnOnce() -> Vec<Mem>) -> Result<()> {
        if minute != self.minute {
            if let Some(m) = self.minute {
                self.line(json!({"minute": m}), slot, segments, &mems())?;
                self.this_minute = Hist::default();
            }
            self.minute = minute;
        }
        self.this_minute.add(ns);
        self.all.add(ns);
        self.events += 1;
        Ok(())
    }

    fn line(&mut self, mut v: Value, slot: u64, segments: Option<u64>, mems: &[Mem]) -> Result<()> {
        v["slot"] = json!(slot);
        v["events"] = json!(self.events);
        v["segments"] = json!(segments);
        v["rss_bytes"] = json!(mem::rss_bytes());
        v["heap_bytes"] = json!(mem::heap_bytes());
        v["heap_allocs"] = json!(mem::heap_allocs());
        v["sqlite_bytes"] = json!(mem::sqlite_bytes());
        v["structures"] = structures(mems);
        v["time_ns"] = self.this_minute.json();
        serde_json::to_writer(&mut self.out, &v)?;
        self.out.write_all(b"\n")?;
        self.out.flush()?; // once per chain minute: a killed run leaves whole lines
        self.lines += 1;
        Ok(())
    }

    /// The end of the replay (after `finish` and its drain), with the engine's minute after `finish`: the last minute's
    /// line; a line for the minute `finish` started, if it started one (no events: its histogram is empty); then the end
    /// line with the whole run's times. Returns the lines written.
    pub fn end(mut self, minute_after: Option<i64>, slot: u64, segments: Option<u64>, mems: &[Mem]) -> Result<u64> {
        if let Some(m) = self.minute {
            self.line(json!({"minute": m}), slot, segments, mems)?;
        }
        if minute_after.is_some() && minute_after != self.minute {
            self.this_minute = Hist::default();
            self.line(json!({"minute": minute_after}), slot, segments, mems)?;
            self.minute = minute_after;
        }
        self.this_minute = self.all.clone();
        self.line(json!({"minute": self.minute, "end": true}), slot, segments, mems)?;
        Ok(self.lines)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_buckets_are_contiguous_and_at_most_a_32nd_wide() {
        let mut prev = 0;
        for i in 0..BUCKETS {
            let (lo, hi) = bounds(i);
            assert_eq!(lo, prev, "bucket {i}");
            assert!(hi > lo && (lo < 64 || (hi - lo) * 32 <= lo), "bucket {i}: [{lo}, {hi})");
            assert_eq!((index(lo), index(hi - 1)), (i, i), "bucket {i}");
            prev = hi;
        }
        assert_eq!(prev, 1 << TOP_BITS);
        assert_eq!((index(u64::MAX), index(1 << TOP_BITS)), (BUCKETS - 1, BUCKETS - 1));
    }

    #[test]
    fn the_quantiles_are_their_buckets_top_capped_by_the_max() {
        let mut h = Hist::default();
        assert_eq!((h.quantile(0.5), h.json()["n"].as_u64()), (0, Some(0)));
        for ns in 1..=100 {
            h.add(ns);
        }
        // 50 is exact (below 64 ns); 90 lies in [90, 92), so 91; 99 in [98, 100), so 99; the max is 100.
        assert_eq!([h.quantile(0.5), h.quantile(0.9), h.quantile(0.99), h.quantile(1.0)], [50, 91, 99, 100]);
        h.add(1_000_000_000); // 1 s: p999's rank is the 101st time, the max
        assert_eq!((h.quantile(0.999), h.max), (1_000_000_000, 1_000_000_000));
        let j = h.json();
        assert_eq!(j["buckets"].as_array().unwrap().iter().map(|b| b[1].as_u64().unwrap()).sum::<u64>(), 101);
        let q = h.quantile(0.995); // the 101st of 101 again
        assert_eq!(q, 1_000_000_000);
    }

    #[test]
    fn a_line_per_minute_then_the_end() {
        let dir = std::env::temp_dir().join(format!("lab-live-memlog-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("mem.jsonl");
        let mut log = MemLog::create(&path).unwrap();
        let mems = || vec![Mem::new("a", 1, 8), Mem::new("b", 2, 16)];
        // two events before the first dated minute, three in minute 10, one in minute 11 (written at the end)
        let mut asked = 0;
        for (ns, minute, slot) in [(5, None, 1), (7, Some(10), 2), (9, Some(10), 2), (11, Some(10), 3), (13, Some(11), 4)] {
            log.event(ns, minute, slot, Some(1), || {
                asked += 1;
                mems()
            })
            .unwrap();
        }
        assert_eq!(asked, 1);
        assert_eq!(log.end(Some(11), 5, Some(2), &mems()).unwrap(), 3);
        let lines: Vec<Value> = fs::read_to_string(&path).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        assert_eq!(lines.len(), 3);
        // minute 10: written at slot 4's event; its time histogram holds the events since None moved to 10 (7, 9, 11),
        // the first event (5, no minute yet) went to the same histogram before it
        let l = &lines[0];
        assert_eq!((l["minute"].as_i64(), l["slot"].as_u64(), l["events"].as_u64(), l["segments"].as_u64()), (Some(10), Some(4), Some(4), Some(1)));
        assert_eq!((l["time_ns"]["n"].as_u64(), l["time_ns"]["max"].as_u64()), (Some(4), Some(11)));
        assert_eq!(l["structures"], json!({"a": {"entries": 1, "bytes_est": 8, "group": "state"}, "b": {"entries": 2, "bytes_est": 16, "group": "state"}}));
        assert!(l["sqlite_bytes"].is_i64());
        assert_eq!([group("curve_hold"), group("interned_keys"), group("legs_buffer"), group("recent_legs")], ["snapshot", "snapshot", "legs_buffer", "state"]);
        assert!(l["heap_bytes"].as_u64().unwrap() > 0 && l["rss_bytes"].as_u64().unwrap() > 0);
        assert_eq!((lines[1]["minute"].as_i64(), lines[1]["time_ns"]["n"].as_u64(), lines[1]["events"].as_u64()), (Some(11), Some(1), Some(5)));
        assert_eq!((lines[2]["end"].as_bool(), lines[2]["minute"].as_i64(), lines[2]["time_ns"]["n"].as_u64()), (Some(true), Some(11), Some(5)));
        // finish started minute 12: its own line (no events), then the end line labelled 12
        let path2 = dir.join("mem2.jsonl");
        let mut log = MemLog::create(&path2).unwrap();
        for (ns, minute) in [(5, Some(10)), (6, Some(11))] {
            log.event(ns, minute, 1, None, mems).unwrap();
        }
        assert_eq!(log.end(Some(12), 2, None, &mems()).unwrap(), 4);
        let l2: Vec<Value> = fs::read_to_string(&path2).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        let got: Vec<(Option<i64>, Option<u64>, bool)> =
            l2.iter().map(|l| (l["minute"].as_i64(), l["time_ns"]["n"].as_u64(), l["end"].as_bool() == Some(true))).collect();
        assert_eq!(got, [(Some(10), Some(1), false), (Some(11), Some(1), false), (Some(12), Some(0), false), (Some(12), Some(2), true)]);
        let _ = fs::remove_dir_all(&dir);
    }
}
