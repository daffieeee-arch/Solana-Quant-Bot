//! Byte meter per stream per minute, and the daily budget (§6 rule 6 of the Triton edge note).
//!
//! Bytes are the protobuf size of each update as received (`encoded_len`), before the zstd
//! transport compression: an upper bound of what Triton counts, so the budget errs on the safe
//! side. The first hour of the top-up compares it with Triton's own figure (`--wire-ratio`).
//!
//! The day is the UTC day. Spent bytes are kept in `<root>/budget.json` (written atomically and
//! fsynced), so a restart does not reset them. At the start the day's spend is the larger of
//! budget.json and the usage rows of today's segments: a lost or stale budget.json never starts
//! the day at 0. When today's spend cannot be known (an unreadable budget.json or `_segment.json`
//! of today, or a segment directory of today without `_segment.json` and no budget.json of today),
//! the recorder stops for the day (fail-closed) until the owner restarts it with
//! `--budget-assume-spent YYYY-MM-DD=BYTES`.
//!
//! Received bytes never leave the spend (Codex 3, LAB review): budget.json holds a reservation
//! (`reserved_bytes`), the spend this run may reach before its next save, and a byte is only counted
//! below it. Before the first byte the run saves one (`reserve_ahead`), and once the spend comes within
//! half a reservation of it a new one is saved (`add`). A save that fails ends the recorder
//! (`BudgetWriteError`, exit 74 like a segment write error), so nothing is received that the disk does
//! not cover. (A single update larger than half a reservation, at least 512 KiB, is covered only once
//! its save succeeds; the recorder's updates are a few KB, sliced accounts and transactions.) A
//! restart takes the larger of `spent_bytes` and `reserved_bytes`: after a crash, a kill or a failed
//! write it counts the whole reservation (at most one sustainable minute more than came); a clean end
//! settles it to the spend (`settle`). Only a budget.json with a reservation covers a segment directory
//! of today without `_segment.json`. The stop at the cap never waits for a save in `tick`, also on a
//! minute's roll. A reservation that `add` cannot save ends the run with exit 74, also at the cap: the
//! restart then stops at once (75), because the reservation on disk is past the cap, but the open
//! segment's last slots are not written.
//!
//! When the day's spend projects above the cap (current rate until midnight), the
//! lowest-priority stream is dropped (downscale), one level at a time; at the cap itself the
//! recorder stops and does not start again that day. Every decision is a usage row (stream
//! "budget", with a note) in the segment, so the engine journals it.

use anyhow::Result;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const GB: f64 = 1e9;
/// The smallest reservation (a run with a tiny cap still saves at most every half MiB).
const RESERVE_MIN: u64 = 1 << 20;

/// budget.json could not be written: the recorder ends (main.rs, exit 74). The reservation on disk
/// still covers every byte counted so far.
#[derive(Debug)]
pub struct BudgetWriteError(pub String);

impl std::fmt::Display for BudgetWriteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "budget.json could not be written ({})", self.0)
    }
}

impl std::error::Error for BudgetWriteError {}

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
    /// The spend budget.json covers on disk: the last reservation saved (`spent` at most).
    pub reserved: u64,
    /// One reservation: a sustainable minute (cap / 1440), at least RESERVE_MIN.
    reserve: u64,
    pub stopped: bool,
    recent: VecDeque<u64>,
    last_downscale_unix: u64,
    /// Measuring runs (`--no-downscale`): no projected or spike downscale, only the hard stop at the cap.
    pub no_downscale: bool,
    /// Why today's spend is unknown, when that stopped the day (`load_with`).
    pub unknown: Option<String>,
}

const RECENT_MINUTES: usize = 5;
/// A minute above this multiple of the sustainable rate (cap / 1440) downscales at once.
const SPIKE: u64 = 20;

fn day_of(unix: u64) -> u64 {
    unix / 86_400
}

/// The UTC date "YYYY-MM-DD" of a day number (days since 1970-01-01; H. Hinnant's civil_from_days).
pub fn date_of_day(day: u64) -> String {
    let z = day as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

/// The day number of a UTC date "YYYY-MM-DD" (days_from_civil); None for anything else.
pub fn day_from_date(s: &str) -> Option<u64> {
    let mut p = s.split('-');
    let (y, m, d): (i64, i64, i64) = (p.next()?.parse().ok()?, p.next()?.parse().ok()?, p.next()?.parse().ok()?);
    if p.next().is_some() || !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * ((m + 9) % 12) + 2) / 5 + d - 1;
    let day = u64::try_from(era * 146_097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719_468).ok()?;
    (date_of_day(day) == s).then_some(day) // 2026-02-30 is no date
}

/// Today's usage as the segments under `root` record it (segments written before the day, by file
/// time, cannot hold its minutes and are not read).
#[derive(Default)]
pub struct SegmentsUsage {
    /// The bytes of `day`'s usage rows in complete segments: a lower bound of the day's spend that
    /// survives a lost budget.json.
    pub bytes: u64,
    /// Segments of the day whose `_segment.json` cannot be read: their usage is unknown.
    pub unreadable: Vec<String>,
    /// Segment directories of the day without `_segment.json` (an interrupted write): their minutes
    /// are only in budget.json.
    pub unfinished: Vec<String>,
}

pub fn segments_usage(root: &Path, day: u64) -> Result<SegmentsUsage> {
    let mut out = SegmentsUsage::default();
    let Ok(entries) = std::fs::read_dir(root.join("segments")) else { return Ok(out) };
    let day_start = UNIX_EPOCH + Duration::from_secs(day * 86_400);
    let before = |t: std::io::Result<SystemTime>| t.map_or(false, |t| t < day_start);
    for e in entries {
        let e = e?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.parse::<u64>().is_err() || !e.file_type()?.is_dir() {
            continue; // not a segment
        }
        let meta = e.path().join("_segment.json");
        match std::fs::metadata(&meta) {
            Ok(md) if before(md.modified()) => {}
            Ok(_) => match std::fs::read(&meta).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
                Some(v) => {
                    for u in v["usage"].as_array().into_iter().flatten() {
                        if u["minute"].as_u64().map(|m| day_of(m * 60)) == Some(day) {
                            out.bytes += u["bytes"].as_u64().unwrap_or(0);
                        }
                    }
                }
                None => out.unreadable.push(name),
            },
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                if !before(e.metadata().and_then(|md| md.modified())) {
                    out.unfinished.push(name);
                }
            }
            Err(_) => out.unreadable.push(name),
        }
    }
    out.unreadable.sort();
    out.unfinished.sort();
    Ok(out)
}

/// The bytes of `day`'s usage rows in the complete segments under `root` (`segments_usage`).
pub fn segments_spent(root: &Path, day: u64) -> Result<u64> {
    Ok(segments_usage(root, day)?.bytes)
}

impl Meter {
    /// The meter of `root`, with the day's spend so far from `budget.json` (`load_with`, no override).
    #[cfg(test)]
    pub fn load(root: &Path, cap_bytes: u64, wire_ratio: f64, now_unix: u64) -> Result<Self> {
        Self::load_with(root, cap_bytes, wire_ratio, now_unix, None)
    }

    /// The meter of `root`, with the day's spend so far: the larger of today's budget.json and
    /// today's segments. When that spend cannot be known the day is stopped (fail-closed), unless
    /// `assume` is the owner's override `(day, bytes)` for this day (`--budget-assume-spent`): then
    /// the spend is at least `bytes`, and a budget row notes it.
    pub fn load_with(root: &Path, cap_bytes: u64, wire_ratio: f64, now_unix: u64, assume: Option<(u64, u64)>) -> Result<Self> {
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
            reserved: 0,
            reserve: (cap_bytes / 1440).max(RESERVE_MIN),
            stopped: false,
            recent: VecDeque::new(),
            last_downscale_unix: 0,
            no_downscale: false,
            unknown: None,
        };
        let day_start = UNIX_EPOCH + Duration::from_secs(m.day * 86_400);
        // budget.json holds today's spend (`today`), another day's, or nothing we can read. With its
        // reservation (`covered`) it counts every byte its run received.
        let (mut today, mut covered) = (false, false);
        let mut unknown = Vec::new();
        match std::fs::read(&m.path) {
            Ok(b) => match serde_json::from_slice::<Value>(&b) {
                Ok(v) if v["day"].as_u64() == Some(m.day) => match v["spent_bytes"].as_u64() {
                    Some(spent) => {
                        let reserved = v["reserved_bytes"].as_u64();
                        m.spent = spent.max(reserved.unwrap_or(0));
                        if let Some(r) = reserved.filter(|r| *r > spent) {
                            m.note(format!("the last run ended without settling its budget: today's spend counts its \
                                            reservation, {} bytes above the {spent} saved", r - spent));
                        }
                        m.stopped = v["stopped"].as_bool().unwrap_or(false);
                        today = true;
                        covered = reserved.is_some();
                    }
                    None => unknown.push("budget.json of today has no spent_bytes".to_owned()),
                },
                Ok(_) => {}
                // Written before today (file time): it cannot hold today's spend.
                Err(e) if std::fs::metadata(&m.path).and_then(|md| md.modified()).is_ok_and(|t| t < day_start) => {
                    log::warn!("budget: {} is unreadable ({e}) but older than today", m.path.display());
                }
                Err(e) => unknown.push(format!("budget.json is unreadable ({e})")),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => unknown.push(format!("budget.json cannot be read ({e})")),
        }
        let seg = segments_usage(root, m.day)?;
        if seg.bytes > m.spent {
            log::warn!("budget: today's segments hold {} bytes, budget.json {}: taking the segments", seg.bytes, m.spent);
            m.spent = seg.bytes;
        }
        // An unreadable _segment.json of today: its usage is unknown. A directory without one (an
        // interrupted write) is covered by today's budget.json with its reservation, saved before any of
        // its bytes were counted; a budget.json without one (an older recorder's: saved at the minute's
        // roll, so a failed segment's minutes and the running one can lie past it) or none leaves it
        // unknown.
        unknown.extend(seg.unreadable.iter().map(|s| format!("segments/{s}/_segment.json is unreadable")));
        if !covered {
            let why = if today { "today's budget.json has no reservation to cover it" } else { "no budget.json of today" };
            unknown.extend(seg.unfinished.iter().map(|s| format!("segments/{s} has no _segment.json (an interrupted write; {why})")));
        }
        if !unknown.is_empty() {
            let why = unknown.join("; ");
            let date = date_of_day(m.day);
            match assume.filter(|(day, _)| *day == m.day) {
                Some((_, bytes)) => {
                    m.spent = m.spent.max(bytes);
                    m.note(format!("today's spend was unknown ({why}); the owner's override assumes {bytes} bytes for {date}: spent {}", m.spent));
                }
                None => {
                    log::error!("budget: today's spend is unknown ({why}): stopped for {date}. Once checked, the owner can restart with \
                                 --budget-assume-spent {date}=<bytes> (at least {} from the readable segments)", m.spent);
                    m.stopped = true;
                    m.unknown = Some(why);
                }
            }
        }
        // A recovered spend at the cap stops before the stream starts (main checks `stopped` first).
        m.stopped |= m.spent >= m.cap_bytes;
        m.reserved = m.spent;
        Ok(m)
    }

    /// Before the first byte of a run: a reservation on disk (main.rs; a failure ends the run before
    /// it streams).
    pub fn reserve_ahead(&mut self) -> Result<()> {
        self.save_reservation(self.spent + self.reserve)
    }

    /// A new reservation on disk; `reserved` moves only once it is saved.
    fn save_reservation(&mut self, upto: u64) -> Result<()> {
        let before = std::mem::replace(&mut self.reserved, upto);
        self.save().inspect_err(|_| self.reserved = before)
    }

    /// Bytes of one update on `stream` (counted with the wire ratio). Within half a reservation of the
    /// one on disk a new one is saved first; when that fails the recorder ends (`BudgetWriteError`),
    /// with every byte so far below the reservation on disk.
    pub fn add(&mut self, stream: &str, bytes: usize) -> Result<()> {
        let b = crate::wire::metered(bytes, self.wire_ratio);
        let e = self.current.entry(stream.to_owned()).or_insert((0, 0));
        e.0 += b;
        e.1 += 1;
        self.spent += b;
        if self.spent + self.reserve / 2 > self.reserved {
            self.save_reservation(self.spent + self.reserve)?;
        }
        Ok(())
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

    /// Close the running minute's usage (it goes into the next segment), without saving or moving the
    /// clock: the day, `spent` and `stopped` stay exactly as they are.
    pub fn close_minute(&mut self) {
        self.roll();
    }

    /// The last save of a run that counts no more bytes (shutdown, the hard stop, an exit on a write
    /// error): the reservation comes down to the spend, so the next start counts no bytes that never
    /// came. When it fails, the reservation on disk still covers the spend.
    pub fn settle(&mut self) -> Result<()> {
        self.reserved = self.spent;
        self.save()
    }

    /// `close_minute` and `settle`: the end of a run that writes no segment after it.
    #[cfg(test)]
    pub fn flush(&mut self) -> Result<()> {
        self.roll();
        self.settle()
    }

    /// budget.json, atomically and fsynced; a failure is a `BudgetWriteError`.
    pub fn save(&self) -> Result<()> {
        let write = || -> Result<()> {
            crate::segment::write_json_atomic(&self.path, &self.state())?;
            if let Some(dir) = self.path.parent() {
                std::fs::File::open(dir)?.sync_all()?; // the rename itself on disk
            }
            Ok(())
        };
        write().map_err(|e| BudgetWriteError(format!("{e:#}")).into())
    }

    pub fn state(&self) -> Value {
        let mut v = json!({"day": self.day, "spent_bytes": self.spent, "reserved_bytes": self.reserved.max(self.spent),
                           "cap_bytes": self.cap_bytes, "spent_gb": self.spent as f64 / GB, "stopped": self.stopped,
                           "wire_ratio": self.wire_ratio});
        if let Some(why) = &self.unknown {
            v["unknown"] = json!(why);
        }
        v
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
                // Normally 0; after a clock step past midnight the new day's segments are the floor.
                self.spent = match self.path.parent() {
                    Some(root) => segments_spent(root, self.day)?,
                    None => 0,
                };
                self.reserved = self.spent + self.reserve; // the new day's first reservation, saved below
                self.stopped = false;
                self.recent.clear();
                restore = true;
            }
            // A failed save ends the run (below), unless the cap stops it here anyway.
            if let Err(e) = self.save() {
                if self.spent < self.cap_bytes {
                    return Err(e);
                }
                log::error!("budget: {e:#} at the cap; the stop goes on");
            }
        }
        if self.spent >= self.cap_bytes {
            // The stop never waits for the disk (LAB review): the reservation there already counts every
            // byte, so a restart stops again.
            self.stopped = true;
            if let Err(e) = self.save() {
                log::error!("budget: the stop could not be saved ({e:#}); the reservation on disk covers the spend");
            }
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
        m.add("tx", 100).unwrap();
        m.add("tx", 50).unwrap();
        m.add("blocks", 10).unwrap();
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
        m.add("vaults", 5_000).unwrap();
        // 5,000 bytes in the first minute projects ~7.2 MB > cap.
        assert!(matches!(m.tick(T0 + 60).unwrap(), Decision::Downscale(_)));
        assert_eq!(m.tick(T0 + 61).unwrap(), Decision::Continue); // not twice within 30 s
        m.add("tx", 1_500_000).unwrap();
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
        m.add("pool_creations", 25_000).unwrap(); // a spike and a projection far above the cap
        assert_eq!(m.tick(T0 + 5).unwrap(), Decision::Continue);
        assert_eq!(m.tick(T0 + 60).unwrap(), Decision::Continue);
        m.add("tx_new_reversed", 1_500_000).unwrap();
        assert!(matches!(m.tick(T0 + 120).unwrap(), Decision::Stop(_)));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn flush_closes_the_minute_without_moving_the_day() {
        let root = tmp("meter-flush");
        let late = T0 + 86_400 - (T0 % 86_400) - 20; // 20 s before the end of the UTC day
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, late).unwrap();
        m.add("tx", 100).unwrap();
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
        m.add("pool_accounts", 25_000).unwrap();
        assert!(matches!(m.tick(T0 + 5).unwrap(), Decision::Downscale(_)));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_new_day_restores_all_streams() {
        let root = tmp("meter-day");
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, T0 + 86_400 - 30).unwrap();
        m.add("tx", 10).unwrap();
        assert_eq!(m.tick(T0 + 86_400 + 1).unwrap(), Decision::Restore);
        assert_eq!(m.spent, 0);
        std::fs::remove_dir_all(&root).unwrap();
    }

    fn segment(root: &Path, seq: u64, usage: Value) {
        let d = root.join(format!("segments/{seq:09}"));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("_segment.json"), json!({"seq": seq, "complete": true, "usage": usage}).to_string()).unwrap();
    }

    #[test]
    fn a_lost_stale_or_unreadable_budget_takes_todays_segments() {
        let root = tmp("budget-recover");
        let minute = T0 / 60; // 2026-10-12 00:00
        segment(&root, 0, json!([{"minute": minute + 5, "stream": "tx", "bytes": 100, "messages": 1},
                                  {"minute": minute + 5, "stream": "budget", "bytes": 0, "messages": 0, "note": "x"}]));
        segment(&root, 1, json!([{"minute": minute + 6, "stream": "tx", "bytes": 250, "messages": 2},
                                  {"minute": minute - 10, "stream": "tx", "bytes": 999, "messages": 1}])); // yesterday's minute
        assert_eq!(segments_spent(&root, day_of(T0)).unwrap(), 350);
        let load = || Meter::load(&root, 25_000_000_000, 1.0, T0 + 3_600).unwrap().spent;
        std::fs::write(root.join("budget.json"), b"{\"day\": 20738, \"spent_by").unwrap(); // cut off
        assert_eq!(load(), 350); // not 0; and stopped (an_unknown_spend_of_today_stops_until_the_owners_override)
        assert!(Meter::load(&root, 25_000_000_000, 1.0, T0 + 3_600).unwrap().stopped);
        std::fs::write(root.join("budget.json"), json!({"day": day_of(T0), "spent_bytes": 50}).to_string()).unwrap();
        assert_eq!(load(), 350); // a stale budget.json: the segments are larger
        std::fs::write(root.join("budget.json"), json!({"day": day_of(T0), "spent_bytes": 500}).to_string()).unwrap();
        assert_eq!(load(), 500); // the running minutes not yet in a segment
        std::fs::write(root.join("budget.json"), json!({"day": day_of(T0) - 1, "spent_bytes": 9_999}).to_string()).unwrap();
        assert_eq!(load(), 350); // yesterday's budget.json: not today's spend, the segments count
        std::fs::remove_file(root.join("budget.json")).unwrap();
        assert_eq!(load(), 350);
        // A recovered spend at the cap is stopped before any byte is streamed.
        assert!(Meter::load(&root, 300, 1.0, T0 + 3_600).unwrap().stopped);
        assert!(!Meter::load(&root, 400, 1.0, T0 + 3_600).unwrap().stopped);
        // A new day in tick takes that day's segments as the floor (a clock step past midnight).
        let next = T0 + 86_400;
        segment(&root, 2, json!([{"minute": next / 60 + 1, "stream": "tx", "bytes": 70, "messages": 1}]));
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, T0 + 3_600).unwrap();
        assert_eq!(m.tick(next + 120).unwrap(), Decision::Restore);
        assert_eq!(m.spent, 70);
        // save() writes it back atomically
        let m = Meter::load(&root, 25_000_000_000, 1.0, T0 + 3_600).unwrap();
        m.save().unwrap();
        let v: Value = serde_json::from_slice(&std::fs::read(root.join("budget.json")).unwrap()).unwrap();
        assert_eq!(v["spent_bytes"].as_u64(), Some(350));
        assert!(!root.join("budget.json.tmp").exists());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn dates_and_day_numbers() {
        assert_eq!((date_of_day(day_of(T0)), date_of_day(0), date_of_day(11_016)), ("2026-10-12".into(), "1970-01-01".into(), "2000-02-29".into()));
        assert_eq!((day_from_date("2026-10-12"), day_from_date("1970-01-01"), day_from_date("2000-02-29")), (Some(day_of(T0)), Some(0), Some(11_016)));
        for bad in ["2026-02-30", "2100-02-29", "2026-13-01", "2026-10-1x", "2026-10", "2026-10-12-1", "1969-12-31", ""] {
            assert_eq!(day_from_date(bad), None, "{bad}");
        }
    }

    #[test]
    fn an_unknown_spend_of_today_stops_until_the_owners_override() {
        let root = tmp("budget-unknown");
        let (minute, today) = (T0 / 60, day_of(T0));
        let load = |assume| Meter::load_with(&root, 25_000_000_000, 1.0, T0 + 3_600, assume).unwrap();
        let unknown = |m: &Meter, what: &str| m.stopped && m.unknown.as_deref().is_some_and(|u| u.contains(what));
        // A first start (nothing on disk), and no budget.json with complete readable segments: as before.
        let m = load(None);
        assert!(!m.stopped && m.spent == 0 && m.unknown.is_none());
        segment(&root, 0, json!([{"minute": minute + 5, "stream": "tx", "bytes": 100, "messages": 1}]));
        let m = load(None);
        assert!(!m.stopped && m.spent == 100 && m.unknown.is_none());
        // 1. An unreadable budget.json of today.
        std::fs::write(root.join("budget.json"), b"{\"day\": 20738, \"spent_by").unwrap();
        let m = load(None);
        assert!(unknown(&m, "budget.json is unreadable"));
        assert_eq!(m.state()["unknown"].as_str(), m.unknown.as_deref());
        // The owner's override holds for its own day only, and never goes below the segments.
        assert!(unknown(&load(Some((today - 1, 5_000))), "budget.json is unreadable"));
        let m = load(Some((today, 5_000)));
        assert!(!m.stopped && m.spent == 5_000 && m.unknown.is_none());
        let mut m = load(Some((today, 50)));
        assert_eq!(m.spent, 100);
        m.flush().unwrap(); // the override is a budget row, and budget.json readable again
        assert!(m.take_usage().iter().any(|r| r["stream"] == "budget" && r["note"].as_str().unwrap().contains("the owner's override")));
        assert!(!load(None).stopped);
        std::fs::write(root.join("budget.json"), json!({"day": today}).to_string()).unwrap();
        assert!(unknown(&load(None), "no spent_bytes"));
        // 2. An unreadable _segment.json of today: unknown, also beside today's budget.json.
        std::fs::write(root.join("budget.json"), json!({"day": today, "spent_bytes": 400}).to_string()).unwrap();
        std::fs::write(root.join("segments/000000000/_segment.json"), b"{\"seq\": 0, \"comp").unwrap();
        assert!(unknown(&load(None), "segments/000000000/_segment.json is unreadable"));
        segment(&root, 0, json!([{"minute": minute + 5, "stream": "tx", "bytes": 100, "messages": 1}]));
        // 3. A segment directory of today without _segment.json (an interrupted write): today's
        // budget.json covers its minutes only with its reservation (every byte was below it before it
        // was counted). One without (an older recorder's, saved at the minute's roll), or none, leaves
        // its usage unknown.
        std::fs::create_dir_all(root.join("segments/000000001/blocks")).unwrap();
        assert!(unknown(&load(None), "today's budget.json has no reservation to cover it"));
        std::fs::write(root.join("budget.json"), json!({"day": today, "spent_bytes": 400, "reserved_bytes": 450}).to_string()).unwrap();
        let mut m = load(None);
        assert!(!m.stopped && m.spent == 450 && m.unknown.is_none());
        m.close_minute(); // the reservation counted is a budget row
        assert!(m.take_usage().iter().any(|r| r["stream"] == "budget" && r["note"].as_str().unwrap().contains("50 bytes above the 400")));
        std::fs::remove_file(root.join("budget.json")).unwrap();
        assert!(unknown(&load(None), "segments/000000001 has no _segment.json"));
        std::fs::write(root.join("budget.json"), json!({"day": today - 1, "spent_bytes": 9}).to_string()).unwrap();
        assert!(unknown(&load(None), "segments/000000001 has no _segment.json")); // yesterday's budget.json does not cover it
        // A later day: files written before it hold none of its spend, readable or not.
        std::fs::write(root.join("budget.json"), b"{broken").unwrap();
        std::fs::write(root.join("segments/000000000/_segment.json"), b"{broken").unwrap();
        let later = Meter::load_with(&root, 25_000_000_000, 1.0, T0 + 3_650 * 86_400, None).unwrap();
        assert!(!later.stopped && later.spent == 0 && later.unknown.is_none());
        std::fs::remove_dir_all(&root).unwrap();
    }

    /// budget.json cannot be written (`write_json_atomic` writes budget.json.tmp first).
    fn block_saves(root: &Path) {
        std::fs::create_dir_all(root.join("budget.json.tmp")).unwrap();
    }

    fn unblock_saves(root: &Path) {
        std::fs::remove_dir_all(root.join("budget.json.tmp")).unwrap();
    }

    #[test]
    fn a_kill_never_gives_back_the_bytes_of_the_running_minute() {
        let root = tmp("budget-kill");
        let cap = 25_000_000_000;
        let mut m = Meter::load(&root, cap, 1.0, T0).unwrap();
        m.reserve_ahead().unwrap();
        m.add("tx", 1_000).unwrap();
        assert_eq!(m.tick(T0 + 61).unwrap(), Decision::Continue); // the minute's roll saves budget.json
        m.add("tx", 5_000).unwrap(); // the running minute, after that save
        drop(m); // killed (OOM, SIGKILL, power): no flush, no settle
        let again = Meter::load(&root, cap, 1.0, T0 + 90).unwrap();
        assert!(again.spent >= 6_000 && !again.stopped && again.unknown.is_none(), "{}", again.spent);
        // A clean end settles: the next start counts what came, no more.
        let mut m = again;
        m.add("tx", 10).unwrap();
        let spent = m.spent;
        m.flush().unwrap();
        assert_eq!(Meter::load(&root, cap, 1.0, T0 + 95).unwrap().spent, spent);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_failed_segment_write_and_a_failed_budget_write_never_give_room_back() {
        // Codex 3: today's budget.json was saved at a roll; then budget.json cannot be written, a segment
        // takes the next minutes' usage and cannot be written either, and the save before the exit fails
        // too. The restart finds an older budget.json of today beside the unfinished segment directory.
        let root = tmp("budget-both-fail");
        let cap = 25_000_000_000;
        let mut m = Meter::load(&root, cap, 1.0, T0).unwrap();
        m.reserve_ahead().unwrap();
        m.add("tx", 1_000).unwrap();
        m.tick(T0 + 61).unwrap(); // budget.json of today: 1,000 spent
        block_saves(&root);
        m.add("tx", 2_000).unwrap(); // below the reservation on disk: counted without a save
        let e = m.tick(T0 + 121).unwrap_err(); // the roll's save fails: the recorder ends (main.rs: exit 74)
        assert!(e.downcast_ref::<BudgetWriteError>().is_some(), "{e:#}");
        let rows = m.take_usage(); // close_segment took these minutes' usage ...
        assert!(rows.iter().any(|r| r["bytes"] == 2_000));
        std::fs::create_dir_all(root.join("segments/000000000/blocks")).unwrap(); // ... and its write failed
        assert!(m.settle().is_err()); // the save before the exit fails as well
        drop(m);
        unblock_saves(&root);
        let again = Meter::load(&root, cap, 1.0, T0 + 150).unwrap();
        assert!(again.spent >= 3_000 && again.unknown.is_none(), "{}", again.spent);
        // An older recorder's budget.json (no reservation) beside that directory proves nothing: stopped
        // until the owner's override.
        std::fs::write(root.join("budget.json"), json!({"day": day_of(T0), "spent_bytes": 1_000}).to_string()).unwrap();
        let old = Meter::load(&root, cap, 1.0, T0 + 150).unwrap();
        assert!(old.stopped && old.unknown.as_deref().is_some_and(|u| u.contains("no reservation")));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_failed_save_ends_the_run_below_its_reservation_and_the_stop_never_waits_for_one() {
        let root = tmp("budget-stop");
        let cap = 1_440_000; // a reservation is RESERVE_MIN here
        let mut m = Meter::load(&root, cap, 1.0, T0).unwrap();
        m.no_downscale = true;
        m.reserve_ahead().unwrap();
        block_saves(&root);
        // Updates of 100 kB: the sixth comes within half a reservation of the one on disk.
        let mut counted = 0;
        let e = loop {
            match m.add("tx", 100_000) {
                Ok(()) => counted += 100_000,
                Err(e) => break e,
            }
        };
        assert!(e.downcast_ref::<BudgetWriteError>().is_some());
        assert!(m.spent == counted + 100_000 && m.spent <= RESERVE_MIN, "{}", m.spent); // every byte below the disk's
        unblock_saves(&root);
        // At the cap the stop comes also when budget.json cannot be written (LAB review: it was lost, and
        // the recorder streamed on past the cap, reconnecting on every tick).
        let mut m = Meter::load(&root, cap, 1.0, T0).unwrap();
        m.no_downscale = true;
        m.reserve_ahead().unwrap();
        while m.spent < cap {
            m.add("tx", 100_000).unwrap();
        }
        block_saves(&root);
        assert!(matches!(m.tick(T0 + 5).unwrap(), Decision::Stop(_)));
        assert!(matches!(m.tick(T0 + 6).unwrap(), Decision::Stop(_)));
        assert!(matches!(m.tick(T0 + 61).unwrap(), Decision::Stop(_))); // also on a minute's roll, whose save fails
        drop(m);
        unblock_saves(&root);
        assert!(Meter::load(&root, cap, 1.0, T0 + 70).unwrap().stopped); // the reservation on disk is past the cap
        // The cap crossed in one minute, first seen on the next minute's roll with budget.json not writable.
        let root2 = tmp("budget-stop-roll");
        let mut m = Meter::load(&root2, cap, 1.0, T0).unwrap();
        m.no_downscale = true;
        m.reserve_ahead().unwrap();
        while m.spent < cap {
            m.add("tx", 100_000).unwrap();
        }
        block_saves(&root2);
        assert!(matches!(m.tick(T0 + 61).unwrap(), Decision::Stop(_)));
        std::fs::remove_dir_all(&root).unwrap();
        std::fs::remove_dir_all(&root2).unwrap();
    }

    #[test]
    fn an_owners_override_is_on_disk_before_the_unfinished_segment_is_removed() {
        // LAB review: main.rs saves the reservation before Segments::open removes the directory that made today's
        // spend unknown, so a run that ends right after keeps the override's spend for the next start.
        let root = tmp("budget-override");
        let (cap, today) = (25_000_000_000, day_of(T0));
        std::fs::write(root.join("budget.json"), json!({"day": today, "spent_bytes": 1_000}).to_string()).unwrap();
        std::fs::create_dir_all(root.join("segments/000000000/blocks")).unwrap();
        assert!(Meter::load_with(&root, cap, 1.0, T0 + 60, None).unwrap().unknown.is_some());
        let mut m = Meter::load_with(&root, cap, 1.0, T0 + 60, Some((today, 5_000_000_000))).unwrap();
        assert!(!m.stopped && m.spent == 5_000_000_000);
        m.reserve_ahead().unwrap();
        std::fs::remove_dir_all(root.join("segments/000000000")).unwrap(); // Segments::open; then the run ends
        drop(m);
        let again = Meter::load_with(&root, cap, 1.0, T0 + 90, None).unwrap(); // the next start, without the override
        assert!(again.spent >= 5_000_000_000 && again.unknown.is_none(), "{}", again.spent);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_new_day_saves_its_first_reservation_and_a_failed_one_ends_the_run() {
        let root = tmp("budget-day-fails");
        let mut m = Meter::load(&root, 25_000_000_000, 1.0, T0 + 86_400 - 30).unwrap();
        m.reserve_ahead().unwrap();
        m.add("tx", 10).unwrap();
        block_saves(&root);
        assert!(m.tick(T0 + 86_400 + 1).unwrap_err().downcast_ref::<BudgetWriteError>().is_some());
        unblock_saves(&root);
        assert_eq!(m.tick(T0 + 86_400 + 61).unwrap(), Decision::Continue); // (the restore of a run that went on)
        let v: Value = serde_json::from_slice(&std::fs::read(root.join("budget.json")).unwrap()).unwrap();
        assert_eq!((v["day"].as_u64(), v["spent_bytes"].as_u64()), (Some(day_of(T0 + 86_400)), Some(0)));
        assert!(v["reserved_bytes"].as_u64().unwrap() >= RESERVE_MIN);
        std::fs::remove_dir_all(&root).unwrap();
    }
}
