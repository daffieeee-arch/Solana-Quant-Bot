//! Exact field-level callback payload snapshot from upstream `firehose.rs`.

use solana_hash::Hash;
pub use solana_runtime::bank::KeyedRewardsAndNumPartitions;
use solana_signature::Signature;
use solana_transaction::versioned::VersionedTransaction;
use solana_transaction_status::TransactionStatusMeta;

/// Firehose transaction payload passed to callbacks at the pinned revision.
#[derive(Debug, Clone)]
pub struct TransactionData {
    pub slot: u64,
    pub transaction_slot_index: usize,
    pub signature: Signature,
    pub message_hash: Hash,
    pub is_vote: bool,
    pub transaction_status_meta: TransactionStatusMeta,
    pub transaction: VersionedTransaction,
}

/// Block-level data streamed to callbacks at the pinned revision.
#[derive(Debug)]
pub enum BlockData {
    Block {
        parent_slot: u64,
        parent_blockhash: Hash,
        slot: u64,
        blockhash: Hash,
        rewards: KeyedRewardsAndNumPartitions,
        block_time: Option<i64>,
        block_height: Option<u64>,
        executed_transaction_count: u64,
        entry_count: u64,
    },
    PossibleLeaderSkipped {
        slot: u64,
    },
}

impl BlockData {
    #[must_use]
    #[inline(always)]
    pub const fn slot(&self) -> u64 {
        match self {
            Self::Block { slot, .. } | Self::PossibleLeaderSkipped { slot } => *slot,
        }
    }

    #[must_use]
    #[inline(always)]
    pub const fn was_skipped(&self) -> bool {
        matches!(self, Self::PossibleLeaderSkipped { .. })
    }

    #[must_use]
    #[inline(always)]
    pub const fn block_time(&self) -> Option<i64> {
        match self {
            Self::Block { block_time, .. } => *block_time,
            Self::PossibleLeaderSkipped { .. } => None,
        }
    }
}
