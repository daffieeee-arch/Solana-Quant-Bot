//! Bounded offline scheduling only. Source bytes and archival order never change.
use crate::invalid;
use std::io;

#[derive(Clone, Copy, Debug)]
pub enum DeliveryOrder {
    Canonical,
    Reverse,
    OddEven,
}

impl DeliveryOrder {
    /// # Errors
    /// Only the three preregistered schedules are supported; no arbitrary indices.
    pub fn parse(name: &str) -> io::Result<Self> {
        match name {
            "canonical" => Ok(Self::Canonical),
            "reverse" => Ok(Self::Reverse),
            "odd-even" => Ok(Self::OddEven),
            _ => Err(invalid("DELIVERY_ORDER")),
        }
    }

    #[must_use]
    pub fn name(self) -> &'static str {
        match self {
            Self::Canonical => "canonical",
            Self::Reverse => "reverse",
            Self::OddEven => "odd-even",
        }
    }

    pub(crate) fn ranks(self, count: usize) -> Vec<usize> {
        match self {
            Self::Canonical => (0..count).collect(),
            Self::Reverse => (0..count).rev().collect(),
            Self::OddEven => (1..count).step_by(2).chain((0..count).step_by(2)).collect(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schedules_are_complete_bijections_including_empty_and_odd_sizes() {
        for n in [0, 1, 2, 3, 7, 3224] {
            for order in [
                DeliveryOrder::Canonical,
                DeliveryOrder::Reverse,
                DeliveryOrder::OddEven,
            ] {
                let mut ranks = order.ranks(n);
                ranks.sort_unstable();
                assert_eq!(ranks, (0..n).collect::<Vec<_>>());
            }
        }
        assert_eq!(DeliveryOrder::OddEven.ranks(7), [1, 3, 5, 0, 2, 4, 6]);
        assert!(DeliveryOrder::parse("random").is_err());
    }
}
