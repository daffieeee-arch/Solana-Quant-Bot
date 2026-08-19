use serde_json::{Value, json};
use solana_address::Address;
use thiserror::Error;

const EVENT_CPI_TAG: [u8; 8] = [0xe4, 0x45, 0xa5, 0x2e, 0x51, 0xcb, 0x9a, 0x1d];
const CREATE_EVENT_DISCRIMINATOR: [u8; 8] = [0x1b, 0x72, 0xa9, 0x4d, 0xde, 0xeb, 0x63, 0x76];
const TRADE_EVENT_DISCRIMINATOR: [u8; 8] = [0xbd, 0xdb, 0x7f, 0xd3, 0x4e, 0xe6, 0x61, 0xee];
const MAX_EVENT_BYTES: usize = 4_096;
const MAX_EVENT_TEXT_BYTES: usize = 1_024;
const MAX_SHAREHOLDERS: u32 = 128;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SilverEventError {
    #[error("invalid_event_hex")]
    InvalidHex,
    #[error("invalid_event_payload")]
    InvalidPayload,
    #[error("invalid_event_cpi_tag")]
    InvalidCpiTag,
    #[error("unknown_event_discriminator")]
    UnknownDiscriminator,
    #[error("invalid_bool")]
    InvalidBool,
    #[error("invalid_event_text")]
    InvalidText,
    #[error("too_many_shareholders")]
    TooManyShareholders,
    #[error("duplicate_shareholder")]
    DuplicateShareholder,
    #[error("trailing_event_bytes")]
    TrailingBytes,
}

struct Cursor<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> Cursor<'a> {
    const fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, offset: 0 }
    }

    fn take(&mut self, length: usize) -> Result<&'a [u8], SilverEventError> {
        let end = self
            .offset
            .checked_add(length)
            .ok_or(SilverEventError::InvalidPayload)?;
        let value = self
            .bytes
            .get(self.offset..end)
            .ok_or(SilverEventError::InvalidPayload)?;
        self.offset = end;
        Ok(value)
    }

    fn array<const N: usize>(&mut self) -> Result<[u8; N], SilverEventError> {
        self.take(N)?
            .try_into()
            .map_err(|_| SilverEventError::InvalidPayload)
    }

    fn u16(&mut self) -> Result<u16, SilverEventError> {
        Ok(u16::from_le_bytes(self.array()?))
    }

    fn u32(&mut self) -> Result<u32, SilverEventError> {
        Ok(u32::from_le_bytes(self.array()?))
    }

    fn u64_string(&mut self) -> Result<String, SilverEventError> {
        Ok(u64::from_le_bytes(self.array()?).to_string())
    }

    fn i64_string(&mut self) -> Result<String, SilverEventError> {
        Ok(i64::from_le_bytes(self.array()?).to_string())
    }

    fn boolean(&mut self) -> Result<bool, SilverEventError> {
        match self.take(1)?.first() {
            Some(0) => Ok(false),
            Some(1) => Ok(true),
            _ => Err(SilverEventError::InvalidBool),
        }
    }

    fn public_key(&mut self) -> Result<String, SilverEventError> {
        Ok(Address::from(self.array::<32>()?).to_string())
    }

    fn text(&mut self) -> Result<String, SilverEventError> {
        let length = usize::try_from(self.u32()?).map_err(|_| SilverEventError::InvalidText)?;
        if length > MAX_EVENT_TEXT_BYTES {
            return Err(SilverEventError::InvalidText);
        }
        let bytes = self
            .take(length)
            .map_err(|_| SilverEventError::InvalidText)?;
        String::from_utf8(bytes.to_vec()).map_err(|_| SilverEventError::InvalidText)
    }

    fn assert_end(&self) -> Result<(), SilverEventError> {
        if self.offset == self.bytes.len() {
            Ok(())
        } else {
            Err(SilverEventError::TrailingBytes)
        }
    }
}

fn decode_hex(value: &str) -> Result<Vec<u8>, SilverEventError> {
    if value.is_empty() || value.len() > MAX_EVENT_BYTES * 2 || !value.len().is_multiple_of(2) {
        return Err(SilverEventError::InvalidHex);
    }
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(value.len() / 2);
    for pair in bytes.chunks_exact(2) {
        let high = hex_nibble(pair[0]).ok_or(SilverEventError::InvalidHex)?;
        let low = hex_nibble(pair[1]).ok_or(SilverEventError::InvalidHex)?;
        decoded.push((high << 4) | low);
    }
    Ok(decoded)
}

const fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn decode_create(cursor: &mut Cursor<'_>) -> Result<Value, SilverEventError> {
    let value = json!({
        "eventType": "create",
        "name": cursor.text()?,
        "symbol": cursor.text()?,
        "uri": cursor.text()?,
        "mint": cursor.public_key()?,
        "bondingCurve": cursor.public_key()?,
        "user": cursor.public_key()?,
        "creator": cursor.public_key()?,
        "timestamp": cursor.i64_string()?,
        "virtualTokenReserves": cursor.u64_string()?,
        "virtualSolReserves": cursor.u64_string()?,
        "realTokenReserves": cursor.u64_string()?,
        "tokenTotalSupply": cursor.u64_string()?,
        "tokenProgram": cursor.public_key()?,
        "isMayhemMode": cursor.boolean()?,
        "isCashbackEnabled": cursor.boolean()?,
        "quoteMint": cursor.public_key()?,
        "virtualQuoteReserves": cursor.u64_string()?,
    });
    cursor.assert_end()?;
    Ok(value)
}

#[allow(clippy::too_many_lines)]
fn decode_trade(cursor: &mut Cursor<'_>) -> Result<Value, SilverEventError> {
    let mint = cursor.public_key()?;
    let sol_amount = cursor.u64_string()?;
    let token_amount = cursor.u64_string()?;
    let is_buy = cursor.boolean()?;
    let user = cursor.public_key()?;
    let timestamp = cursor.i64_string()?;
    let virtual_sol_reserves = cursor.u64_string()?;
    let virtual_token_reserves = cursor.u64_string()?;
    let real_sol_reserves = cursor.u64_string()?;
    let real_token_reserves = cursor.u64_string()?;
    let fee_recipient = cursor.public_key()?;
    let fee_basis_points = cursor.u64_string()?;
    let fee = cursor.u64_string()?;
    let creator = cursor.public_key()?;
    let creator_fee_basis_points = cursor.u64_string()?;
    let creator_fee = cursor.u64_string()?;
    let track_volume = cursor.boolean()?;
    let total_unclaimed_tokens = cursor.u64_string()?;
    let total_claimed_tokens = cursor.u64_string()?;
    let current_sol_volume = cursor.u64_string()?;
    let last_update_timestamp = cursor.i64_string()?;
    let ix_name = cursor.text()?;
    let mayhem_mode = cursor.boolean()?;
    let cashback_fee_basis_points = cursor.u64_string()?;
    let cashback = cursor.u64_string()?;
    let buyback_fee_basis_points = cursor.u64_string()?;
    let buyback_fee = cursor.u64_string()?;
    let shareholder_count = cursor.u32()?;
    if shareholder_count > MAX_SHAREHOLDERS {
        return Err(SilverEventError::TooManyShareholders);
    }
    let mut shareholders = Vec::with_capacity(usize::try_from(shareholder_count).unwrap_or(0));
    let mut shareholder_addresses = std::collections::HashSet::new();
    for _ in 0..shareholder_count {
        let address = cursor.public_key()?;
        if !shareholder_addresses.insert(address.clone()) {
            return Err(SilverEventError::DuplicateShareholder);
        }
        shareholders.push(json!({ "address": address, "shareBps": cursor.u16()? }));
    }
    let value = json!({
        "eventType": "trade",
        "mint": mint,
        "solAmount": sol_amount,
        "tokenAmount": token_amount,
        "isBuy": is_buy,
        "user": user,
        "timestamp": timestamp,
        "virtualSolReserves": virtual_sol_reserves,
        "virtualTokenReserves": virtual_token_reserves,
        "realSolReserves": real_sol_reserves,
        "realTokenReserves": real_token_reserves,
        "feeRecipient": fee_recipient,
        "feeBasisPoints": fee_basis_points,
        "fee": fee,
        "creator": creator,
        "creatorFeeBasisPoints": creator_fee_basis_points,
        "creatorFee": creator_fee,
        "trackVolume": track_volume,
        "totalUnclaimedTokens": total_unclaimed_tokens,
        "totalClaimedTokens": total_claimed_tokens,
        "currentSolVolume": current_sol_volume,
        "lastUpdateTimestamp": last_update_timestamp,
        "ixName": ix_name,
        "mayhemMode": mayhem_mode,
        "cashbackFeeBasisPoints": cashback_fee_basis_points,
        "cashback": cashback,
        "buybackFeeBasisPoints": buyback_fee_basis_points,
        "buybackFee": buyback_fee,
        "shareholders": shareholders,
        "quoteMint": cursor.public_key()?,
        "quoteAmount": cursor.u64_string()?,
        "virtualQuoteReserves": cursor.u64_string()?,
        "realQuoteReserves": cursor.u64_string()?,
    });
    cursor.assert_end()?;
    Ok(value)
}

/// Decodes one canonical Anchor CPI Pump `CreateEvent` or `TradeEvent` from hex.
///
/// # Errors
///
/// Returns a fail-closed error for non-canonical hex, tag/discriminator drift, malformed Borsh,
/// duplicate shareholders, or trailing bytes.
pub fn decode_pump_silver_event_hex(value: &str) -> Result<Value, SilverEventError> {
    let bytes = decode_hex(value)?;
    let mut cursor = Cursor::new(&bytes);
    if cursor.array::<8>()? != EVENT_CPI_TAG {
        return Err(SilverEventError::InvalidCpiTag);
    }
    match cursor.array::<8>()? {
        CREATE_EVENT_DISCRIMINATOR => decode_create(&mut cursor),
        TRADE_EVENT_DISCRIMINATOR => decode_trade(&mut cursor),
        _ => Err(SilverEventError::UnknownDiscriminator),
    }
}
