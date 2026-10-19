//! The process's memory: from /proc/self/status (Linux; None elsewhere) for the progress lines and the baseline of the
//! read (state `lab_live_measure`), and the live heap from the counting allocator (M1 C4-2, `--mem-log`).

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

/// The global allocator: `System`, counting the live Rust heap's bytes (as requested, so the allocator's own rounding and
/// retained pages are RSS's part) and its live allocations, with two relaxed atomics per call. C code allocates outside it:
/// the bundled SQLite (`sqlite_bytes` in the memory log, its own counter) and libzstd's contexts are only in RSS.
pub struct Counting {
    bytes: AtomicU64,
    allocs: AtomicU64,
}

impl Counting {
    pub const fn new() -> Self {
        Counting { bytes: AtomicU64::new(0), allocs: AtomicU64::new(0) }
    }

    pub fn bytes(&self) -> u64 {
        self.bytes.load(Relaxed)
    }

    pub fn allocs(&self) -> u64 {
        self.allocs.load(Relaxed)
    }
}

/// lab-live's allocator (the binary and its tests).
#[global_allocator]
pub static ALLOC: Counting = Counting::new();

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        let p = unsafe { System.alloc(l) };
        if !p.is_null() {
            self.bytes.fetch_add(l.size() as u64, Relaxed);
            self.allocs.fetch_add(1, Relaxed);
        }
        p
    }

    unsafe fn alloc_zeroed(&self, l: Layout) -> *mut u8 {
        let p = unsafe { System.alloc_zeroed(l) };
        if !p.is_null() {
            self.bytes.fetch_add(l.size() as u64, Relaxed);
            self.allocs.fetch_add(1, Relaxed);
        }
        p
    }

    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) };
        self.bytes.fetch_sub(l.size() as u64, Relaxed);
        self.allocs.fetch_sub(1, Relaxed);
    }

    unsafe fn realloc(&self, p: *mut u8, l: Layout, new_size: usize) -> *mut u8 {
        let q = unsafe { System.realloc(p, l, new_size) };
        if !q.is_null() {
            if new_size >= l.size() {
                self.bytes.fetch_add((new_size - l.size()) as u64, Relaxed);
            } else {
                self.bytes.fetch_sub((l.size() - new_size) as u64, Relaxed);
            }
        }
        q
    }
}

/// The live Rust heap's bytes now.
pub fn heap_bytes() -> u64 {
    ALLOC.bytes()
}

/// The live Rust heap's allocations now.
pub fn heap_allocs() -> u64 {
    ALLOC.allocs()
}

/// The bundled SQLite's own outstanding memory (`sqlite3_memory_used`; its MEMSTATUS is on by default): the journal's
/// page cache and statements, which the counting allocator does not see.
pub fn sqlite_bytes() -> i64 {
    // SAFETY: a plain read of SQLite's global counter; no pointer, no state change.
    unsafe { rusqlite::ffi::sqlite3_memory_used() }
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
    fn the_counting_allocator_counts_every_path_exactly() {
        // On its own instance (the global one sees every thread's allocations): alloc, a growing and a shrinking realloc,
        // alloc_zeroed and dealloc, each exact; back to 0 at the end.
        let c = Counting::new();
        let l = |n: usize| Layout::from_size_align(n, 8).unwrap();
        unsafe {
            let p = c.alloc(l(100));
            assert!(!p.is_null());
            assert_eq!((c.bytes(), c.allocs()), (100, 1));
            let p = c.realloc(p, l(100), 300);
            assert_eq!((c.bytes(), c.allocs()), (300, 1));
            let p = c.realloc(p, l(300), 50);
            assert_eq!((c.bytes(), c.allocs()), (50, 1));
            let z = c.alloc_zeroed(l(64));
            assert!(std::slice::from_raw_parts(z, 64).iter().all(|&b| b == 0));
            assert_eq!((c.bytes(), c.allocs()), (114, 2));
            c.dealloc(p, l(50));
            c.dealloc(z, l(64));
        }
        assert_eq!((c.bytes(), c.allocs()), (0, 0));
        // the global allocator is this one: while this test holds a block, the live heap is at least that block
        let v: Vec<u8> = Vec::with_capacity(8 << 20);
        assert!(heap_bytes() >= 8 << 20 && heap_allocs() >= 1, "{} bytes in {} allocations", heap_bytes(), heap_allocs());
        drop(v);
        assert!(sqlite_bytes() >= 0);
    }
}
