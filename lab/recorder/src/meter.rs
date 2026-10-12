//! Byte meter per stream per minute, and the daily budget (§6 rule 6 of the Triton edge note).
//!
//! Bytes are the protobuf size of each update as received (`encoded_len`), before the zstd
//! transport compression: an upper bound of what Triton counts, so the budget errs on the safe
//! side. The first hour of the top-up compares it with Triton's own figure (`--wire-ratio`).
//!
//! The day is the UTC day. Spent bytes are kept in `<root>/budget.json`, so a restart does not
//! reset them. When the day's spend projects above the cap (current rate until midnight), the
//! lowest-priority stream is dropped (downscale), one level at a time; at the cap itself the
//! recorder stops and does not start again that day. Every decision is a usage row (stream
//! "budget", with a note) in the segment, so the engine journals it.

use anyhow::Result;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::path::{Path, PathBuf};

pub const GB: f64 = 1e9;

#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    Continue,
    /// Drop the lowest-priority stream still on.
    Downscale(String),
    /// A new UTC day: back to all streams.
    Restore,
    /// The day's cap is spent.
    Stop(String),
}

pub struct Meter {
    path: PathBuf,
    pub cap_bytes: u64,
    wire_ratio: f64,
    minute: u64,
    current: BTreeMap<String, (u64, u64)>,
    notes: Vec<String>,
    done: Vec<Value>,
    pub day: u64,
    pub spent: u64,
    pub stopped: bool,
    recent: VecDeque<u64>,
    last_downscale_unix: u64,
    /// Measuring runs (`--no-downscale`): no projected or spike downscale, only the hard stop at the cap.
    pub no_downscale: bool,
}

const RECENT_MINUTES: usize = 5;
/// A minute above this multiple of the sustainable rate (cap / 1440) downscales at once.
const SPIKE: u64 = 20;

fn day_of(unix: u64) -> u64 {
    unix / 86_400
}

impl Meter {
    /// The meter of `root`, with the day's spend so far from `budget.json`.
    pub fn load(root: &Path, cap_bytes: u64, wire_ratio: f64, now_unix: u64) -> Result<Self> {
        let path = root.join("budget.json");
        let mut m = Self {
            path,
            cap_bytes,
            wire_ratio,
            minute: now_unix / 60,
            current: BTreeMap::new(),
            notes: Vec::new(),
            done: Vec::new(),
            day: day_of(now_unix),
            spent: 0,
            stopped: false,
            recent: VecDeque::new(),
            last_downscale_unix: 0,
            no_downscale: false,
        };
        if let Some(v) = std::fs::read(&m.path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
            if v["day"].as_u64() == Some(m.day) {
                m.spent = v["spent_bytes"].as_u64().unwrap_or(0);
                m.stopped = v["stopped"].as_bool().unwrap_or(false);
            }
        }
        Ok(m)
    }

    /// Bytes of one update on `stream` (counted with the wire ratio).
    pub fn add(&mut self, stream: &str, bytes: usize) {
        let b = (bytes as f64 * self.wire_ratio).ceil() as u64;
        let e = self.current.entry(stream.to_owned()).or_insert((0, 0));
        e.0 += b;
        e.1 += 1;
        self.spent += b;
    }

    /// A note for this minute's "budget" usage row.
    pub fn note(&mut self, text: String) {
        log::warn!("budget: {text}");
        self.notes.push(text);
    }

    fn roll(&mut self) {
        let total: u64 = self.current.values().map(|(b, _)| b).sum();
        for (stream, (bytes, messages)) in std::mem::take(&mut self.current) {
            self.done.push(json!({"minute": self.minute, "stream": stream, "bytes": bytes, "messages": messages}));
        }
        if !self.notes.is_empty() {
            self.done.push(json!({"minute": self.minute, "stream": "budget", "bytes": 0, "messages": 0,
                                  "note": std::mem::take(&mut self.notes).join("; ")}));
        }
        self.recent.push_back(total);
        while self.recent.len() > RECENT_MINUTES {
            self.recent.pop_front();
        }
    }

    /// Close the running minute's usage (it goes into the next segment) and save the state, without
    /// moving the clock: the day, `spent` and `stopped` stay exactly as they are.
    pub fn flush(&mut self) -> Result<()> {
        self.roll();
        self.save()
    }

    pub fn save(&self) -> Result<()> {
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_vec_pretty(&self.state())?)?;
        std::fs::rename(&tmp, &self.path)?;
        Ok(())
    }

    pub fn state(&self) -> Value {
        json!({"day": self.day, "spent_bytes": self.spent, "cap_bytes": self.cap_bytes,
               "spent_gb": self.spent as f64 / GB, "stopped": self.stopped, "wire_ratio": self.wire_ratio})
    }

    /// Advance to `now_unix`: close finished minutes, and decide.
    pub fn tick(&mut self, now_unix: u64) -> Result<Decision> {
        let minute = now_unix / 60;
        let mut restore = false;
        if minute != self.minute {
            self.roll();
            self.minute = minute;
            if day_of(now_unix) != self.day {
                self.day = day_of(now_unix);
                self.spent = 0;
                self.stopped = false;
                self.recent.clear();
                restore = true;
            }
            self.save()?;
        }
        if self.spent >= self.cap_bytes {
            self.stopped = true;
            self.save()?;
            return Ok(Decision::Stop(format!("{:.2} GB spent today, cap {:.2} GB", self.spent as f64 / GB, self.cap_bytes as f64 / GB)));
        }
        if restore {
            return Ok(Decision::Restore);
        }
        if self.no_downscale {
            return Ok(Decision::Continue);
        }
        // At most one downscale per 30 s, so the dropped stream's last bytes do not count twice.
        if now_unix < self.last_downscale_unix + 30 {
            return Ok(Decision::Continue);
        }
        let this_minute: u64 = self.current.values().map(|(b, _)| b).sum();
        let sustainable = self.cap_bytes / 1440;
        let reason = if this_minute > sustainable * SPIKE {
            Some(format!("{:.3} GB in this minute (> {SPIKE}x the sustainable {:.3} GB/min)", this_minute as f64 / GB, sustainable as f64 / GB))
        } else if !self.recent.is_empty() {
            let rate = self.recent.iter().sum::<u64>() / self.recent.len() as u64;
            let minutes_left = 1440 - (now_unix % 86_400) / 60;
            let projected = self.spent + rate * minutes_left;
            (projected > self.cap_bytes).then(|| {
                format!("projected {:.2} GB today at {:.3} GB/min > cap {:.2} GB", projected as f64 / GB, rate as f64 / GB, self.cap_bytes as f64 / GB)
            })
        } else {
            None
        };
        Ok(match reason {
            Some(r) => {
                self.last_downscale_unix = now_unix;
                Decision::Downscale(r)
            }
            None => Decision::Continue,
        })
    }

    /// Finished minutes not yet written to a segment (one row per minute and stream).
    pub fn take_usage(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.done)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-recorder-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    const T0: u64 = 1_791_763_200; // 2026-10-12 00:00 UTC

    #[test]
    fn usage_rows_are_per_minute_and_stream() {
        let root = tmp("meter-rows");
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, T0).unwrap();
        m.add("tx", 100);
        m.add("tx", 50);
        m.add("blocks", 10);
        assert_eq!(m.tick(T0 + 30).unwrap(), Decision::Continue);
        assert!(m.take_usage().is_empty()); // the minute is not finished
        m.tick(T0 + 61).unwrap();
        let rows = m.take_usage();
        assert_eq!(rows.len(), 2);
        assert!(rows.iter().any(|r| r["stream"] == "tx" && r["bytes"] == 150 && r["messages"] == 2 && r["minute"] == T0 / 60));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn projection_downscales_then_the_cap_stops_and_survives_a_restart() {
        let root = tmp("meter-budget");
        let cap = 1_440_000; // 1,000 bytes per minute is sustainable
        let mut m = Meter::load(&root, cap, 1.0, T0).unwrap();
        m.add("vaults", 5_000);
        // 5,000 bytes in the first minute projects ~7.2 MB > cap.
        assert!(matches!(m.tick(T0 + 60).unwrap(), Decision::Downscale(_)));
        assert_eq!(m.tick(T0 + 61).unwrap(), Decision::Continue); // not twice within 30 s
        m.add("tx", 1_500_000);
        assert!(matches!(m.tick(T0 + 120).unwrap(), Decision::Stop(_)));
        let again = Meter::load(&root, cap, 1.0, T0 + 200).unwrap();
        assert!(again.stopped && again.spent >= cap);
        // The next UTC day starts from zero.
        let next = Meter::load(&root, cap, 1.0, T0 + 86_400).unwrap();
        assert!(!next.stopped && next.spent == 0);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_measuring_run_never_downscales_but_stops_at_the_cap() {
        let root = tmp("meter-measure");
        let cap = 1_440_000;
        let mut m = Meter::load(&root, cap, 1.0, T0).unwrap();
        m.no_downscale = true;
        m.add("pool_creations", 25_000); // a spike and a projection far above the cap
        assert_eq!(m.tick(T0 + 5).unwrap(), Decision::Continue);
        assert_eq!(m.tick(T0 + 60).unwrap(), Decision::Continue);
        m.add("tx_new_reversed", 1_500_000);
        assert!(matches!(m.tick(T0 + 120).unwrap(), Decision::Stop(_)));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn flush_closes_the_minute_without_moving_the_day() {
        let root = tmp("meter-flush");
        let late = T0 + 86_400 - (T0 % 86_400) - 20; // 20 s before the end of the UTC day
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, late).unwrap();
        m.add("tx", 100);
        m.flush().unwrap();
        assert!(m.take_usage().iter().any(|r| r["stream"] == "tx" && r["bytes"] == 100));
        assert_eq!((m.day, m.spent, m.stopped), (day_of(late), 100, false));
        let saved = Meter::load(&root, 25_000_000_000, 1.0, late + 5).unwrap();
        assert_eq!(saved.spent, 100);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_spike_downscales_within_the_minute() {
        let root = tmp("meter-spike");
        let mut m = Meter::load(&root, 1_440_000, 1.0, T0).unwrap();
        m.add("pool_accounts", 25_000);
        assert!(matches!(m.tick(T0 + 5).unwrap(), Decision::Downscale(_)));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_new_day_restores_all_streams() {
        let root = tmp("meter-day");
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, T0 + 86_400 - 30).unwrap();
        m.add("tx", 10);
        assert_eq!(m.tick(T0 + 86_400 + 1).unwrap(), Decision::Restore);
        assert_eq!(m.spent, 0);
        std::fs::remove_dir_all(&root).unwrap();
    }
}
