//! The process's memory: from /proc/self/status (Linux; None elsewhere) for the progress lines and the baseline of the
//! read (state `lab_live_measure`), and the live heap from the counting allocator (M1 C4-2, `--mem-log`).

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

static LIVE_BYTES: AtomicU64 = AtomicU64::new(0);
static LIVE_ALLOCS: AtomicU64 = AtomicU64::new(0);

/// The global allocator (main.rs): `System`, counting the live heap's bytes (as requested, so the allocator's own
/// rounding and retained pages are RSS's part) and its live allocations, with two relaxed atomics per call.
pub struct Counting;

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        let p = unsafe { System.alloc(l) };
        if !p.is_null() {
            LIVE_BYTES.fetch_add(l.size() as u64, Relaxed);
            LIVE_ALLOCS.fetch_add(1, Relaxed);
        }
        p
    }

    unsafe fn alloc_zeroed(&self, l: Layout) -> *mut u8 {
        let p = unsafe { System.alloc_zeroed(l) };
        if !p.is_null() {
            LIVE_BYTES.fetch_add(l.size() as u64, Relaxed);
            LIVE_ALLOCS.fetch_add(1, Relaxed);
        }
        p
    }

    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) };
        LIVE_BYTES.fetch_sub(l.size() as u64, Relaxed);
        LIVE_ALLOCS.fetch_sub(1, Relaxed);
    }

    unsafe fn realloc(&self, p: *mut u8, l: Layout, new_size: usize) -> *mut u8 {
        let q = unsafe { System.realloc(p, l, new_size) };
        if !q.is_null() {
            if new_size >= l.size() {
                LIVE_BYTES.fetch_add((new_size - l.size()) as u64, Relaxed);
            } else {
                LIVE_BYTES.fetch_sub((l.size() - new_size) as u64, Relaxed);
            }
        }
        q
    }
}

/// The live heap's bytes now (0 when `Counting` is not the global allocator).
pub fn heap_bytes() -> u64 {
    LIVE_BYTES.load(Relaxed)
}

/// The live heap's allocations now.
pub fn heap_allocs() -> u64 {
    LIVE_ALLOCS.load(Relaxed)
}

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

    #[test]
    fn the_counting_allocator_holds_every_live_byte() {
        // Other tests allocate and free on their threads, so only a lower bound holds: while this test holds a block,
        // the live heap is at least that block (alloc, alloc_zeroed and realloc each counted).
        const MB: usize = 1 << 20;
        let mut v: Vec<u8> = Vec::with_capacity(16 * MB);
        v.push(1);
        assert!(heap_bytes() >= 16 * MB as u64 && heap_allocs() >= 1, "{} bytes in {} allocations", heap_bytes(), heap_allocs());
        v.reserve_exact(24 * MB); // realloc to 24 MB + 1
        assert!(heap_bytes() >= 24 * MB as u64);
        let z = vec![0u8; 20 * MB]; // alloc_zeroed
        assert!(heap_bytes() >= 44 * MB as u64 && z[MB] == 0);
        drop((v, z));
    }
}
