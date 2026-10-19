//! What C4's memory log reports per structure (M1 C4-2; the spec is research/m1-c4-plateau-spec-2026-10-19.md §3): its
//! entries and an estimate of its heap bytes, capacity × size_of. That is a lower bound: a hash table's control bytes are
//! counted, but not the allocator's rounding or a value's own heap unless the owner adds it. Read-only: a report changes
//! no structure.

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::mem::size_of;

/// One structure's line in the log: its name, its entries (keys, or items where the name says so) and bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mem {
    pub name: &'static str,
    pub entries: usize,
    pub bytes: usize,
}

impl Mem {
    pub fn new(name: &'static str, entries: usize, bytes: usize) -> Self {
        Mem { name, entries, bytes }
    }
}

pub fn vec<T>(v: &Vec<T>) -> usize {
    v.capacity() * size_of::<T>()
}

pub fn deque<T>(v: &VecDeque<T>) -> usize {
    v.capacity() * size_of::<T>()
}

/// A hash map's table: its capacity × (the entry + one control byte).
pub fn map<K, V, S>(m: &HashMap<K, V, S>) -> usize {
    m.capacity() * (size_of::<(K, V)>() + 1)
}

pub fn set<K, S>(s: &HashSet<K, S>) -> usize {
    s.capacity() * (size_of::<K>() + 1)
}

/// A B-tree's entries (its nodes' spare room and pointers are not counted).
pub fn btree<K, V>(m: &BTreeMap<K, V>) -> usize {
    m.len() * size_of::<(K, V)>()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_estimates_are_capacity_times_size() {
        let v: Vec<u64> = Vec::with_capacity(10);
        assert_eq!(vec(&v), 80);
        let d: VecDeque<u32> = VecDeque::with_capacity(4);
        assert!(deque(&d) >= 16);
        let mut m: HashMap<u32, u64> = HashMap::new();
        assert_eq!(map(&m), 0);
        m.insert(1, 2);
        assert_eq!(map(&m), m.capacity() * 17);
        let s: HashSet<u64> = (0..5).collect();
        assert_eq!(set(&s), s.capacity() * 9);
        let b: BTreeMap<u64, u64> = (0..3).map(|i| (i, i)).collect();
        assert_eq!(btree(&b), 48);
    }
}
