use std::{
    collections::HashSet,
    io::{Cursor, Error, ErrorKind, Read},
};

use borsh::BorshDeserialize;
use serde::Serialize;

use crate::registry::{
    BUY_DISCRIMINATOR, EVENT_IX_TAG_LE, EvidenceStatus, ProtocolRegistryEntry,
    TRADE_EVENT_DISCRIMINATOR, ValidatedProtocolCandidate,
};

const BUY_INSTRUCTION_BYTES: usize = 8 + 8 + 8 + 1;
const MAX_EVENT_BYTES: usize = 4_096;
const MAX_IX_NAME_BYTES: usize = 32;
const MAX_SHAREHOLDERS: u32 = 128;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BuyInstruction {
    pub amount: u64,
    pub max_sol_cost: u64,
    pub track_volume: bool,
}

#[derive(BorshDeserialize)]
struct BuyInstructionWire {
    amount: u64,
    max_sol_cost: u64,
    track_volume: OptionBool,
}

#[derive(BorshDeserialize)]
struct OptionBool(bool);

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Shareholder {
    pub address: [u8; 32],
    pub share_bps: u16,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TradeEvent {
    pub mint: [u8; 32],
    pub sol_amount: u64,
    pub token_amount: u64,
    pub is_buy: bool,
    pub user: [u8; 32],
    pub timestamp: i64,
    pub virtual_sol_reserves: u64,
    pub virtual_token_reserves: u64,
    pub real_sol_reserves: u64,
    pub real_token_reserves: u64,
    pub fee_recipient: [u8; 32],
    pub fee_basis_points: u64,
    pub fee: u64,
    pub creator: [u8; 32],
    pub creator_fee_basis_points: u64,
    pub creator_fee: u64,
    pub track_volume: bool,
    pub total_unclaimed_tokens: u64,
    pub total_claimed_tokens: u64,
    pub current_sol_volume: u64,
    pub last_update_timestamp: i64,
    pub ix_name: String,
    pub mayhem_mode: bool,
    pub cashback_fee_basis_points: u64,
    pub cashback: u64,
    pub buyback_fee_basis_points: u64,
    pub buyback_fee: u64,
    pub shareholders: Vec<Shareholder>,
    pub quote_mint: [u8; 32],
    pub quote_amount: u64,
    pub virtual_quote_reserves: u64,
    pub real_quote_reserves: u64,
}

impl BorshDeserialize for TradeEvent {
    fn deserialize_reader<R: Read>(reader: &mut R) -> std::io::Result<Self> {
        let mint = <[u8; 32]>::deserialize_reader(reader)?;
        let sol_amount = u64::deserialize_reader(reader)?;
        let token_amount = u64::deserialize_reader(reader)?;
        let is_buy = bool::deserialize_reader(reader)?;
        let user = <[u8; 32]>::deserialize_reader(reader)?;
        let timestamp = i64::deserialize_reader(reader)?;
        let virtual_sol_reserves = u64::deserialize_reader(reader)?;
        let virtual_token_reserves = u64::deserialize_reader(reader)?;
        let real_sol_reserves = u64::deserialize_reader(reader)?;
        let real_token_reserves = u64::deserialize_reader(reader)?;
        let fee_recipient = <[u8; 32]>::deserialize_reader(reader)?;
        let fee_basis_points = u64::deserialize_reader(reader)?;
        let fee = u64::deserialize_reader(reader)?;
        let creator = <[u8; 32]>::deserialize_reader(reader)?;
        let creator_fee_basis_points = u64::deserialize_reader(reader)?;
        let creator_fee = u64::deserialize_reader(reader)?;
        let track_volume = bool::deserialize_reader(reader)?;
        let total_unclaimed_tokens = u64::deserialize_reader(reader)?;
        let total_claimed_tokens = u64::deserialize_reader(reader)?;
        let current_sol_volume = u64::deserialize_reader(reader)?;
        let last_update_timestamp = i64::deserialize_reader(reader)?;
        let ix_name = read_bounded_string(reader)?;
        let mayhem_mode = bool::deserialize_reader(reader)?;
        let cashback_fee_basis_points = u64::deserialize_reader(reader)?;
        let cashback = u64::deserialize_reader(reader)?;
        let buyback_fee_basis_points = u64::deserialize_reader(reader)?;
        let buyback_fee = u64::deserialize_reader(reader)?;
        let shareholder_count = u32::deserialize_reader(reader)?;
        if shareholder_count > MAX_SHAREHOLDERS {
            return Err(Error::new(ErrorKind::InvalidData, "too many shareholders"));
        }
        let mut shareholders = Vec::with_capacity(shareholder_count as usize);
        let mut shareholder_addresses = HashSet::with_capacity(shareholder_count as usize);
        for _ in 0..shareholder_count {
            let address = <[u8; 32]>::deserialize_reader(reader)?;
            if !shareholder_addresses.insert(address) {
                return Err(Error::new(ErrorKind::InvalidData, "duplicate shareholder"));
            }
            shareholders.push(Shareholder {
                address,
                share_bps: u16::deserialize_reader(reader)?,
            });
        }
        Ok(Self {
            mint,
            sol_amount,
            token_amount,
            is_buy,
            user,
            timestamp,
            virtual_sol_reserves,
            virtual_token_reserves,
            real_sol_reserves,
            real_token_reserves,
            fee_recipient,
            fee_basis_points,
            fee,
            creator,
            creator_fee_basis_points,
            creator_fee,
            track_volume,
            total_unclaimed_tokens,
            total_claimed_tokens,
            current_sol_volume,
            last_update_timestamp,
            ix_name,
            mayhem_mode,
            cashback_fee_basis_points,
            cashback,
            buyback_fee_basis_points,
            buyback_fee,
            shareholders,
            quote_mint: <[u8; 32]>::deserialize_reader(reader)?,
            quote_amount: u64::deserialize_reader(reader)?,
            virtual_quote_reserves: u64::deserialize_reader(reader)?,
            real_quote_reserves: u64::deserialize_reader(reader)?,
        })
    }
}

fn read_bounded_string<R: Read>(reader: &mut R) -> std::io::Result<String> {
    let length = u32::deserialize_reader(reader)?;
    let length = usize::try_from(length)
        .map_err(|_| Error::new(ErrorKind::InvalidData, "string length overflow"))?;
    if length > MAX_IX_NAME_BYTES {
        return Err(Error::new(ErrorKind::InvalidData, "ix_name exceeds bound"));
    }
    let mut bytes = vec![0_u8; length];
    reader.read_exact(&mut bytes)?;
    String::from_utf8(bytes).map_err(|_| Error::new(ErrorKind::InvalidData, "invalid ix_name"))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum QuarantineReason {
    TruncatedDiscriminator,
    WrongDiscriminator,
    TruncatedPayload,
    UnexpectedTrailingBytes,
    UnsupportedSchema,
    UnsupportedVariant,
    ReferenceDisagreement,
    DuplicateShareholder,
    UnknownQuoteMintEvidence,
    UnknownDecimalEvidence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Quarantine {
    pub reason: QuarantineReason,
    pub detail: String,
}

impl Quarantine {
    fn new(reason: QuarantineReason, detail: impl Into<String>) -> Self {
        Self {
            reason,
            detail: detail.into(),
        }
    }
}

/// Decode the exact selected `buy` instruction wire schema.
///
/// # Errors
///
/// Returns a typed quarantine when identity, bounds, Borsh schema, or exact
/// payload exhaustion does not match the single registered candidate.
pub fn decode_buy_instruction(
    candidate: &ValidatedProtocolCandidate,
    data: &[u8],
) -> Result<BuyInstruction, Quarantine> {
    validate_selected_candidate(candidate, "buy")?;
    probe_buy_instruction_layout(data)
}

/// Inspect the single pinned layout without manufacturing candidate selection.
/// This is `STRUCTURAL_LAYOUT_ONLY`: no program/invocation, account, activation,
/// economic or Silver admission is established by a successful parse.
/// # Errors
/// Preserves the exact bounded parser's discriminator, layout and exhaustion errors.
pub fn probe_buy_instruction_layout(data: &[u8]) -> Result<BuyInstruction, Quarantine> {
    validate_discriminator(data, BUY_DISCRIMINATOR, "buy instruction")?;
    if data.len() < BUY_INSTRUCTION_BYTES {
        return Err(Quarantine::new(
            QuarantineReason::TruncatedPayload,
            "buy payload shorter than the selected fixed schema",
        ));
    }
    if data.len() > BUY_INSTRUCTION_BYTES {
        return Err(Quarantine::new(
            QuarantineReason::UnexpectedTrailingBytes,
            "buy payload contains bytes outside the selected schema",
        ));
    }
    let wire =
        BuyInstructionWire::try_from_slice(&data[8..]).map_err(|error| map_borsh_error(&error))?;
    Ok(BuyInstruction {
        amount: wire.amount,
        max_sol_cost: wire.max_sol_cost,
        track_volume: wire.track_volume.0,
    })
}

/// Decode the selected `buy` `TradeEvent` inside the retained Anchor CPI envelope.
///
/// # Errors
///
/// Returns a typed quarantine for framing/discriminator drift, bounded Borsh
/// failures, unsupported event variants, duplicate shareholders, or trailing bytes.
pub fn decode_trade_event_cpi(
    candidate: &ValidatedProtocolCandidate,
    data: &[u8],
) -> Result<TradeEvent, Quarantine> {
    validate_selected_candidate(candidate, "TradeEvent[is_buy=true,ix_name=buy]")?;
    probe_trade_event_cpi_layout(data)
}

/// Structural inspection of the pinned buy-event CPI layout only; not an
/// independently selected/observed-compatible candidate or committed state.
/// # Errors
/// Preserves the existing discriminator, Borsh, variant and exhaustion checks.
pub fn probe_trade_event_cpi_layout(data: &[u8]) -> Result<TradeEvent, Quarantine> {
    validate_discriminator(data, EVENT_IX_TAG_LE, "Anchor event instruction tag")?;
    let event = data.get(8..).ok_or_else(|| {
        Quarantine::new(
            QuarantineReason::TruncatedDiscriminator,
            "missing TradeEvent discriminator",
        )
    })?;
    validate_discriminator(event, TRADE_EVENT_DISCRIMINATOR, "TradeEvent")?;
    if data.len() > MAX_EVENT_BYTES {
        return Err(Quarantine::new(
            QuarantineReason::UnsupportedSchema,
            "TradeEvent exceeds the bounded candidate size",
        ));
    }
    let payload = &event[8..];
    let mut reader = Cursor::new(payload);
    let decoded =
        TradeEvent::deserialize_reader(&mut reader).map_err(|error| map_borsh_error(&error))?;
    if reader.position() != payload.len() as u64 {
        return Err(Quarantine::new(
            QuarantineReason::UnexpectedTrailingBytes,
            "TradeEvent has bytes outside the selected schema",
        ));
    }
    if !decoded.is_buy || decoded.ix_name != "buy" {
        return Err(Quarantine::new(
            QuarantineReason::UnsupportedVariant,
            "selected candidate accepts only is_buy=true and ix_name=buy",
        ));
    }
    Ok(decoded)
}

fn validate_selected_candidate(
    candidate: &ValidatedProtocolCandidate,
    selected_surface: &str,
) -> Result<(), Quarantine> {
    let entry = candidate.entry();
    let matches = match selected_surface {
        "buy" => {
            entry.instruction_name == "buy"
                && entry.instruction_discriminator_hex == hex::encode(BUY_DISCRIMINATOR)
        }
        "TradeEvent[is_buy=true,ix_name=buy]" => {
            entry.event_name == selected_surface
                && entry.event_discriminator_hex == hex::encode(TRADE_EVENT_DISCRIMINATOR)
        }
        _ => false,
    };
    if matches {
        Ok(())
    } else {
        Err(Quarantine::new(
            QuarantineReason::UnsupportedSchema,
            "validated registry candidate does not authorize the selected surface",
        ))
    }
}

fn validate_discriminator(data: &[u8], expected: [u8; 8], label: &str) -> Result<(), Quarantine> {
    let observed = data.get(..8).ok_or_else(|| {
        Quarantine::new(
            QuarantineReason::TruncatedDiscriminator,
            format!("{label} discriminator is truncated"),
        )
    })?;
    if observed != expected {
        return Err(Quarantine::new(
            QuarantineReason::WrongDiscriminator,
            format!("{label} discriminator does not match the selected candidate"),
        ));
    }
    Ok(())
}

fn map_borsh_error(error: &std::io::Error) -> Quarantine {
    let detail = error.to_string();
    if error.kind() == ErrorKind::UnexpectedEof
        || detail.contains("Unexpected length of input")
        || detail.contains("failed to fill whole buffer")
    {
        Quarantine::new(QuarantineReason::TruncatedPayload, detail)
    } else if detail == "duplicate shareholder" {
        Quarantine::new(QuarantineReason::DuplicateShareholder, detail)
    } else {
        Quarantine::new(QuarantineReason::UnsupportedSchema, detail)
    }
}

/// Compare primary output with the sealed structural-reference output.
///
/// # Errors
///
/// Returns `REFERENCE_DISAGREEMENT` when the two exact values differ.
pub fn validate_reference_agreement<T: Eq>(
    primary: &T,
    structural_reference: &T,
) -> Result<(), Quarantine> {
    if primary == structural_reference {
        Ok(())
    } else {
        Err(Quarantine::new(
            QuarantineReason::ReferenceDisagreement,
            "primary decoder and sealed structural reference disagree",
        ))
    }
}

/// Gate normalized economic use on explicit quote-mint and decimal evidence.
///
/// # Errors
///
/// Returns a typed quarantine while quote-mint or decimal evidence is unknown.
pub fn require_economic_identity(registry: &ProtocolRegistryEntry) -> Result<(), Quarantine> {
    if registry.quote_asset_kind.value.is_none()
        || !has_observed_status(&registry.quote_asset_kind.status)
        || registry.quote_mint.value.is_none()
        || !has_observed_status(&registry.quote_mint.status)
    {
        return Err(Quarantine::new(
            QuarantineReason::UnknownQuoteMintEvidence,
            "selected source does not prove an observed exact quote asset and mint",
        ));
    }
    if !has_observed_canonical_u8(&registry.quote_decimals)
        || !has_observed_canonical_u8(&registry.base_decimals)
    {
        return Err(Quarantine::new(
            QuarantineReason::UnknownDecimalEvidence,
            "selected source does not prove observed canonical base and quote decimal domains",
        ));
    }
    Ok(())
}

fn has_observed_value(value: &crate::registry::EvidenceValue) -> bool {
    value.value.as_ref().is_some_and(|text| !text.is_empty()) && has_observed_status(&value.status)
}

fn has_observed_status(status: &EvidenceStatus) -> bool {
    matches!(
        status,
        EvidenceStatus::ObservedCompatible | EvidenceStatus::ProvenAtSlotRange
    )
}

fn has_observed_canonical_u8(value: &crate::registry::EvidenceValue) -> bool {
    has_observed_value(value)
        && value.value.as_ref().is_some_and(|text| {
            text.parse::<u8>()
                .is_ok_and(|parsed| parsed.to_string() == *text)
        })
}
