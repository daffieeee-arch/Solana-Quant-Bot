use std::{
    collections::{BTreeMap, BTreeSet},
    str::FromStr,
};

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use solana_address::Address;
use solana_signature::Signature;

const STATE_HASH_DOMAIN: &str = "PUMP_SILVER_STATE_CONTRACT_1";
const SNAPSHOT_HASH_DOMAIN: &str = "PUMP_SILVER_STATE_SNAPSHOT_SET_1";
const RERUN_HASH_DOMAIN: &str = "PUMP_SILVER_STATE_RERUN_1";
const EVENT_BINDING_HASH_DOMAIN: &str = "PUMP_SILVER_STATE_EVENT_BINDING_HASH_1";
const PUMP_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const WSOL: &str = "So11111111111111111111111111111111111111112";
const TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN_2022_PROGRAM: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const ASSOCIATED_TOKEN_PROGRAM: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const SYNTHETIC_ACCOUNT_RENT_RESERVE: u64 = 1_000_000;

const CURVE_DISCRIMINATOR: &str = "17b7f83760d8ac60";
const CURVE_DISCRIMINATOR_BYTES: [u8; 8] = [0x17, 0xb7, 0xf8, 0x37, 0x60, 0xd8, 0xac, 0x60];
const PUMP_IDL_SHA256: &str = "b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49";
const PUMP_DOCS_COMMIT: &str = "9c82f61cb711b044a17f770ab8ce9f9bdf78f333";
const GOLDEN_EPOCH_CID: &str = "bafkreiacv4rclo6minmaq75znwrqj5oxg7l5cim3ami4snfdptltigowka";
const GOLDEN_CAR_SHA256: &str = "ec5ce3b235ac4e5774656598a2e548e11873c9603fcc324f7f4ca1d0077bb326";
const GOLDEN_CAR_SIZE: &str = "4096";
const GOLDEN_INVENTORY_SHA256: &str =
    "1e188b728002793e3b64d6a7072c370d4c0f90c657ce10140f276a908b35b5b8";
const GOLDEN_INVENTORY_SIZE: &str = "512";
const GOLDEN_INVENTORY_ENTRIES: &str = "1";
const GOLDEN_SLOT_START: &str = "361000001";
const GOLDEN_SLOT_END: &str = "361000002";
const GOLDEN_PARSER_GIT_SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const GOLDEN_REDUCER_GIT_SHA: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const GOLDEN_ADAPTER_GIT_SHA: &str = "cccccccccccccccccccccccccccccccccccccccc";
const GOLDEN_BINDING_HASHES: &[&str] = &[
    "aa47cfd82dd2c5448a0a8a6758610dd95f069508adb3e17f02f992bef532d4f5",
    "617a8185d49cb9fd64c7cd8ae36e86e031b02dc284727f69434c523053abddb2",
    "d557dc91de3e4ae331b3d10d90736f38a9ea95b06cb503058f87654064e14468",
    "260579be7cf3aaadff2e56a96d9448c3bc9ba1470934e810a68af853a369c141",
    "a96d064df76785a0ba2f41cc0bbecc4a2def78066c876507957c81c50caddf39",
    "b39d08d9471c7bdfbaba755be8af3c4db27ac39d14cfea04827870a2223bf82a",
    "f9e936233b985dbcef96b50ade4a6750dfa0397d8339831d440270f5d97b79ff",
    "bc239d639c4e40a3446d4da99457ea06c4711189ec8174bbe6d3fe0dd189ae4d",
];
const GOLDEN_PILOT_BUDGET: &[(&str, &str)] = &[
    ("maxBytesRead", "1048576"),
    ("maxBytesWritten", "1048576"),
    ("maxSlots", "1"),
    ("maxCallbacks", "16"),
    ("maxRuntimeMillis", "60000"),
    ("maxStorageBytes", "2097152"),
];

const EVENT_BINDING_KEYS: &[&str] = &[
    "schemaVersion",
    "eventKey",
    "variant",
    "kind",
    "mint",
    "bondingCurve",
    "tokenProgramId",
    "quoteTokenProgramId",
    "baseBondingCurveTokenAccount",
    "quoteBondingCurveTokenAccount",
    "quoteMint",
    "signature",
    "slot",
    "transactionIndex",
    "executionStatus",
    "parentInstructionLocation",
    "parentInstructionIndex",
    "parentStackHeight",
    "eventInstructionLocation",
    "eventParentInstructionIndex",
    "eventInstructionIndex",
    "eventStackHeight",
    "sourcePhase6aSha256",
    "creator",
    "mayhemMode",
    "isCashbackCoin",
    "tokenDecimals",
    "quoteDecimals",
    "tokenAmount",
    "quoteAmount",
    "virtualTokenReserves",
    "virtualQuoteReserves",
    "realTokenReserves",
    "realQuoteReserves",
];
const EVIDENCE_KEYS: &[&str] = &[
    "schemaVersion",
    "eventKey",
    "registry",
    "snapshots",
    "provenance",
    "coverage",
    "pilotBudget",
];
const REGISTRY_KEYS: &[&str] = &[
    "schemaVersion",
    "approved",
    "researchReady",
    "evidenceClass",
    "realActivationSlotRange",
    "activationSlotEvidence",
    "officialDocsCommit",
    "pumpIdlSha256",
    "pumpProgramId",
    "bondingCurveLayout",
    "bondingCurveDiscriminatorHex",
    "legacyTokenProgramId",
    "token2022ProgramId",
    "supportedToken2022Extensions",
    "fixtureBindingSha256",
];
const SNAPSHOT_KEYS: &[&str] = &[
    "schemaVersion",
    "accountRole",
    "boundary",
    "source",
    "evidenceClass",
    "signature",
    "slot",
    "transactionIndex",
    "parentInstructionLocation",
    "parentInstructionIndex",
    "parentStackHeight",
    "eventInstructionLocation",
    "eventParentInstructionIndex",
    "eventInstructionIndex",
    "eventStackHeight",
    "eventKey",
    "accountPubkey",
    "ownerProgramId",
    "lamports",
    "executable",
    "dataHex",
    "dataSha256",
    "writeOrdinal",
    "transactionWideBalanceOnly",
    "stateAuthority",
    "token2022Extensions",
];
const PROVENANCE_KEYS: &[&str] = &[
    "schemaVersion",
    "evidenceClass",
    "epochCid",
    "carSha256",
    "carFileSizeBytes",
    "slotInventorySha256",
    "slotInventorySizeBytes",
    "slotInventoryEntryCount",
    "slotRange",
    "parserGitSha",
    "reducerGitSha",
    "adapterGitSha",
    "sourceSha256",
    "eventBindingSha256",
    "outputSha256",
    "cumulativeSourceBytes",
    "deterministicRerunHash",
];
const COVERAGE_KEYS: &[&str] = &[
    "expectedSlots",
    "observedSlots",
    "skippedSlots",
    "quarantinedSlots",
    "expectedCallbacks",
    "observedCallbacks",
    "skippedCallbacks",
    "quarantinedCallbacks",
    "quarantineByReason",
];
const BUDGET_KEYS: &[&str] = &[
    "maxBytesRead",
    "maxBytesWritten",
    "maxSlots",
    "maxCallbacks",
    "maxRuntimeMillis",
    "maxStorageBytes",
];
const SLOT_RANGE_KEYS: &[&str] = &["startInclusive", "endExclusive"];

#[derive(Clone)]
struct EventBinding {
    event_key: String,
    kind: String,
    mint: String,
    bonding_curve: String,
    token_program_id: String,
    quote_token_program_id: Option<String>,
    base_token_account: String,
    quote_token_account: Option<String>,
    quote_mint: String,
    signature: String,
    slot: String,
    transaction_index: u64,
    execution_status: String,
    parent_instruction_location: String,
    parent_instruction_index: u64,
    parent_stack_height: Option<u64>,
    event_instruction_location: String,
    event_parent_instruction_index: Option<u64>,
    event_instruction_index: u64,
    event_stack_height: Option<u64>,
    source_phase6a_sha256: String,
    creator: String,
    mayhem_mode: bool,
    is_cashback_coin: bool,
    token_decimals: u8,
    token_amount: u64,
    quote_amount: u64,
    virtual_token_reserves: String,
    virtual_quote_reserves: String,
    real_token_reserves: String,
    real_quote_reserves: String,
}

#[derive(Clone)]
struct BondingCurve {
    virtual_token_reserves: u64,
    virtual_quote_reserves: u64,
    real_token_reserves: u64,
    real_quote_reserves: u64,
    token_total_supply: u64,
    complete: bool,
    creator: String,
    is_mayhem_mode: bool,
    is_cashback_coin: bool,
    quote_mint: String,
}

#[derive(Clone)]
struct Mint {
    token_program_id: String,
    authority: Option<String>,
    supply: u64,
    decimals: u8,
    initialized: bool,
    freeze_authority: Option<String>,
}

#[derive(Clone)]
struct TokenAccount {
    token_program_id: String,
    mint: String,
    owner: String,
    amount: u64,
    delegate: Option<String>,
    state: &'static str,
    is_native_reserve: Option<u64>,
    delegated_amount: u64,
    close_authority: Option<String>,
}

impl BondingCurve {
    fn json(&self) -> Value {
        json!({
            "virtualTokenReserves": self.virtual_token_reserves.to_string(),
            "virtualQuoteReserves": self.virtual_quote_reserves.to_string(),
            "realTokenReserves": self.real_token_reserves.to_string(),
            "realQuoteReserves": self.real_quote_reserves.to_string(),
            "tokenTotalSupply": self.token_total_supply.to_string(),
            "complete": self.complete,
            "creator": self.creator,
            "isMayhemMode": self.is_mayhem_mode,
            "isCashbackCoin": self.is_cashback_coin,
            "quoteMint": self.quote_mint,
        })
    }
}

impl Mint {
    fn json(&self) -> Value {
        json!({
            "tokenProgramId": self.token_program_id,
            "mintAuthority": self.authority,
            "supply": self.supply.to_string(),
            "decimals": self.decimals,
            "initialized": self.initialized,
            "freezeAuthority": self.freeze_authority,
            "token2022Extensions": [],
        })
    }
}

impl TokenAccount {
    fn json(&self) -> Value {
        json!({
            "tokenProgramId": self.token_program_id,
            "mint": self.mint,
            "owner": self.owner,
            "amount": self.amount.to_string(),
            "delegate": self.delegate,
            "state": self.state,
            "isNativeReserve": self.is_native_reserve.map(|value| value.to_string()),
            "delegatedAmount": self.delegated_amount.to_string(),
            "closeAuthority": self.close_authority,
            "token2022Extensions": [],
        })
    }
}

fn exact_keys(map: &Map<String, Value>, expected: &[&str]) -> bool {
    map.len() == expected.len() && expected.iter().all(|key| map.contains_key(*key))
}

fn value_str<'a>(map: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    map.get(key)?.as_str()
}

fn canonical_u64(value: &str) -> Option<u64> {
    if value.is_empty()
        || value.len() > 20
        || (value.len() > 1 && value.starts_with('0'))
        || !value.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    value.parse().ok()
}

fn canonical_u128(value: &str) -> Option<u128> {
    if value.is_empty()
        || value.len() > 39
        || (value.len() > 1 && value.starts_with('0'))
        || !value.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    value.parse().ok()
}

fn valid_hex(value: &str, bytes: usize) -> bool {
    value.len() == bytes.saturating_mul(2)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn decode_hex(value: &str, maximum_bytes: usize) -> Option<Vec<u8>> {
    if value.is_empty()
        || value.len() > maximum_bytes.saturating_mul(2)
        || !value.len().is_multiple_of(2)
    {
        return None;
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Some((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?))
        .collect()
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn canonical_json(value: &Value, output: &mut String) -> bool {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) if value.is_i64() || value.is_u64() => {
            output.push_str(&value.to_string());
        }
        Value::String(value) => match serde_json::to_string(value) {
            Ok(encoded) => output.push_str(&encoded),
            Err(_) => return false,
        },
        Value::Array(values) => {
            output.push('[');
            for (index, entry) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                if !canonical_json(entry, output) {
                    return false;
                }
            }
            output.push(']');
        }
        Value::Object(map) => {
            output.push('{');
            for (index, key) in map.keys().collect::<BTreeSet<_>>().into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                match serde_json::to_string(key) {
                    Ok(encoded) => output.push_str(&encoded),
                    Err(_) => return false,
                }
                output.push(':');
                if !canonical_json(&map[key], output) {
                    return false;
                }
            }
            output.push('}');
        }
        Value::Number(_) => return false,
    }
    true
}

fn domain_hash(domain: &str, value: &Value) -> Option<String> {
    let mut canonical = String::new();
    if !canonical_json(value, &mut canonical) {
        return None;
    }
    let mut hasher = Sha256::new();
    hasher.update(domain.as_bytes());
    hasher.update([0]);
    hasher.update(canonical.as_bytes());
    Some(format!("{:x}", hasher.finalize()))
}

fn quarantine(
    reason: &str,
    event_key: Option<&str>,
    source_hash: Option<&str>,
    binding_hash: Option<&str>,
) -> Value {
    let mut unsigned = json!({
        "schemaVersion": "PUMP_SILVER_STATE_CONTRACT_1",
        "status": "QUARANTINED",
        "approved": false,
        "researchReady": false,
        "pilotEligible": false,
        "eventKey": event_key,
        "sourcePhase6aSha256": source_hash,
        "eventBindingSha256": binding_hash,
        "evidenceClass": "SYNTHETIC_TEST_ONLY",
        "state": null,
        "provenance": null,
        "coverage": null,
        "quarantineReasons": [reason],
    });
    let hash = domain_hash(STATE_HASH_DOMAIN, &unsigned).unwrap_or_default();
    if let Some(map) = unsigned.as_object_mut() {
        map.insert("canonicalHash".to_owned(), Value::String(hash));
    }
    unsigned
}

fn nullable_u32(value: &Value) -> Result<Option<u64>, ()> {
    if value.is_null() {
        return Ok(None);
    }
    let parsed = value.as_u64().ok_or(())?;
    u32::try_from(parsed).map(|_| Some(parsed)).map_err(|_| ())
}

fn nullable_address(value: &Value) -> Result<Option<String>, ()> {
    if value.is_null() {
        return Ok(None);
    }
    let address = value.as_str().ok_or(())?;
    Address::from_str(address).map_err(|_| ())?;
    Ok(Some(address.to_owned()))
}

fn derive_associated_token(owner: &Address, token_program: &str, mint: &str) -> Option<String> {
    let token_program = Address::from_str(token_program).ok()?;
    let mint = Address::from_str(mint).ok()?;
    let associated_program = Address::from_str(ASSOCIATED_TOKEN_PROGRAM).ok()?;
    Some(
        Address::find_program_address(
            &[owner.as_ref(), token_program.as_ref(), mint.as_ref()],
            &associated_program,
        )
        .0
        .to_string(),
    )
}

fn canonical_event_key(map: &Map<String, Value>) -> Option<String> {
    fn coordinate(value: &Value) -> Option<String> {
        match value {
            Value::Null => Some("null".to_owned()),
            Value::String(value) => Some(value.clone()),
            Value::Number(value) => value.as_u64().map(|number| number.to_string()),
            _ => None,
        }
    }
    let fields = [
        map.get("slot")?,
        map.get("transactionIndex")?,
        map.get("parentInstructionLocation")?,
        map.get("parentInstructionIndex")?,
        map.get("parentStackHeight")?,
        map.get("eventInstructionLocation")?,
        map.get("eventParentInstructionIndex")?,
        map.get("eventInstructionIndex")?,
        map.get("eventStackHeight")?,
        map.get("variant")?,
    ];
    fields
        .iter()
        .map(|field| coordinate(field))
        .collect::<Option<Vec<_>>>()
        .map(|parts| parts.join(":"))
}

#[allow(clippy::too_many_lines)]
fn parse_event_binding(value: &Value) -> Option<EventBinding> {
    let map = value.as_object()?;
    if !exact_keys(map, EVENT_BINDING_KEYS)
        || value_str(map, "schemaVersion")? != "PUMP_SILVER_STATE_EVENT_BINDING_1"
    {
        return None;
    }
    let variant = value_str(map, "variant")?;
    let kind = value_str(map, "kind")?;
    if !matches!(
        variant,
        "buy" | "sell" | "buy_v2" | "sell_v2" | "buy_exact_sol_in" | "buy_exact_quote_in_v2"
    ) || !matches!(kind, "buy" | "sell")
        || (variant.starts_with("sell") != (kind == "sell"))
    {
        return None;
    }
    let mint = value_str(map, "mint")?;
    let bonding_curve = value_str(map, "bondingCurve")?;
    let pump = Address::from_str(PUMP_PROGRAM_ID).ok()?;
    let mint_address = Address::from_str(mint).ok()?;
    let derived_curve =
        Address::find_program_address(&[b"bonding-curve", mint_address.as_ref()], &pump)
            .0
            .to_string();
    if derived_curve != bonding_curve {
        return None;
    }
    let bonding_curve_address = Address::from_str(bonding_curve).ok()?;
    let token_program_id = value_str(map, "tokenProgramId")?;
    if !matches!(token_program_id, TOKEN_PROGRAM | TOKEN_2022_PROGRAM) {
        return None;
    }
    let quote_token_program_id = nullable_address(map.get("quoteTokenProgramId")?).ok()?;
    let quote_token_account = nullable_address(map.get("quoteBondingCurveTokenAccount")?).ok()?;
    let v2 = variant.ends_with("_v2");
    if v2 != quote_token_program_id.is_some() || v2 != quote_token_account.is_some() {
        return None;
    }
    if quote_token_program_id
        .as_deref()
        .is_some_and(|program| program != TOKEN_PROGRAM)
    {
        return None;
    }
    let base_token_account = value_str(map, "baseBondingCurveTokenAccount")?;
    if derive_associated_token(&bonding_curve_address, token_program_id, mint).as_deref()
        != Some(base_token_account)
    {
        return None;
    }
    let quote_mint = value_str(map, "quoteMint")?;
    if v2
        && derive_associated_token(
            &bonding_curve_address,
            quote_token_program_id.as_deref()?,
            quote_mint,
        )
        .as_deref()
            != quote_token_account.as_deref()
    {
        return None;
    }
    let signature = value_str(map, "signature")?;
    if quote_mint != WSOL || Signature::from_str(signature).is_err() {
        return None;
    }
    let transaction_index = map.get("transactionIndex")?.as_u64()?;
    let parent_instruction_index = map.get("parentInstructionIndex")?.as_u64()?;
    let event_instruction_index = map.get("eventInstructionIndex")?.as_u64()?;
    if transaction_index > u64::from(u32::MAX)
        || parent_instruction_index > u64::from(u32::MAX)
        || event_instruction_index > u64::from(u32::MAX)
        || value_str(map, "parentInstructionLocation")? != "top_level"
        || value_str(map, "eventInstructionLocation")? != "inner"
        || !valid_hex(value_str(map, "sourcePhase6aSha256")?, 32)
    {
        return None;
    }
    let parent_stack_height = nullable_u32(map.get("parentStackHeight")?).ok()?;
    let event_parent_instruction_index =
        nullable_u32(map.get("eventParentInstructionIndex")?).ok()?;
    let event_stack_height = nullable_u32(map.get("eventStackHeight")?).ok()?;
    if parent_stack_height.is_some()
        || event_parent_instruction_index != Some(parent_instruction_index)
        || !matches!(event_stack_height, Some(2..=9))
    {
        return None;
    }
    let token_decimals = u8::try_from(map.get("tokenDecimals")?.as_u64()?).ok()?;
    let quote_decimals = u8::try_from(map.get("quoteDecimals")?.as_u64()?).ok()?;
    if quote_decimals != 9 {
        return None;
    }
    let event_key = value_str(map, "eventKey")?;
    if canonical_event_key(map).as_deref() != Some(event_key) {
        return None;
    }
    let execution_status = value_str(map, "executionStatus")?;
    if !matches!(execution_status, "succeeded" | "failed") {
        return None;
    }
    let creator = value_str(map, "creator")?;
    Address::from_str(creator).ok()?;
    let mayhem_mode = map.get("mayhemMode")?.as_bool()?;
    let is_cashback_coin = map.get("isCashbackCoin")?.as_bool()?;
    if is_cashback_coin {
        return None;
    }
    Some(EventBinding {
        event_key: event_key.to_owned(),
        kind: kind.to_owned(),
        mint: mint.to_owned(),
        bonding_curve: bonding_curve.to_owned(),
        token_program_id: token_program_id.to_owned(),
        quote_token_program_id,
        base_token_account: base_token_account.to_owned(),
        quote_token_account,
        quote_mint: WSOL.to_owned(),
        signature: signature.to_owned(),
        slot: canonical_u64(value_str(map, "slot")?)?.to_string(),
        transaction_index,
        execution_status: execution_status.to_owned(),
        parent_instruction_location: "top_level".to_owned(),
        parent_instruction_index,
        parent_stack_height,
        event_instruction_location: "inner".to_owned(),
        event_parent_instruction_index,
        event_instruction_index,
        event_stack_height,
        source_phase6a_sha256: value_str(map, "sourcePhase6aSha256")?.to_owned(),
        creator: creator.to_owned(),
        mayhem_mode,
        is_cashback_coin,
        token_decimals,
        token_amount: canonical_u64(value_str(map, "tokenAmount")?)?,
        quote_amount: canonical_u64(value_str(map, "quoteAmount")?)?,
        virtual_token_reserves: canonical_u64(value_str(map, "virtualTokenReserves")?)?.to_string(),
        virtual_quote_reserves: canonical_u64(value_str(map, "virtualQuoteReserves")?)?.to_string(),
        real_token_reserves: canonical_u64(value_str(map, "realTokenReserves")?)?.to_string(),
        real_quote_reserves: canonical_u64(value_str(map, "realQuoteReserves")?)?.to_string(),
    })
}

fn read_u32(bytes: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        bytes.get(offset..offset.checked_add(4)?)?.try_into().ok()?,
    ))
}

fn read_u64(bytes: &[u8], offset: usize) -> Option<u64> {
    Some(u64::from_le_bytes(
        bytes.get(offset..offset.checked_add(8)?)?.try_into().ok()?,
    ))
}

fn read_address(bytes: &[u8], offset: usize) -> Option<String> {
    let raw: [u8; 32] = bytes
        .get(offset..offset.checked_add(32)?)?
        .try_into()
        .ok()?;
    Some(Address::from(raw).to_string())
}

fn decode_curve(bytes: &[u8]) -> Option<BondingCurve> {
    if bytes.len() != 115 || bytes[..8] != CURVE_DISCRIMINATOR_BYTES {
        return None;
    }
    let complete = *bytes.get(48)?;
    let mayhem = *bytes.get(81)?;
    let cashback = *bytes.get(82)?;
    if complete > 1 || mayhem > 1 || cashback > 1 {
        return None;
    }
    Some(BondingCurve {
        virtual_token_reserves: read_u64(bytes, 8)?,
        virtual_quote_reserves: read_u64(bytes, 16)?,
        real_token_reserves: read_u64(bytes, 24)?,
        real_quote_reserves: read_u64(bytes, 32)?,
        token_total_supply: read_u64(bytes, 40)?,
        complete: complete == 1,
        creator: read_address(bytes, 49)?,
        is_mayhem_mode: mayhem == 1,
        is_cashback_coin: cashback == 1,
        quote_mint: read_address(bytes, 83)?,
    })
}

enum COptionAddress {
    Absent,
    Present(String),
}

impl COptionAddress {
    fn into_option(self) -> Option<String> {
        match self {
            Self::Absent => None,
            Self::Present(address) => Some(address),
        }
    }
}

fn decode_coption_address(bytes: &[u8], offset: usize) -> Option<COptionAddress> {
    let tag = read_u32(bytes, offset)?;
    let address_bytes = bytes.get(offset.checked_add(4)?..offset.checked_add(36)?)?;
    match tag {
        0 if address_bytes.iter().all(|byte| *byte == 0) => Some(COptionAddress::Absent),
        1 => Some(COptionAddress::Present(read_address(
            bytes,
            offset.checked_add(4)?,
        )?)),
        _ => None,
    }
}

fn decode_coption_u64(bytes: &[u8], offset: usize) -> Result<Option<u64>, ()> {
    let tag = read_u32(bytes, offset).ok_or(())?;
    let value = read_u64(bytes, offset.checked_add(4).ok_or(())?).ok_or(())?;
    match tag {
        0 if value == 0 => Ok(None),
        1 => Ok(Some(value)),
        _ => Err(()),
    }
}

fn no_extensions(value: &Value) -> bool {
    value.as_array().is_some_and(Vec::is_empty)
}

fn decode_mint(bytes: &[u8], token_program_id: &str, extensions: &Value) -> Option<Mint> {
    if bytes.len() != 82
        || !matches!(token_program_id, TOKEN_PROGRAM | TOKEN_2022_PROGRAM)
        || !no_extensions(extensions)
    {
        return None;
    }
    let initialized = *bytes.get(45)?;
    if initialized > 1 {
        return None;
    }
    Some(Mint {
        token_program_id: token_program_id.to_owned(),
        authority: decode_coption_address(bytes, 0)?.into_option(),
        supply: read_u64(bytes, 36)?,
        decimals: *bytes.get(44)?,
        initialized: initialized == 1,
        freeze_authority: decode_coption_address(bytes, 46)?.into_option(),
    })
}

fn decode_token_account(
    bytes: &[u8],
    token_program_id: &str,
    extensions: &Value,
) -> Option<TokenAccount> {
    if bytes.len() != 165
        || !matches!(token_program_id, TOKEN_PROGRAM | TOKEN_2022_PROGRAM)
        || !no_extensions(extensions)
    {
        return None;
    }
    let state = match bytes.get(108)? {
        1 => "initialized",
        2 => "frozen",
        _ => return None,
    };
    let delegate = decode_coption_address(bytes, 72)?;
    let delegated_amount = read_u64(bytes, 121)?;
    if matches!(delegate, COptionAddress::Absent) && delegated_amount != 0 {
        return None;
    }
    Some(TokenAccount {
        token_program_id: token_program_id.to_owned(),
        mint: read_address(bytes, 0)?,
        owner: read_address(bytes, 32)?,
        amount: read_u64(bytes, 64)?,
        delegate: delegate.into_option(),
        state,
        is_native_reserve: decode_coption_u64(bytes, 109).ok()?,
        delegated_amount,
        close_authority: decode_coption_address(bytes, 129)?.into_option(),
    })
}

fn closure_valid(map: &Map<String, Value>, prefix: &str) -> bool {
    let expected = value_str(map, &format!("expected{prefix}")).and_then(canonical_u64);
    let observed = value_str(map, &format!("observed{prefix}")).and_then(canonical_u64);
    let skipped = value_str(map, &format!("skipped{prefix}")).and_then(canonical_u64);
    let quarantined = value_str(map, &format!("quarantined{prefix}")).and_then(canonical_u64);
    matches!((expected, observed, skipped, quarantined),
        (Some(e), Some(o), Some(s), Some(q)) if o.checked_add(s).and_then(|sum| sum.checked_add(q)) == Some(e))
}

#[derive(Clone, PartialEq, Eq)]
struct SnapshotState {
    owner_program_id: String,
    lamports: u64,
    executable: bool,
    data: Vec<u8>,
}

type SnapshotStateByBoundary = (Option<SnapshotState>, Option<SnapshotState>);

/// Evaluates only the static synthetic Phase 6B fixture contract. It performs no I/O and is not
/// connected to callbacks, networking, timers, storage, or the production reducer path.
#[allow(clippy::too_many_lines)]
#[must_use]
pub fn evaluate_pump_silver_state_fixture(event_value: &Value, evidence_value: &Value) -> Value {
    let Some(event) = parse_event_binding(event_value) else {
        return quarantine("INVALID_INPUT_SCHEMA", None, None, None);
    };
    let binding_hash = domain_hash(EVENT_BINDING_HASH_DOMAIN, event_value).unwrap_or_default();
    if !GOLDEN_BINDING_HASHES.contains(&binding_hash.as_str()) {
        return quarantine("PHASE6A_INVALID", None, None, None);
    }
    macro_rules! reject {
        ($reason:expr) => {
            return quarantine(
                $reason,
                Some(&event.event_key),
                Some(&event.source_phase6a_sha256),
                Some(&binding_hash),
            )
        };
    }

    let Some(evidence) = evidence_value.as_object() else {
        reject!("INVALID_INPUT_SCHEMA");
    };
    if evidence.contains_key("observability") {
        reject!("OBSERVABILITY_CANONICAL_INFLUENCE_FORBIDDEN");
    }
    if !exact_keys(evidence, EVIDENCE_KEYS)
        || value_str(evidence, "schemaVersion") != Some("PUMP_SILVER_STATE_EVIDENCE_1")
        || value_str(evidence, "eventKey") != Some(&event.event_key)
    {
        reject!("INVALID_INPUT_SCHEMA");
    }

    let Some(registry_value) = evidence.get("registry") else {
        reject!("INVALID_INPUT_SCHEMA");
    };
    let Some(registry) = registry_value.as_object() else {
        reject!("INVALID_INPUT_SCHEMA");
    };
    if !exact_keys(registry, REGISTRY_KEYS) {
        reject!("INVALID_INPUT_SCHEMA");
    }
    if registry.get("researchReady") == Some(&Value::Bool(true)) {
        reject!("RESEARCH_READY_FORBIDDEN");
    }
    if registry.get("approved") == Some(&Value::Bool(true)) {
        reject!("SELF_APPROVED_REGISTRY");
    }
    if registry.get("realActivationSlotRange") != Some(&Value::Null) {
        reject!("REAL_ACTIVATION_RANGE_FORBIDDEN");
    }
    let registry_valid = value_str(registry, "schemaVersion")
        == Some("PUMP_SILVER_STATE_FIXTURE_REGISTRY_1")
        && registry.get("approved") == Some(&Value::Bool(false))
        && registry.get("researchReady") == Some(&Value::Bool(false))
        && value_str(registry, "evidenceClass") == Some("SYNTHETIC_TEST_ONLY")
        && value_str(registry, "activationSlotEvidence") == Some("NONE_SYNTHETIC_FIXTURE_ONLY")
        && value_str(registry, "officialDocsCommit") == Some(PUMP_DOCS_COMMIT)
        && value_str(registry, "pumpIdlSha256") == Some(PUMP_IDL_SHA256)
        && value_str(registry, "pumpProgramId") == Some(PUMP_PROGRAM_ID)
        && value_str(registry, "bondingCurveLayout") == Some("PUMP_IDL_BONDING_CURVE_9C82F61_1")
        && value_str(registry, "bondingCurveDiscriminatorHex") == Some(CURVE_DISCRIMINATOR)
        && value_str(registry, "legacyTokenProgramId") == Some(TOKEN_PROGRAM)
        && value_str(registry, "token2022ProgramId") == Some(TOKEN_2022_PROGRAM)
        && registry
            .get("supportedToken2022Extensions")
            .is_some_and(no_extensions)
        && value_str(registry, "fixtureBindingSha256") == Some(&binding_hash);
    if !registry_valid {
        reject!("INVALID_REGISTRY");
    }

    let Some(snapshots) = evidence.get("snapshots").and_then(Value::as_array) else {
        reject!("INVALID_INPUT_SCHEMA");
    };
    let expected_roles: &[&str] = if event.quote_token_account.is_some() {
        &[
            "bonding_curve",
            "mint",
            "base_bonding_curve_token_account",
            "quote_bonding_curve_token_account",
        ]
    } else {
        &["bonding_curve", "mint", "base_bonding_curve_token_account"]
    };
    let expected_snapshot_count = expected_roles.len() * 2;
    if snapshots.len() < expected_snapshot_count {
        reject!("MISSING_RAW_ACCOUNT_BYTES");
    }
    if snapshots.len() > expected_snapshot_count {
        reject!("AMBIGUOUS_ACCOUNT_WRITES");
    }

    let mut identities = BTreeSet::new();
    let mut before_curve = None;
    let mut after_curve = None;
    let mut before_curve_lamports = None;
    let mut after_curve_lamports = None;
    let mut before_mint = None;
    let mut after_mint = None;
    let mut before_base = None;
    let mut after_base = None;
    let mut before_quote = None;
    let mut after_quote = None;
    let mut before_ordinals = Vec::new();
    let mut after_ordinals = Vec::new();
    let mut raw_state_by_role: BTreeMap<String, SnapshotStateByBoundary> = BTreeMap::new();
    for snapshot_value in snapshots {
        let Some(snapshot) = snapshot_value.as_object() else {
            reject!("INVALID_INPUT_SCHEMA");
        };
        if !exact_keys(snapshot, SNAPSHOT_KEYS) {
            reject!("INVALID_INPUT_SCHEMA");
        }
        if value_str(snapshot, "schemaVersion") != Some("PUMP_SILVER_ACCOUNT_SNAPSHOT_1")
            || value_str(snapshot, "source") != Some("SYNTHETIC_EXACT_BYTES")
            || value_str(snapshot, "evidenceClass") != Some("INSTRUCTION_EXACT_SYNTHETIC")
            || value_str(snapshot, "signature") != Some(&event.signature)
            || value_str(snapshot, "slot") != Some(&event.slot)
            || snapshot.get("transactionIndex").and_then(Value::as_u64)
                != Some(event.transaction_index)
            || value_str(snapshot, "parentInstructionLocation")
                != Some(&event.parent_instruction_location)
            || snapshot
                .get("parentInstructionIndex")
                .and_then(Value::as_u64)
                != Some(event.parent_instruction_index)
            || nullable_u32(&snapshot["parentStackHeight"]) != Ok(event.parent_stack_height)
            || value_str(snapshot, "eventInstructionLocation")
                != Some(&event.event_instruction_location)
            || nullable_u32(&snapshot["eventParentInstructionIndex"])
                != Ok(event.event_parent_instruction_index)
            || snapshot
                .get("eventInstructionIndex")
                .and_then(Value::as_u64)
                != Some(event.event_instruction_index)
            || nullable_u32(&snapshot["eventStackHeight"]) != Ok(event.event_stack_height)
            || value_str(snapshot, "eventKey") != Some(&event.event_key)
        {
            reject!("INVALID_SNAPSHOT_COORDINATES");
        }
        if snapshot.get("transactionWideBalanceOnly") != Some(&Value::Bool(false)) {
            reject!("TRANSACTION_WIDE_BALANCE_ONLY");
        }
        if value_str(snapshot, "stateAuthority") != Some("RAW_ACCOUNT_STATE") {
            reject!("EVENT_FIELDS_AS_STATE_AUTHORITY");
        }
        let role = value_str(snapshot, "accountRole").unwrap_or("");
        let boundary = value_str(snapshot, "boundary").unwrap_or("");
        if !expected_roles.contains(&role)
            || !matches!(
                boundary,
                "parent_instruction_pre" | "parent_instruction_post"
            )
        {
            reject!("INVALID_INPUT_SCHEMA");
        }
        if !identities.insert(format!("{boundary}:{role}")) {
            reject!("DUPLICATE_SNAPSHOT");
        }
        let Some(bytes) = value_str(snapshot, "dataHex").and_then(|value| decode_hex(value, 256))
        else {
            reject!("MISSING_RAW_ACCOUNT_BYTES");
        };
        if value_str(snapshot, "dataSha256") != Some(&sha256_hex(&bytes)) {
            reject!("RAW_ACCOUNT_HASH_MISMATCH");
        }
        let (expected_pubkey, expected_owner) = match role {
            "bonding_curve" => (event.bonding_curve.as_str(), PUMP_PROGRAM_ID),
            "mint" => (event.mint.as_str(), event.token_program_id.as_str()),
            "base_bonding_curve_token_account" => (
                event.base_token_account.as_str(),
                event.token_program_id.as_str(),
            ),
            "quote_bonding_curve_token_account" => (
                event.quote_token_account.as_deref().unwrap_or(""),
                event.quote_token_program_id.as_deref().unwrap_or(""),
            ),
            _ => unreachable!(),
        };
        if value_str(snapshot, "accountPubkey") != Some(expected_pubkey) {
            reject!("ACCOUNT_IDENTITY_MISMATCH");
        }
        if value_str(snapshot, "ownerProgramId") != Some(expected_owner) {
            reject!("WRONG_ACCOUNT_OWNER");
        }
        let Some(lamports_text) = value_str(snapshot, "lamports") else {
            reject!("INVALID_INPUT_SCHEMA");
        };
        let Some(executable) = snapshot.get("executable").and_then(Value::as_bool) else {
            reject!("INVALID_INPUT_SCHEMA");
        };
        let Some(lamports) = canonical_u64(lamports_text) else {
            reject!("UNSAFE_INTEGER");
        };
        if executable {
            reject!("UNKNOWN_ACCOUNT_LAYOUT");
        }
        let role_state = raw_state_by_role.entry(role.to_owned()).or_default();
        let account_state = SnapshotState {
            owner_program_id: expected_owner.to_owned(),
            lamports,
            executable,
            data: bytes.clone(),
        };
        if boundary == "parent_instruction_pre" {
            role_state.0 = Some(account_state);
        } else {
            role_state.1 = Some(account_state);
        }
        if !snapshot
            .get("token2022Extensions")
            .is_some_and(no_extensions)
        {
            reject!(if expected_owner == TOKEN_2022_PROGRAM {
                "UNSUPPORTED_TOKEN_2022_EXTENSION"
            } else {
                "UNKNOWN_ACCOUNT_LAYOUT"
            });
        }
        let Some(ordinal) = value_str(snapshot, "writeOrdinal").and_then(canonical_u64) else {
            reject!("UNSAFE_INTEGER");
        };
        if boundary == "parent_instruction_pre" {
            before_ordinals.push(ordinal);
        } else {
            after_ordinals.push(ordinal);
        }
        match role {
            "bonding_curve" => {
                let Some(decoded) = decode_curve(&bytes) else {
                    reject!("UNKNOWN_ACCOUNT_LAYOUT");
                };
                if boundary == "parent_instruction_pre" {
                    before_curve = Some(decoded);
                    before_curve_lamports = Some(lamports);
                } else {
                    after_curve = Some(decoded);
                    after_curve_lamports = Some(lamports);
                }
            }
            "mint" => {
                let Some(decoded) =
                    decode_mint(&bytes, expected_owner, &snapshot["token2022Extensions"])
                else {
                    reject!(if expected_owner == TOKEN_2022_PROGRAM {
                        "UNSUPPORTED_TOKEN_2022_EXTENSION"
                    } else {
                        "UNKNOWN_ACCOUNT_LAYOUT"
                    });
                };
                if boundary == "parent_instruction_pre" {
                    before_mint = Some(decoded);
                } else {
                    after_mint = Some(decoded);
                }
            }
            "base_bonding_curve_token_account" => {
                let Some(decoded) =
                    decode_token_account(&bytes, expected_owner, &snapshot["token2022Extensions"])
                else {
                    reject!(if expected_owner == TOKEN_2022_PROGRAM {
                        "UNSUPPORTED_TOKEN_2022_EXTENSION"
                    } else {
                        "UNKNOWN_ACCOUNT_LAYOUT"
                    });
                };
                if boundary == "parent_instruction_pre" {
                    before_base = Some(decoded);
                } else {
                    after_base = Some(decoded);
                }
            }
            "quote_bonding_curve_token_account" => {
                let Some(decoded) =
                    decode_token_account(&bytes, expected_owner, &snapshot["token2022Extensions"])
                else {
                    reject!("UNKNOWN_ACCOUNT_LAYOUT");
                };
                if boundary == "parent_instruction_pre" {
                    before_quote = Some(decoded);
                } else {
                    after_quote = Some(decoded);
                }
            }
            _ => unreachable!(),
        }
    }
    if before_ordinals.is_empty()
        || after_ordinals.is_empty()
        || before_ordinals
            .iter()
            .any(|before| after_ordinals.iter().any(|after| before >= after))
    {
        reject!("INVALID_SNAPSHOT_ORDER");
    }
    let (
        Some(before_curve),
        Some(after_curve),
        Some(before_mint),
        Some(after_mint),
        Some(before_base),
        Some(after_base),
    ) = (
        before_curve,
        after_curve,
        before_mint,
        after_mint,
        before_base,
        after_base,
    )
    else {
        reject!("MISSING_RAW_ACCOUNT_BYTES");
    };
    if event.quote_token_account.is_some() && (before_quote.is_none() || after_quote.is_none()) {
        reject!("MISSING_RAW_ACCOUNT_BYTES");
    }
    let Some(provenance) = evidence.get("provenance").and_then(Value::as_object) else {
        reject!("INVALID_CAR_PROVENANCE");
    };
    if !exact_keys(provenance, PROVENANCE_KEYS)
        || value_str(provenance, "schemaVersion") != Some("PUMP_SILVER_STATE_PROVENANCE_1")
        || value_str(provenance, "evidenceClass") != Some("SYNTHETIC_TEST_ONLY")
        || value_str(provenance, "epochCid") != Some(GOLDEN_EPOCH_CID)
        || value_str(provenance, "carSha256") != Some(GOLDEN_CAR_SHA256)
        || value_str(provenance, "carFileSizeBytes") != Some(GOLDEN_CAR_SIZE)
        || value_str(provenance, "parserGitSha") != Some(GOLDEN_PARSER_GIT_SHA)
        || value_str(provenance, "reducerGitSha") != Some(GOLDEN_REDUCER_GIT_SHA)
        || value_str(provenance, "adapterGitSha") != Some(GOLDEN_ADAPTER_GIT_SHA)
        || value_str(provenance, "sourceSha256") != Some(&event.source_phase6a_sha256)
        || value_str(provenance, "eventBindingSha256") != Some(&binding_hash)
        || !value_str(provenance, "outputSha256").is_some_and(|value| valid_hex(value, 32))
        || value_str(provenance, "cumulativeSourceBytes")
            .is_none_or(|value| canonical_u128(value).is_none())
    {
        reject!("INVALID_CAR_PROVENANCE");
    }
    let Some(slot_range) = provenance.get("slotRange").and_then(Value::as_object) else {
        reject!("INVALID_SLOT_INVENTORY");
    };
    if !exact_keys(slot_range, SLOT_RANGE_KEYS)
        || value_str(provenance, "slotInventorySha256") != Some(GOLDEN_INVENTORY_SHA256)
        || value_str(provenance, "slotInventorySizeBytes") != Some(GOLDEN_INVENTORY_SIZE)
        || value_str(provenance, "slotInventoryEntryCount") != Some(GOLDEN_INVENTORY_ENTRIES)
        || value_str(slot_range, "startInclusive") != Some(GOLDEN_SLOT_START)
        || value_str(slot_range, "endExclusive") != Some(GOLDEN_SLOT_END)
        || !canonical_u64(&event.slot)
            .is_some_and(|slot| (361_000_001..361_000_002).contains(&slot))
    {
        reject!("INVALID_SLOT_INVENTORY");
    }
    if value_str(provenance, "cumulativeSourceBytes").and_then(canonical_u128) != Some(4608) {
        reject!("INVALID_CAR_PROVENANCE");
    }
    if value_str(provenance, "outputSha256")
        != domain_hash(
            SNAPSHOT_HASH_DOMAIN,
            evidence.get("snapshots").unwrap_or(&Value::Null),
        )
        .as_deref()
    {
        reject!("RAW_ACCOUNT_HASH_MISMATCH");
    }

    let Some(coverage) = evidence.get("coverage").and_then(Value::as_object) else {
        reject!("INCOMPLETE_COVERAGE");
    };
    if !exact_keys(coverage, COVERAGE_KEYS)
        || !closure_valid(coverage, "Slots")
        || !closure_valid(coverage, "Callbacks")
    {
        reject!("INCOMPLETE_COVERAGE");
    }
    if !coverage
        .get("quarantineByReason")
        .and_then(Value::as_array)
        .is_some_and(Vec::is_empty)
    {
        reject!("QUARANTINE_ACCOUNTING_MISMATCH");
    }
    if value_str(coverage, "expectedSlots") != Some("1")
        || value_str(coverage, "observedSlots") != Some("1")
        || value_str(coverage, "skippedSlots") != Some("0")
        || value_str(coverage, "quarantinedSlots") != Some("0")
        || value_str(coverage, "expectedCallbacks") != Some("2")
        || value_str(coverage, "observedCallbacks") != Some("2")
        || value_str(coverage, "skippedCallbacks") != Some("0")
        || value_str(coverage, "quarantinedCallbacks") != Some("0")
    {
        reject!("INCOMPLETE_COVERAGE");
    }
    let Some(budget) = evidence.get("pilotBudget").and_then(Value::as_object) else {
        reject!("UNSAFE_INTEGER");
    };
    if !exact_keys(budget, BUDGET_KEYS)
        || GOLDEN_PILOT_BUDGET
            .iter()
            .any(|(key, value)| value_str(budget, key) != Some(*value))
    {
        reject!("UNSAFE_INTEGER");
    }
    if value_str(coverage, "expectedSlots").and_then(canonical_u64)
        > value_str(budget, "maxSlots").and_then(canonical_u64)
        || value_str(coverage, "expectedCallbacks").and_then(canonical_u64)
            > value_str(budget, "maxCallbacks").and_then(canonical_u64)
    {
        reject!("INCOMPLETE_COVERAGE");
    }
    let rerun_payload = json!({
        "registry": evidence["registry"].clone(),
        "sourceSha256": provenance["sourceSha256"].clone(),
        "eventBindingSha256": provenance["eventBindingSha256"].clone(),
        "outputSha256": provenance["outputSha256"].clone(),
        "coverage": evidence["coverage"].clone(),
        "pilotBudget": evidence["pilotBudget"].clone(),
    });
    if value_str(provenance, "deterministicRerunHash")
        != domain_hash(RERUN_HASH_DOMAIN, &rerun_payload).as_deref()
    {
        reject!("RERUN_HASH_MISMATCH");
    }

    if event.execution_status == "failed"
        && raw_state_by_role
            .values()
            .any(|(before, after)| before != after)
    {
        reject!("FAILED_TRANSACTION_ROLLBACK_MISMATCH");
    }
    if event.execution_status != "failed"
        && raw_state_by_role.iter().any(|(role, (before, after))| {
            let legacy_native_curve =
                role == "bonding_curve" && event.quote_token_account.is_none();
            !legacy_native_curve
                && match (before, after) {
                    (Some(before), Some(after)) => before.lamports != after.lamports,
                    _ => true,
                }
        })
    {
        reject!("ACCOUNT_LAMPORTS_MISMATCH");
    }
    if before_curve.quote_mint != event.quote_mint || after_curve.quote_mint != event.quote_mint {
        reject!("ACCOUNT_IDENTITY_MISMATCH");
    }
    if before_mint.supply != after_mint.supply
        || before_mint.decimals != after_mint.decimals
        || before_mint.authority != after_mint.authority
        || before_mint.freeze_authority != after_mint.freeze_authority
        || before_curve.token_total_supply != after_curve.token_total_supply
        || before_curve.token_total_supply != before_mint.supply
        || after_curve.token_total_supply != after_mint.supply
    {
        reject!("SUPPLY_OR_DECIMAL_MISMATCH");
    }
    if event.quote_token_account.is_none() {
        let (Some(before_lamports), Some(after_lamports)) =
            (before_curve_lamports, after_curve_lamports)
        else {
            reject!("RESERVE_MISMATCH");
        };
        let before_rent = before_lamports.checked_sub(before_curve.real_quote_reserves);
        let after_rent = after_lamports.checked_sub(after_curve.real_quote_reserves);
        if before_rent != Some(SYNTHETIC_ACCOUNT_RENT_RESERVE)
            || after_rent != Some(SYNTHETIC_ACCOUNT_RENT_RESERVE)
        {
            reject!("RESERVE_MISMATCH");
        }
    }
    if !before_mint.initialized
        || !after_mint.initialized
        || before_mint.decimals != event.token_decimals
        || after_mint.decimals != event.token_decimals
    {
        reject!("MINT_STATE_MISMATCH");
    }
    if before_curve.creator != event.creator
        || after_curve.creator != event.creator
        || before_curve.is_mayhem_mode != event.mayhem_mode
        || after_curve.is_mayhem_mode != event.mayhem_mode
        || before_curve.is_cashback_coin != event.is_cashback_coin
        || after_curve.is_cashback_coin != event.is_cashback_coin
        || before_curve.complete
        || (after_curve.complete && after_curve.real_token_reserves != 0)
    {
        reject!("CURVE_STATE_MISMATCH");
    }
    for curve in [&before_curve, &after_curve] {
        if curve.real_token_reserves > curve.virtual_token_reserves
            || curve.real_token_reserves > curve.token_total_supply
            || curve.real_quote_reserves > curve.virtual_quote_reserves
        {
            reject!("RESERVE_MISMATCH");
        }
    }
    let valid_token = |account: &TokenAccount, mint: &str, amount: u64, program: &str| {
        account.token_program_id == program
            && account.mint == mint
            && account.owner == event.bonding_curve
            && account.amount == amount
            && account.state == "initialized"
            && account.delegate.is_none()
            && account.delegated_amount == 0
            && account.close_authority.is_none()
            && account.is_native_reserve.is_none()
    };
    if !valid_token(
        &before_base,
        &event.mint,
        before_curve.real_token_reserves,
        &event.token_program_id,
    ) || !valid_token(
        &after_base,
        &event.mint,
        after_curve.real_token_reserves,
        &event.token_program_id,
    ) {
        reject!("RESERVE_MISMATCH");
    }
    let quote_accounts_valid = match (
        before_quote.as_ref(),
        after_quote.as_ref(),
        event.quote_token_program_id.as_deref(),
    ) {
        (Some(before_quote), Some(after_quote), Some(quote_program)) => {
            valid_token(
                before_quote,
                &event.quote_mint,
                before_curve.real_quote_reserves,
                quote_program,
            ) && valid_token(
                after_quote,
                &event.quote_mint,
                after_curve.real_quote_reserves,
                quote_program,
            )
        }
        (None, None, None) => true,
        _ => false,
    };
    if !quote_accounts_valid {
        reject!("RESERVE_MISMATCH");
    }
    if event.execution_status != "failed" {
        let transition = if event.kind == "buy" {
            before_curve
                .virtual_token_reserves
                .checked_sub(event.token_amount)
                == Some(after_curve.virtual_token_reserves)
                && before_curve
                    .real_token_reserves
                    .checked_sub(event.token_amount)
                    == Some(after_curve.real_token_reserves)
                && before_curve
                    .virtual_quote_reserves
                    .checked_add(event.quote_amount)
                    == Some(after_curve.virtual_quote_reserves)
                && before_curve
                    .real_quote_reserves
                    .checked_add(event.quote_amount)
                    == Some(after_curve.real_quote_reserves)
        } else {
            before_curve
                .virtual_token_reserves
                .checked_add(event.token_amount)
                == Some(after_curve.virtual_token_reserves)
                && before_curve
                    .real_token_reserves
                    .checked_add(event.token_amount)
                    == Some(after_curve.real_token_reserves)
                && before_curve
                    .virtual_quote_reserves
                    .checked_sub(event.quote_amount)
                    == Some(after_curve.virtual_quote_reserves)
                && before_curve
                    .real_quote_reserves
                    .checked_sub(event.quote_amount)
                    == Some(after_curve.real_quote_reserves)
        };
        if !transition {
            reject!("RESERVE_MISMATCH");
        }
        if after_curve.virtual_token_reserves.to_string() != event.virtual_token_reserves
            || after_curve.virtual_quote_reserves.to_string() != event.virtual_quote_reserves
            || after_curve.real_token_reserves.to_string() != event.real_token_reserves
            || after_curve.real_quote_reserves.to_string() != event.real_quote_reserves
        {
            reject!("EVENT_STATE_CONFLICT");
        }
    }

    if event.execution_status == "failed" {
        reject!("FAILED_TRANSACTION");
    }

    let mut unsigned = json!({
        "schemaVersion": "PUMP_SILVER_STATE_CONTRACT_1",
        "status": "FIXTURE_VALID",
        "approved": false,
        "researchReady": false,
        "pilotEligible": false,
        "eventKey": event.event_key,
        "sourcePhase6aSha256": event.source_phase6a_sha256,
        "eventBindingSha256": binding_hash,
        "evidenceClass": "SYNTHETIC_TEST_ONLY",
        "state": {
            "before": {
                "bondingCurve": before_curve.json(),
                "mint": before_mint.json(),
                "baseBondingCurveTokenAccount": before_base.json(),
                "quoteBondingCurveTokenAccount": before_quote.as_ref().map(TokenAccount::json),
            },
            "after": {
                "bondingCurve": after_curve.json(),
                "mint": after_mint.json(),
                "baseBondingCurveTokenAccount": after_base.json(),
                "quoteBondingCurveTokenAccount": after_quote.as_ref().map(TokenAccount::json),
            },
        },
        "provenance": evidence["provenance"].clone(),
        "coverage": evidence["coverage"].clone(),
        "quarantineReasons": [],
    });
    let hash = domain_hash(STATE_HASH_DOMAIN, &unsigned).unwrap_or_default();
    if let Some(map) = unsigned.as_object_mut() {
        map.insert("canonicalHash".to_owned(), Value::String(hash));
    }
    unsigned
}

#[cfg(test)]
mod parity_regressions {
    use super::*;

    fn vector(index: usize) -> (Value, Value) {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/pump-silver/state-vectors.json"
        ))
        .expect("shared state fixture must parse");
        (
            fixture["vectors"][index]["eventBinding"].clone(),
            fixture["vectors"][index]["evidence"].clone(),
        )
    }

    fn first_vector() -> (Value, Value) {
        vector(0)
    }

    fn rebind_rerun(evidence: &mut Value) {
        let payload = json!({
            "registry": evidence["registry"].clone(),
            "sourceSha256": evidence["provenance"]["sourceSha256"].clone(),
            "eventBindingSha256": evidence["provenance"]["eventBindingSha256"].clone(),
            "outputSha256": evidence["provenance"]["outputSha256"].clone(),
            "coverage": evidence["coverage"].clone(),
            "pilotBudget": evidence["pilotBudget"].clone(),
        });
        evidence["provenance"]["deterministicRerunHash"] =
            Value::String(domain_hash(RERUN_HASH_DOMAIN, &payload).expect("canonical fixture"));
    }

    #[test]
    fn parses_creator_mayhem_and_cashback_binding_fields() {
        let (mut event, _) = first_vector();
        let map = event
            .as_object_mut()
            .expect("event binding must be an object");
        map.insert(
            "creator".to_owned(),
            Value::String("3JF3sEqM796hk5WFqA6EtmEwJQ9quALszsfJyvXNQKy3".to_owned()),
        );
        map.insert("mayhemMode".to_owned(), Value::Bool(false));
        map.insert("isCashbackCoin".to_owned(), Value::Bool(false));

        let parsed = parse_event_binding(&event).expect("bound state identity must parse");
        assert_eq!(
            parsed.creator,
            "3JF3sEqM796hk5WFqA6EtmEwJQ9quALszsfJyvXNQKy3"
        );
        assert!(!parsed.mayhem_mode);
        assert!(!parsed.is_cashback_coin);
    }

    #[test]
    fn rejects_a_syntactically_valid_wrong_base_ata() {
        let (mut event, _) = first_vector();
        event["baseBondingCurveTokenAccount"] =
            Value::String("11111111111111111111111111111111".to_owned());
        assert!(parse_event_binding(&event).is_none());
    }

    #[test]
    fn rejects_a_syntactically_valid_wrong_quote_ata() {
        let (mut event, _) = vector(2);
        event["quoteBondingCurveTokenAccount"] =
            Value::String("11111111111111111111111111111111".to_owned());
        assert!(parse_event_binding(&event).is_none());
    }

    #[test]
    fn checked_coverage_overflow_quarantines() {
        let (event, mut evidence) = first_vector();
        evidence["coverage"]["expectedCallbacks"] = Value::String(u64::MAX.to_string());
        evidence["coverage"]["observedCallbacks"] = Value::String(u64::MAX.to_string());
        evidence["coverage"]["skippedCallbacks"] = Value::String("1".to_owned());
        evidence["coverage"]["quarantinedCallbacks"] = Value::String("0".to_owned());
        rebind_rerun(&mut evidence);
        let result = evaluate_pump_silver_state_fixture(&event, &evidence);
        assert_eq!(result["status"], "QUARANTINED");
        assert_eq!(result["quarantineReasons"][0], "INCOMPLETE_COVERAGE");
    }

    #[test]
    fn malformed_source_hash_is_never_echoed_and_output_is_unapproved() {
        let (mut event, evidence) = first_vector();
        event["sourcePhase6aSha256"] = Value::String("not-a-hash".to_owned());
        let result = evaluate_pump_silver_state_fixture(&event, &evidence);
        assert_eq!(result["status"], "QUARANTINED");
        assert!(result["sourcePhase6aSha256"].is_null());
        assert_eq!(result["approved"], false);
    }
}
