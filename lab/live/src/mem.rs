//! The process's memory, from /proc/self/status (Linux; None elsewhere): the progress lines and the baseline of the
//! read (state `lab_live_measure`).

fn status_kb(key: &str) -> Option<u64> {
    let s = std::fs::read_to_string("/proc/self/status").ok()?;
    s.lines().find_map(|l| l.strip_prefix(key))?.trim().trim_end_matches("kB").trim().parse().ok()
}

/// The resident memory now (VmRSS), in bytes.
pub fn rss_bytes() -> Option<u64> {
    status_kb("VmRSS:").map(|kb| kb * 1024)
}

/// The peak resident memory so far (VmHWM), in bytes.
pub fn peak_rss_bytes() -> Option<u64> {
    status_kb("VmHWM:").map(|kb| kb * 1024)
}

/// Bytes as whole MB for a log line, `?` when unknown.
pub fn mb(bytes: Option<u64>) -> String {
    bytes.map_or_else(|| "?".to_owned(), |b| (b / 1_000_000).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_rss_is_read_from_proc() {
        let (now, peak) = (rss_bytes().unwrap(), peak_rss_bytes().unwrap());
        assert!(now > 1_000_000 && peak >= now); // a test binary holds more than 1 MB
        assert_eq!((mb(Some(2_500_000)), mb(None)), ("2".to_owned(), "?".to_owned()));
    }
}
