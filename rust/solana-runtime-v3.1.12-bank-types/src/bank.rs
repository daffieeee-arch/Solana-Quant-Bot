use solana_address::Address as Pubkey;
use solana_reward_info::RewardInfo;

/// Exact callback-visible reward bundle from `solana-runtime` v3.1.12.
#[derive(Debug, PartialEq)]
pub struct KeyedRewardsAndNumPartitions {
    pub keyed_rewards: Vec<(Pubkey, RewardInfo)>,
    pub num_partitions: Option<u64>,
}

impl KeyedRewardsAndNumPartitions {
    #[must_use]
    pub fn should_record(&self) -> bool {
        !self.keyed_rewards.is_empty() || self.num_partitions.is_some()
    }
}
