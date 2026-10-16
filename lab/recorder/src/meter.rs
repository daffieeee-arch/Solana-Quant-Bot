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
//! `--budget-assume-spent YYYY-MM-DD=BYTES`. When the day's spend projects above the cap (current rate until midnight), the
//! lowest-priority stream is dropped (downscale), one level at a time; at the cap itself the
//! recorder stops and does not start again that day. Every decision is a usage row (stream
//! "budget", with a note) in the segment, so the engine journals it.

use anyhow::Result;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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
            stopped: false,
            recent: VecDeque::new(),
            last_downscale_unix: 0,
            no_downscale: false,
            unknown: None,
        };
        let day_start = UNIX_EPOCH + Duration::from_secs(m.day * 86_400);
        // budget.json holds today's spend (`today`), another day's, or nothing we can read.
        let mut today = false;
        let mut unknown = Vec::new();
        match std::fs::read(&m.path) {
            Ok(b) => match serde_json::from_slice::<Value>(&b) {
                Ok(v) if v["day"].as_u64() == Some(m.day) => match v["spent_bytes"].as_u64() {
                    Some(spent) => {
                        m.spent = spent;
                        m.stopped = v["stopped"].as_bool().unwrap_or(false);
                        today = true;
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
        // interrupted write) is covered by today's budget.json, which is saved at every minute's roll,
        // before a segment takes that minute's usage; without today's budget.json it is unknown.
        unknown.extend(seg.unreadable.iter().map(|s| format!("segments/{s}/_segment.json is unreadable")));
        if !today {
            unknown.extend(seg.unfinished.iter().map(|s| format!("segments/{s} has no _segment.json (an interrupted write)")));
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
        crate::segment::write_json_atomic(&self.path, &self.state())?;
        if let Some(dir) = self.path.parent() {
            std::fs::File::open(dir)?.sync_all()?; // the rename itself on disk
        }
        Ok(())
    }

    pub fn state(&self) -> Value {
        let mut v = json!({"day": self.day, "spent_bytes": self.spent, "cap_bytes": self.cap_bytes,
                           "spent_gb": self.spent as f64 / GB, "stopped": self.stopped, "wire_ratio": self.wire_ratio});
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
        // budget.json covers its minutes; without one its usage is unknown.
        std::fs::create_dir_all(root.join("segments/000000001/blocks")).unwrap();
        let m = load(None);
        assert!(!m.stopped && m.spent == 400);
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
}
