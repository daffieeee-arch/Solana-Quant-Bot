#![cfg(target_os = "linux")]

use std::{io, mem::MaybeUninit, thread};

use sha2::{Digest as _, Sha256};

const SEMAPHORE_MODE: libc::c_int = 0o600;
const REGISTRY_MUTEX_INDEX: usize = 0;
const REGISTRY_SCHEMA_INDEX: usize = 1;
const REGISTRY_SCHEMA_MAGIC: libc::c_ushort = 23_117;
const SLOT_COUNT: usize = 4;
const SLOT_WIDTH: usize = 34;
const SLOT_ALLOCATED_OFFSET: usize = 0;
const SLOT_ACTIVE_OFFSET: usize = 1;
const SLOT_FINGERPRINT_OFFSET: usize = 2;
const SEMAPHORE_COUNT: usize = 2 + SLOT_COUNT * SLOT_WIDTH;
const INITIALIZATION_SPINS: usize = 1_024;
const REGISTRY_RETRIES: usize = 16;

/// A process-owned Linux System V semaphore namespace lock.
///
/// One 32-bit System V key addresses a small collision registry. Each registry
/// slot stores the complete effective-UID-plus-namespace SHA-256 fingerprint and
/// has its own `SEM_UNDO` active-writer semaphore. Distinct full fingerprints can
/// therefore share a `key_t` without aliasing, while equal fingerprints contend
/// on the same slot. Normal destruction removes the registry only when no other
/// slot is active; process death releases active ownership without cleanup code.
#[derive(Debug)]
pub struct NamespaceLock {
    semaphore_id: libc::c_int,
    slot: usize,
}

impl NamespaceLock {
    /// Acquires the lock for `namespace` and the current effective Linux UID.
    ///
    /// # Errors
    ///
    /// Returns [`io::ErrorKind::WouldBlock`] when this exact full namespace is
    /// already owned, or an explicit operating-system/registry error otherwise.
    pub fn acquire(namespace: &[u8]) -> io::Result<Self> {
        if namespace.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "namespace must not be empty",
            ));
        }
        Self::acquire_fingerprint(namespace_fingerprint(namespace))
    }

    // Private boundary: production callers always hash the effective UID and
    // namespace above. Only the closed test probe below supplies fixture digests.
    fn acquire_fingerprint(fingerprint: [u8; 32]) -> io::Result<Self> {
        let key = primary_key(&fingerprint);
        for _ in 0..REGISTRY_RETRIES {
            let (semaphore_id, created) = get_or_create_registry(key)?;
            if created {
                if let Err(error) = initialize_registry(semaphore_id) {
                    remove_registry(semaphore_id);
                    if is_removed_error(&error) {
                        continue;
                    }
                    return Err(error);
                }
            } else if let Err(error) = wait_for_initialized_registry(semaphore_id) {
                if is_removed_error(&error) {
                    continue;
                }
                return Err(error);
            }
            if let Err(error) = lock_registry(semaphore_id) {
                if is_removed_error(&error) {
                    continue;
                }
                return Err(error);
            }
            let slot = match claim_fingerprint_slot(semaphore_id, &fingerprint) {
                Ok(slot) => slot,
                Err(error) => {
                    let _ = release_semaphore(semaphore_id, REGISTRY_MUTEX_INDEX);
                    if is_removed_error(&error) {
                        continue;
                    }
                    return Err(error);
                }
            };
            if let Err(error) = release_semaphore(semaphore_id, REGISTRY_MUTEX_INDEX) {
                let _ = release_semaphore(semaphore_id, slot_active_index(slot));
                if is_removed_error(&error) {
                    continue;
                }
                return Err(error);
            }
            return Ok(Self { semaphore_id, slot });
        }
        Err(io::Error::new(
            io::ErrorKind::ResourceBusy,
            "semaphore registry changed too often during acquisition",
        ))
    }
}

impl Drop for NamespaceLock {
    fn drop(&mut self) {
        if lock_registry(self.semaphore_id).is_err() {
            return;
        }
        let Ok(values) = registry_values(self.semaphore_id) else {
            let _ = release_semaphore(self.semaphore_id, REGISTRY_MUTEX_INDEX);
            return;
        };
        let another_slot_is_active = (0..SLOT_COUNT).any(|slot| {
            slot != self.slot && values[slot_active_index(slot)] != libc::c_ushort::default()
        });
        if !another_slot_is_active {
            // Remove while both the registry mutex and this slot remain held. A
            // waiter then observes EIDRM and retries against a fresh registry.
            if remove_registry(self.semaphore_id) {
                return;
            }
        }
        let _ = release_semaphore(self.semaphore_id, slot_active_index(self.slot));
        let _ = release_semaphore(self.semaphore_id, REGISTRY_MUTEX_INDEX);
    }
}

fn namespace_fingerprint(namespace: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    // SAFETY: geteuid has no preconditions and does not dereference pointers.
    let effective_uid = unsafe { libc::geteuid() };
    hasher.update(effective_uid.to_be_bytes());
    hasher.update([0]);
    hasher.update(namespace);
    hasher.finalize().into()
}

fn primary_key(fingerprint: &[u8; 32]) -> libc::key_t {
    let mut key = i32::from_be_bytes(fingerprint[..4].try_into().expect("four-byte prefix"));
    if key == libc::IPC_PRIVATE {
        key = i32::from_be_bytes(fingerprint[4..8].try_into().expect("four-byte fallback"));
        if key == libc::IPC_PRIVATE {
            key = 1;
        }
    }
    key
}

fn get_or_create_registry(key: libc::key_t) -> io::Result<(libc::c_int, bool)> {
    let count = libc::c_int::try_from(SEMAPHORE_COUNT).expect("bounded semaphore count");
    // SAFETY: semget receives a value key, a bounded positive count, and Linux flags.
    let created = unsafe {
        libc::semget(
            key,
            count,
            libc::IPC_CREAT | libc::IPC_EXCL | SEMAPHORE_MODE,
        )
    };
    if created >= 0 {
        return Ok((created, true));
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() != Some(libc::EEXIST) {
        return Err(error);
    }
    // SAFETY: semget requests an existing set with at least the declared count.
    let existing = unsafe { libc::semget(key, count, 0) };
    if existing < 0 {
        let open_error = io::Error::last_os_error();
        if open_error.raw_os_error() == Some(libc::EINVAL) {
            return Err(incompatible_registry_error());
        }
        return Err(open_error);
    }
    require_registry_shape(existing)?;
    Ok((existing, false))
}

fn require_registry_shape(semaphore_id: libc::c_int) -> io::Result<()> {
    let mut metadata = MaybeUninit::<libc::semid_ds>::zeroed();
    // SAFETY: IPC_STAT initializes the valid semid_ds pointer for this semaphore set.
    let result = unsafe { libc::semctl(semaphore_id, 0, libc::IPC_STAT, metadata.as_mut_ptr()) };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful IPC_STAT initialized the complete semid_ds value.
    let metadata = unsafe { metadata.assume_init() };
    if usize::try_from(metadata.sem_nsems).ok() != Some(SEMAPHORE_COUNT) {
        return Err(incompatible_registry_error());
    }
    Ok(())
}

fn initialize_registry(semaphore_id: libc::c_int) -> io::Result<()> {
    let mut values = vec![libc::c_ushort::default(); SEMAPHORE_COUNT];
    values[REGISTRY_SCHEMA_INDEX] = REGISTRY_SCHEMA_MAGIC;
    // SAFETY: the set was created with exactly SEMAPHORE_COUNT entries and SETALL
    // reads that many c_ushort values atomically before any SEM_UNDO acquisition.
    let result = unsafe { libc::semctl(semaphore_id, 0, libc::SETALL, values.as_ptr()) };
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn wait_for_initialized_registry(semaphore_id: libc::c_int) -> io::Result<()> {
    for _ in 0..INITIALIZATION_SPINS {
        let values = registry_values(semaphore_id)?;
        if values[REGISTRY_SCHEMA_INDEX] == REGISTRY_SCHEMA_MAGIC {
            return Ok(());
        }
        if values[REGISTRY_SCHEMA_INDEX] != libc::c_ushort::default() {
            return Err(incompatible_registry_error());
        }
        thread::yield_now();
    }
    Err(io::Error::new(
        io::ErrorKind::ResourceBusy,
        "System V semaphore registry remained uninitialized",
    ))
}

fn registry_values(semaphore_id: libc::c_int) -> io::Result<Vec<libc::c_ushort>> {
    require_registry_shape(semaphore_id)?;
    let mut values = vec![libc::c_ushort::default(); SEMAPHORE_COUNT];
    // SAFETY: GETALL writes exactly the verified number of c_ushort values.
    let result = unsafe { libc::semctl(semaphore_id, 0, libc::GETALL, values.as_mut_ptr()) };
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(values)
    }
}

fn lock_registry(semaphore_id: libc::c_int) -> io::Result<()> {
    acquire_zero_to_one(semaphore_id, REGISTRY_MUTEX_INDEX, false)
}

fn claim_fingerprint_slot(semaphore_id: libc::c_int, fingerprint: &[u8; 32]) -> io::Result<usize> {
    let values = registry_values(semaphore_id)?;
    if values[REGISTRY_SCHEMA_INDEX] != REGISTRY_SCHEMA_MAGIC {
        return Err(incompatible_registry_error());
    }
    if let Some(slot) = (0..SLOT_COUNT).find(|slot| slot_matches(&values, *slot, fingerprint)) {
        acquire_zero_to_one(semaphore_id, slot_active_index(slot), true)?;
        return Ok(slot);
    }
    let slot = (0..SLOT_COUNT)
        .find(|slot| values[slot_allocated_index(*slot)] == libc::c_ushort::default())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::AddrInUse,
                "semaphore namespace collision registry is full",
            )
        })?;
    for (offset, byte) in fingerprint.iter().copied().enumerate() {
        set_value(
            semaphore_id,
            slot_fingerprint_index(slot, offset),
            libc::c_int::from(byte),
        )?;
    }
    set_value(semaphore_id, slot_allocated_index(slot), 1)?;
    acquire_zero_to_one(semaphore_id, slot_active_index(slot), true)?;
    Ok(slot)
}

fn slot_matches(values: &[libc::c_ushort], slot: usize, fingerprint: &[u8; 32]) -> bool {
    values[slot_allocated_index(slot)] == 1
        && fingerprint
            .iter()
            .copied()
            .enumerate()
            .all(|(offset, byte)| {
                values[slot_fingerprint_index(slot, offset)] == libc::c_ushort::from(byte)
            })
}

fn acquire_zero_to_one(
    semaphore_id: libc::c_int,
    semaphore_index: usize,
    nonblocking: bool,
) -> io::Result<()> {
    let nowait = if nonblocking { libc::IPC_NOWAIT } else { 0 };
    let mut operations = [
        libc::sembuf {
            sem_num: u16::try_from(semaphore_index).expect("bounded semaphore index"),
            sem_op: 0,
            sem_flg: libc::c_short::try_from(nowait).expect("Linux flags fit c_short"),
        },
        libc::sembuf {
            sem_num: u16::try_from(semaphore_index).expect("bounded semaphore index"),
            sem_op: 1,
            sem_flg: libc::c_short::try_from(nowait | libc::SEM_UNDO)
                .expect("Linux flags fit c_short"),
        },
    ];
    // SAFETY: the verified set contains the selected index and both operations are valid.
    let result = unsafe { libc::semop(semaphore_id, operations.as_mut_ptr(), operations.len()) };
    if result == 0 {
        return Ok(());
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() == Some(libc::EAGAIN) {
        Err(io::Error::new(
            io::ErrorKind::WouldBlock,
            "reducer namespace already has an active writer",
        ))
    } else {
        Err(error)
    }
}

fn set_value(
    semaphore_id: libc::c_int,
    semaphore_index: usize,
    value: libc::c_int,
) -> io::Result<()> {
    // SAFETY: SETVAL receives a verified semaphore index and a value below SEMVMX.
    let result = unsafe {
        libc::semctl(
            semaphore_id,
            libc::c_int::try_from(semaphore_index).expect("bounded semaphore index"),
            libc::SETVAL,
            value,
        )
    };
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn release_semaphore(semaphore_id: libc::c_int, semaphore_index: usize) -> io::Result<()> {
    let mut operation = libc::sembuf {
        sem_num: u16::try_from(semaphore_index).expect("bounded semaphore index"),
        sem_op: -1,
        sem_flg: libc::c_short::try_from(libc::IPC_NOWAIT | libc::SEM_UNDO)
            .expect("Linux flags fit c_short"),
    };
    // SAFETY: the selected semaphore is held by this process when release is requested.
    let result = unsafe { libc::semop(semaphore_id, &raw mut operation, 1) };
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn slot_base(slot: usize) -> usize {
    2 + slot * SLOT_WIDTH
}

fn slot_allocated_index(slot: usize) -> usize {
    slot_base(slot) + SLOT_ALLOCATED_OFFSET
}

fn slot_active_index(slot: usize) -> usize {
    slot_base(slot) + SLOT_ACTIVE_OFFSET
}

fn slot_fingerprint_index(slot: usize, offset: usize) -> usize {
    slot_base(slot) + SLOT_FINGERPRINT_OFFSET + offset
}

fn incompatible_registry_error() -> io::Error {
    io::Error::new(
        io::ErrorKind::AddrInUse,
        "System V key is occupied by an incompatible semaphore registry",
    )
}

fn is_removed_error(error: &io::Error) -> bool {
    matches!(error.raw_os_error(), Some(libc::EIDRM | libc::EINVAL))
}

fn remove_registry(semaphore_id: libc::c_int) -> bool {
    // SAFETY: IPC_RMID ignores the variadic union argument and removes this semaphore set.
    unsafe { libc::semctl(semaphore_id, 0, libc::IPC_RMID) == 0 }
}

/// An exact-shape, deliberately uninitialized registry used by acquisition tests.
#[cfg(feature = "test-support")]
#[derive(Debug)]
pub struct UninitializedRegistryReservation {
    semaphore_id: libc::c_int,
    key: libc::key_t,
}

#[cfg(feature = "test-support")]
impl UninitializedRegistryReservation {
    /// Returns whether the reserved semaphore ID is still bound to its original key.
    #[must_use]
    pub fn is_present(&self) -> bool {
        let count = libc::c_int::try_from(SEMAPHORE_COUNT).expect("bounded semaphore count");
        // SAFETY: semget performs a read-only lookup of the exact test key and shape.
        (unsafe { libc::semget(self.key, count, 0) }) == self.semaphore_id
    }
}

#[cfg(feature = "test-support")]
impl Drop for UninitializedRegistryReservation {
    fn drop(&mut self) {
        remove_registry(self.semaphore_id);
    }
}

/// Reserves an exact-shape uninitialized registry for fail-closed tests.
///
/// # Errors
///
/// Returns the operating-system error from `semget` or invalid-input for an empty namespace.
#[cfg(feature = "test-support")]
pub fn reserve_uninitialized_registry_for_test(
    namespace: &[u8],
) -> io::Result<UninitializedRegistryReservation> {
    if namespace.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "namespace must not be empty",
        ));
    }
    let key = primary_key(&namespace_fingerprint(namespace));
    let count = libc::c_int::try_from(SEMAPHORE_COUNT).expect("bounded semaphore count");
    // SAFETY: semget creates the production registry's exact bounded shape for this test.
    let semaphore_id = unsafe {
        libc::semget(
            key,
            count,
            libc::IPC_CREAT | libc::IPC_EXCL | SEMAPHORE_MODE,
        )
    };
    if semaphore_id < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(UninitializedRegistryReservation { semaphore_id, key })
}

/// Marks the current process non-dumpable for Linux acquisition regression tests.
///
/// # Errors
///
/// Returns the operating-system error reported by `prctl`.
#[cfg(feature = "test-support")]
pub fn make_current_process_nondumpable() -> io::Result<()> {
    // SAFETY: PR_SET_DUMPABLE takes one integer flag followed by ignored zero arguments.
    let result = unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0) };
    if result == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

/// Exercises a deterministic primary-key collision using actual Linux locks.
///
/// The first identity is the production effective-UID/namespace digest. The
/// second is a synthetic full digest differing in its last byte, leaving the
/// primary-key prefix and fallback unchanged. This tests collision handling,
/// not SHA-256 collision discovery. No arbitrary key override or lock escapes
/// this closed probe, which is absent without `test-support`.
///
/// # Panics
///
/// Panics on a failed collision invariant or OS error. The caller must supply
/// its own unique test namespace. Only the exclusively created registry is
/// reserved for cleanup; an existing registry fails before any mutation.
#[cfg(feature = "test-support")]
pub fn assert_primary_key_collision_isolated_for_test(namespace: &[u8]) {
    let reservation = reserve_uninitialized_registry_for_test(namespace).unwrap();
    initialize_registry(reservation.semaphore_id).unwrap();
    let first_identity = namespace_fingerprint(namespace);
    let mut second_identity = first_identity;
    second_identity[31] ^= 1;
    assert_ne!(first_identity, second_identity);
    assert_eq!(primary_key(&first_identity), primary_key(&second_identity));

    let first = NamespaceLock::acquire(namespace).unwrap();
    let second = NamespaceLock::acquire_fingerprint(second_identity).unwrap();
    assert_eq!(first.semaphore_id, reservation.semaphore_id);
    assert_eq!(first.semaphore_id, second.semaphore_id);
    assert_ne!(first.slot, second.slot);
    let values = registry_values(reservation.semaphore_id).unwrap();
    assert!(slot_matches(&values, first.slot, &first_identity));
    assert!(slot_matches(&values, second.slot, &second_identity));
    assert_eq!(values[slot_active_index(first.slot)], 1);
    assert_eq!(values[slot_active_index(second.slot)], 1);
    assert_eq!(
        NamespaceLock::acquire(namespace).unwrap_err().kind(),
        io::ErrorKind::WouldBlock
    );
    assert_eq!(
        NamespaceLock::acquire_fingerprint(second_identity)
            .unwrap_err()
            .kind(),
        io::ErrorKind::WouldBlock
    );

    let first_slot = first.slot;
    drop(first);
    assert!(reservation.is_present());
    assert_eq!(
        NamespaceLock::acquire_fingerprint(second_identity)
            .unwrap_err()
            .kind(),
        io::ErrorKind::WouldBlock
    );
    let replacement = NamespaceLock::acquire(namespace).unwrap();
    assert_eq!(replacement.semaphore_id, second.semaphore_id);
    assert_eq!(replacement.slot, first_slot);
    drop(second);
    let second_again = NamespaceLock::acquire_fingerprint(second_identity).unwrap();
    assert_eq!(second_again.semaphore_id, replacement.semaphore_id);
    drop(second_again);
    drop(replacement);
    assert!(
        !reservation.is_present(),
        "own registry must be removed after final release"
    );
}
