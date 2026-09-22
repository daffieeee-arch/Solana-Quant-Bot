//! Bounded offline CAR/CID and selected-slot envelope checks.
//!
//! This is not Solana/Pump decoding or epoch-root inclusion verification. Callers must
//! independently bind assembled bytes and the selected slot to verified acquisition receipts.
//! The narrow archival schema is pinned by `car-source-receipt.json`; no upstream runtime
//! is imported. `DataFrame` bytes/checksums remain opaque, including signed checksum integers.

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use thiserror::Error;

type Cid = [u8; 36];
const CID_PREFIX: [u8; 4] = [1, 0x71, 0x12, 32];
pub const CAR_SOURCE_COMMIT: &str = "cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24";

/// Explicit verification resource limits, supplied by the owning plan; not chain constants.
#[derive(Clone, Copy, Debug)]
pub struct VerificationLimits {
    pub max_total_bytes: usize,
    pub max_section_bytes: usize,
    pub max_nodes: usize,
    pub max_links: usize,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum CarError {
    #[error("CAR_VERIFICATION_LIMIT")]
    Limit,
    #[error("CAR_TRUNCATED")]
    Truncated,
    #[error("CAR_VARINT_INVALID")]
    Varint,
    #[error("CAR_CID_UNSUPPORTED")]
    UnsupportedCid,
    #[error("CAR_CID_CONTENT_MISMATCH")]
    Digest,
    #[error("CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID")]
    Schema,
    #[error("CAR_NODE_KIND_UNSUPPORTED")]
    UnsupportedNode,
    #[error("CAR_DUPLICATE_CID")]
    DuplicateCid,
    #[error("CAR_EXPECTED_ONE_TERMINAL_BLOCK")]
    Block,
    #[error("CAR_SELECTED_SLOT_MISMATCH")]
    Slot,
    #[error("CAR_MISSING_LINK")]
    MissingLink,
    #[error("CAR_LINK_TARGET_KIND_MISMATCH")]
    LinkKind,
    #[error("CAR_GRAPH_CYCLE")]
    Cycle,
    #[error("CAR_UNREACHABLE_NODE")]
    Unreachable,
    #[error("CAR_DECLARED_ROOT_MISMATCH")]
    Root,
    #[error("CAR_AMBIGUOUS_ARCHIVAL_ORDER")]
    AmbiguousOrder,
    #[error("TRANSACTION_INDEX_DISAGREEMENT")]
    TransactionIndex,
}

pub type CarResult<T> = Result<T, CarError>;

/// Counts of source-shaped archival envelopes after the complete CID/slot/link gate.
/// These are not decoded Solana transactions, Pump observations or research metrics.
#[derive(Clone, Debug, Default, Serialize, PartialEq, Eq)]
pub struct ArchivalNodeCounts {
    pub transaction: usize,
    pub entry: usize,
    pub block: usize,
    pub rewards: usize,
    pub dataframe: usize,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SlotIntegrityReport {
    pub source_commit: &'static str,
    pub selected_slot: u64,
    pub captured_section_bytes: usize,
    pub verified_nodes: usize,
    pub verified_links: usize,
    // Preserve the historical serialized fixture-report contract. The separate
    // read-only verification report explicitly serializes these new diagnostics.
    #[serde(skip)]
    pub archival_node_counts: ArchivalNodeCounts,
    pub terminal_block_cid_hex: String,
    pub node_cid_integrity: &'static str,
    pub selected_slot_envelope: &'static str,
    pub root_to_slot_membership: &'static str,
    pub source_observation: &'static str,
    pub dataframe_payload_and_checksum: &'static str,
    pub domain_counts: &'static str,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct HeaderReport {
    pub declared_root_cid_hex: String,
    pub header_bytes_consumed: usize,
    pub captured_prefix_bytes: usize,
    pub trailing_prefix_bytes_unverified: usize,
    pub declared_root_agreement: &'static str,
    pub root_to_slot_membership: &'static str,
}

/// Byte offsets are relative to the supplied section buffer, not to the complete CAR.
/// The owning Raw receipt supplies its file/range identity. Lengths include exactly
/// the encoded bytes: section spans include their varint, CBOR spans do not include CID.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct ByteSpan {
    pub offset: usize,
    pub length: usize,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ArchivalKind {
    Transaction,
    Entry,
    Block,
    Rewards,
    DataFrame,
}

/// Lossless archival fields only. A checksum value is retained, never verified here.
/// CBOR integers are decimal strings, including negative checksums and full u64 values.
/// Neither payload concatenation/decompression nor Solana/Pump decoding is implied.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct DataFrameFact {
    pub raw_cbor_span: ByteSpan,
    pub checksum: Option<String>,
    pub frame_index: Option<String>,
    pub total: Option<String>,
    pub data_span: ByteSpan,
    pub next_cid_hex: Vec<String>,
    pub payload_and_checksum: &'static str,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct ArchivalNodeFact {
    pub cid_hex: String,
    pub kind: ArchivalKind,
    pub physical_ordinal: usize,
    pub section_span: ByteSpan,
    pub raw_cbor_span: ByteSpan,
    pub slot: Option<String>,
    pub frames: Vec<DataFrameFact>,
    pub linked_cid_hex: Vec<String>,
}

/// One atomic opaque data+metadata package in source block/entry link order.
/// Its ordinal is archival link order, not a proven Solana execution position.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct TransactionEnvelopeFact {
    pub transaction_cid_hex: String,
    pub entry_cid_hex: String,
    pub block_cid_hex: String,
    pub block_entry_ordinal: usize,
    pub entry_transaction_ordinal: usize,
    pub archival_ordinal: usize,
    pub slot: String,
    pub transaction_position: Option<String>,
    pub data: DataFrameFact,
    pub metadata: DataFrameFact,
    pub atomic_package: &'static str,
    pub solana_transaction_decode: &'static str,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SlotArchivalInspection {
    pub integrity: SlotIntegrityReport,
    pub archival_nodes: Vec<ArchivalNodeFact>,
    pub transaction_envelopes: Vec<TransactionEnvelopeFact>,
    pub ordering: &'static str,
    pub solana_transaction_decode: &'static str,
    pub pump_decode: &'static str,
}

/// Decode one canonical lowercase base32 CIDv1/DAG-CBOR/SHA2-256 sidecar value.
/// A single terminal LF or CRLF is accepted as text-file framing, not arbitrary whitespace.
/// # Errors
/// Rejects other multibases/codecs/hashes, padding, noncanonical bits or extra text.
pub fn decode_reported_cid(value: &str) -> CarResult<Vec<u8>> {
    let value = value
        .strip_suffix("\r\n")
        .or_else(|| value.strip_suffix('\n'))
        .unwrap_or(value);
    if value.len() != 59 || !value.starts_with('b') {
        return Err(CarError::UnsupportedCid);
    }
    let mut decoded = Vec::with_capacity(36);
    let (mut accumulator, mut bits) = (0_u16, 0_u32);
    for byte in value.as_bytes()[1..].iter().copied() {
        let digit = match byte {
            b'a'..=b'z' => byte - b'a',
            b'2'..=b'7' => byte - b'2' + 26,
            _ => return Err(CarError::UnsupportedCid),
        };
        accumulator = (accumulator << 5) | u16::from(digit);
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            decoded.push(u8::try_from(accumulator >> bits).map_err(|_| CarError::UnsupportedCid)?);
            accumulator &= (1 << bits) - 1;
        }
    }
    if accumulator != 0 {
        return Err(CarError::UnsupportedCid);
    }
    cid(&decoded)?;
    Ok(decoded)
}

/// Verify one bounded captured `CARv1` prefix against a separately recorded declared root.
/// Header CBOR is exactly exhausted. Any bytes following the header remain explicitly
/// unverified: a prefix capture is not automatically a collection of complete CAR sections.
/// # Errors
/// Rejects invalid bounds, header shape/version/root/CID or trailing CBOR inside the header.
pub fn verify_header(
    prefix: &[u8],
    expected_root: &[u8],
    limits: VerificationLimits,
) -> CarResult<HeaderReport> {
    check_limits(prefix, limits)?;
    let expected = cid(expected_root)?;
    let mut outer = Cursor::new(prefix);
    let length = usize::try_from(outer.varint()?).map_err(|_| CarError::Limit)?;
    if length == 0 || length > limits.max_section_bytes {
        return Err(CarError::Limit);
    }
    let mut header = Cursor::new(outer.take(length)?);
    // CARv1's exact canonical DAG-CBOR map: roots (shorter key) precedes version.
    header.expect(5, 2)?;
    if header.text()? != "roots" {
        return Err(CarError::Schema);
    }
    header.expect(4, 1)?;
    let root = header.link()?;
    if header.text()? != "version" {
        return Err(CarError::Schema);
    }
    header.expect(0, 1)?;
    header.finish()?;
    if root != expected {
        return Err(CarError::Root);
    }
    Ok(HeaderReport {
        declared_root_cid_hex: hex::encode(root),
        header_bytes_consumed: outer.position,
        captured_prefix_bytes: prefix.len(),
        trailing_prefix_bytes_unverified: prefix.len() - outer.position,
        declared_root_agreement: "MATCHED_DECLARATIONS_ONLY",
        root_to_slot_membership: "UNAVAILABLE",
    })
}

/// Check complete CAR sections for one index-selected slot, after raw publication.
/// All sections must already be assembled from the exact authorized ranges; no I/O occurs.
/// # Errors
/// Rejects framing/hash/schema/slot/closure conflicts or resource-limit exhaustion.
pub fn verify_slot_sections(
    expected_slot: u64,
    sections: &[u8],
    limits: VerificationLimits,
) -> CarResult<SlotIntegrityReport> {
    Ok(parse_slot_sections(expected_slot, sections, limits)?.integrity)
}

/// Inspect the same verified archival grammar without introducing a second decoder.
/// No fact is returned until the complete selected-slot graph passes the existing
/// verifier. This is preparation for Bronze, not decoded Solana or Pump evidence.
/// # Errors
/// Rejects all verifier errors and repeated entry/transaction references whose
/// archival position would otherwise be silently inferred or deduplicated.
pub fn inspect_slot_sections(
    expected_slot: u64,
    sections: &[u8],
    limits: VerificationLimits,
) -> CarResult<SlotArchivalInspection> {
    let parsed = parse_slot_sections(expected_slot, sections, limits)?;
    let block = &parsed.nodes[parsed.block_index];
    let mut transaction_envelopes = Vec::new();
    let mut seen_entries = std::collections::BTreeSet::new();
    let mut seen_transactions = std::collections::BTreeSet::new();
    for (block_entry_ordinal, entry_link) in block
        .links
        .iter()
        .filter(|link| link.kind == Kind::Entry)
        .enumerate()
    {
        if !seen_entries.insert(entry_link.identity) {
            return Err(CarError::AmbiguousOrder);
        }
        let entry = &parsed.nodes[parsed.by_cid[&entry_link.identity]];
        for (entry_transaction_ordinal, transaction_link) in entry.links.iter().enumerate() {
            if !seen_transactions.insert(transaction_link.identity) {
                return Err(CarError::AmbiguousOrder);
            }
            let transaction = &parsed.nodes[parsed.by_cid[&transaction_link.identity]];
            if transaction
                .transaction_position
                .as_ref()
                .is_some_and(|position| position != &transaction_envelopes.len().to_string())
            {
                return Err(CarError::TransactionIndex);
            }
            let data = transaction.frames.first().ok_or(CarError::Schema)?.clone();
            let metadata = transaction.frames.get(1).ok_or(CarError::Schema)?.clone();
            transaction_envelopes.push(TransactionEnvelopeFact {
                transaction_cid_hex: hex::encode(transaction_link.identity),
                entry_cid_hex: hex::encode(entry_link.identity),
                block_cid_hex: parsed.integrity.terminal_block_cid_hex.clone(),
                block_entry_ordinal,
                entry_transaction_ordinal,
                archival_ordinal: transaction_envelopes.len(),
                slot: expected_slot.to_string(),
                transaction_position: transaction.transaction_position.clone(),
                data,
                metadata,
                atomic_package: "OPAQUE_DATA_AND_METADATA_TOGETHER",
                solana_transaction_decode: "UNAVAILABLE_OPAQUE_DATAFRAME",
            });
        }
    }
    if transaction_envelopes.len() != parsed.integrity.archival_node_counts.transaction
        || seen_entries.len() != parsed.integrity.archival_node_counts.entry
    {
        return Err(CarError::AmbiguousOrder);
    }
    Ok(SlotArchivalInspection {
        integrity: parsed.integrity,
        archival_nodes: parsed.facts,
        transaction_envelopes,
        ordering: "BLOCK_ENTRY_TRANSACTION_LINK_ORDER_NOT_EXECUTION_ORDER",
        solana_transaction_decode: "UNAVAILABLE_OPAQUE_DATAFRAME",
        pump_decode: "UNAVAILABLE_WITHOUT_SOLANA_DECODE",
    })
}

struct ParsedSlot {
    integrity: SlotIntegrityReport,
    nodes: Vec<Node>,
    by_cid: BTreeMap<Cid, usize>,
    block_index: usize,
    facts: Vec<ArchivalNodeFact>,
}

fn parse_slot_sections(
    expected_slot: u64,
    sections: &[u8],
    limits: VerificationLimits,
) -> CarResult<ParsedSlot> {
    check_limits(sections, limits)?;
    let mut input = Cursor::new(sections);
    let mut nodes = Vec::new();
    let mut facts = Vec::new();
    let mut by_cid = BTreeMap::new();
    let mut links_used = 0;
    while input.position < sections.len() {
        if nodes.len() >= limits.max_nodes {
            return Err(CarError::Limit);
        }
        let section_offset = input.position;
        let size = usize::try_from(input.varint()?).map_err(|_| CarError::Limit)?;
        if size > limits.max_section_bytes {
            return Err(CarError::Limit);
        }
        if size <= 36 {
            return Err(CarError::Schema);
        }
        let raw_offset = input.position.checked_add(36).ok_or(CarError::Limit)?;
        let section = input.take(size)?;
        let identity = cid(&section[..36])?;
        let raw = &section[36..];
        if Sha256::digest(raw).as_slice() != &identity[4..] {
            return Err(CarError::Digest);
        }
        if by_cid.insert(identity, nodes.len()).is_some() {
            return Err(CarError::DuplicateCid);
        }
        let node = parse_node(raw, raw_offset, expected_slot, limits, &mut links_used)?;
        facts.push(ArchivalNodeFact {
            cid_hex: hex::encode(identity),
            kind: node.kind,
            physical_ordinal: nodes.len(),
            section_span: ByteSpan {
                offset: section_offset,
                length: input.position - section_offset,
            },
            raw_cbor_span: ByteSpan {
                offset: raw_offset,
                length: raw.len(),
            },
            slot: node.slot.map(|slot| slot.to_string()),
            frames: node.frames.clone(),
            linked_cid_hex: node
                .links
                .iter()
                .map(|link| hex::encode(link.identity))
                .collect(),
        });
        nodes.push(node);
    }
    let block_index = nodes.len().checked_sub(1).ok_or(CarError::Block)?;
    if nodes[block_index].kind != Kind::Block
        || nodes.iter().filter(|n| n.kind == Kind::Block).count() != 1
    {
        return Err(CarError::Block);
    }
    verify_graph(&nodes, &by_cid, block_index)?;
    let block_cid = by_cid
        .iter()
        .find_map(|(identity, index)| (*index == block_index).then_some(identity))
        .ok_or(CarError::Block)?;
    let mut archival_node_counts = ArchivalNodeCounts::default();
    for node in &nodes {
        match node.kind {
            Kind::Transaction => archival_node_counts.transaction += 1,
            Kind::Entry => archival_node_counts.entry += 1,
            Kind::Block => archival_node_counts.block += 1,
            Kind::Rewards => archival_node_counts.rewards += 1,
            Kind::DataFrame => archival_node_counts.dataframe += 1,
        }
    }
    let integrity = SlotIntegrityReport {
        source_commit: CAR_SOURCE_COMMIT,
        selected_slot: expected_slot,
        captured_section_bytes: sections.len(),
        verified_nodes: nodes.len(),
        verified_links: links_used,
        archival_node_counts,
        terminal_block_cid_hex: hex::encode(block_cid),
        node_cid_integrity: "RECOMPUTED_SHA2_256_MATCHED",
        selected_slot_envelope: "STRUCTURAL_LINK_CLOSURE_MATCHED",
        root_to_slot_membership: "UNAVAILABLE",
        source_observation: "NOT_ATTESTED_BY_VERIFIER",
        dataframe_payload_and_checksum: "NOT_EVALUATED",
        domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4",
    };
    Ok(ParsedSlot {
        integrity,
        nodes,
        by_cid,
        block_index,
        facts,
    })
}

fn check_limits(bytes: &[u8], limits: VerificationLimits) -> CarResult<()> {
    if bytes.is_empty()
        || bytes.len() > limits.max_total_bytes
        || limits.max_section_bytes == 0
        || limits.max_nodes == 0
        || limits.max_links == 0
    {
        return Err(CarError::Limit);
    }
    Ok(())
}

fn cid(bytes: &[u8]) -> CarResult<Cid> {
    let value: Cid = bytes.try_into().map_err(|_| CarError::UnsupportedCid)?;
    if value[..4] != CID_PREFIX {
        return Err(CarError::UnsupportedCid);
    }
    Ok(value)
}

type Kind = ArchivalKind;
struct Link {
    identity: Cid,
    kind: Kind,
}
struct Node {
    kind: Kind,
    links: Vec<Link>,
    slot: Option<u64>,
    transaction_position: Option<String>,
    frames: Vec<DataFrameFact>,
}

fn parse_node(
    raw: &[u8],
    raw_offset: usize,
    expected_slot: u64,
    limits: VerificationLimits,
    used: &mut usize,
) -> CarResult<Node> {
    let mut input = Cursor::new(raw);
    let length = input.array()?;
    let mut links = Vec::new();
    let mut frames = Vec::new();
    let mut node_slot = None;
    let mut transaction_position = None;
    let kind = match input.uint()? {
        0 => {
            if !(4..=5).contains(&length) {
                return Err(CarError::Schema);
            }
            frames.push(dataframe(&mut input, raw_offset, &mut links, limits, used)?);
            frames.push(dataframe(&mut input, raw_offset, &mut links, limits, used)?);
            slot(&mut input, expected_slot)?;
            node_slot = Some(expected_slot);
            if length == 5 {
                transaction_position = input.optional_uint_value()?.map(|v| v.to_string());
            }
            Kind::Transaction
        }
        1 => {
            if length != 4 {
                return Err(CarError::Schema);
            }
            input.uint()?;
            if input.bytes()?.len() != 32 {
                return Err(CarError::Schema);
            }
            read_links(&mut input, Kind::Transaction, &mut links, limits, used)?;
            Kind::Entry
        }
        2 => {
            if length != 6 {
                return Err(CarError::Schema);
            }
            slot(&mut input, expected_slot)?;
            node_slot = Some(expected_slot);
            let shredding = input.array()?;
            if shredding > limits.max_links {
                return Err(CarError::Limit);
            }
            for _ in 0..shredding {
                input.expect(4, 2)?;
                input.shredding_index_i64()?;
                input.shredding_index_i64()?;
            }
            read_links(&mut input, Kind::Entry, &mut links, limits, used)?;
            let metadata = input.array()?;
            if !(2..=3).contains(&metadata) {
                return Err(CarError::Schema);
            }
            input.uint()?;
            input.integer()?;
            if metadata == 3 {
                input.optional_uint()?;
            }
            add_link(&mut input, Kind::Rewards, &mut links, limits, used)?;
            Kind::Block
        }
        5 => {
            if length != 3 {
                return Err(CarError::Schema);
            }
            slot(&mut input, expected_slot)?;
            node_slot = Some(expected_slot);
            frames.push(dataframe(&mut input, raw_offset, &mut links, limits, used)?);
            Kind::Rewards
        }
        6 => {
            frames.push(dataframe_fields(
                &mut input, raw_offset, 0, length, &mut links, limits, used,
            )?);
            Kind::DataFrame
        }
        _ => return Err(CarError::UnsupportedNode),
    };
    input.finish()?;
    Ok(Node {
        kind,
        links,
        slot: node_slot,
        transaction_position,
        frames,
    })
}

fn slot(input: &mut Cursor<'_>, expected: u64) -> CarResult<()> {
    if input.uint()? != expected {
        return Err(CarError::Slot);
    }
    Ok(())
}

fn dataframe(
    input: &mut Cursor<'_>,
    raw_offset: usize,
    links: &mut Vec<Link>,
    limits: VerificationLimits,
    used: &mut usize,
) -> CarResult<DataFrameFact> {
    let start = input.position;
    let length = input.array()?;
    input.expect(0, 6)?;
    dataframe_fields(input, raw_offset, start, length, links, limits, used)
}

fn dataframe_fields(
    input: &mut Cursor<'_>,
    raw_offset: usize,
    start: usize,
    length: usize,
    links: &mut Vec<Link>,
    limits: VerificationLimits,
    used: &mut usize,
) -> CarResult<DataFrameFact> {
    if !(5..=6).contains(&length) {
        return Err(CarError::Schema);
    }
    let checksum = if input.null()? {
        None
    } else {
        Some(input.integer_text()?)
    };
    let frame_index = input.optional_uint_value()?.map(|v| v.to_string());
    let total = input.optional_uint_value()?.map(|v| v.to_string());
    let data_length = input.bytes()?.len(); // No decompression, CRC or wire decode.
    let data_offset = input.position - data_length;
    let link_start = links.len();
    if length == 6 && !input.null()? {
        read_links(input, Kind::DataFrame, links, limits, used)?;
    }
    Ok(DataFrameFact {
        raw_cbor_span: ByteSpan {
            offset: raw_offset.checked_add(start).ok_or(CarError::Limit)?,
            length: input.position - start,
        },
        checksum,
        frame_index,
        total,
        data_span: ByteSpan {
            offset: raw_offset.checked_add(data_offset).ok_or(CarError::Limit)?,
            length: data_length,
        },
        next_cid_hex: links[link_start..]
            .iter()
            .map(|link| hex::encode(link.identity))
            .collect(),
        payload_and_checksum: "NOT_EVALUATED",
    })
}

fn read_links(
    input: &mut Cursor<'_>,
    kind: Kind,
    links: &mut Vec<Link>,
    limits: VerificationLimits,
    used: &mut usize,
) -> CarResult<()> {
    let length = input.array()?;
    if length > limits.max_links.saturating_sub(*used) {
        return Err(CarError::Limit);
    }
    for _ in 0..length {
        add_link(input, kind, links, limits, used)?;
    }
    Ok(())
}

fn add_link(
    input: &mut Cursor<'_>,
    kind: Kind,
    links: &mut Vec<Link>,
    limits: VerificationLimits,
    used: &mut usize,
) -> CarResult<()> {
    if *used >= limits.max_links {
        return Err(CarError::Limit);
    }
    links.push(Link {
        identity: input.link()?,
        kind,
    });
    *used += 1;
    Ok(())
}

fn verify_graph(nodes: &[Node], by_cid: &BTreeMap<Cid, usize>, root: usize) -> CarResult<()> {
    // Iterative three-colour DFS: bounded by captured nodes/links, never recursive or I/O.
    let mut colours = vec![0_u8; nodes.len()];
    let mut stack = vec![(root, 0_usize)];
    colours[root] = 1;
    while let Some((index, cursor)) = stack.last_mut() {
        if *cursor == nodes[*index].links.len() {
            colours[*index] = 2;
            stack.pop();
            continue;
        }
        let link = &nodes[*index].links[*cursor];
        *cursor += 1;
        let target = *by_cid.get(&link.identity).ok_or(CarError::MissingLink)?;
        if nodes[target].kind != link.kind {
            return Err(CarError::LinkKind);
        }
        match colours[target] {
            1 => return Err(CarError::Cycle),
            0 => {
                colours[target] = 1;
                stack.push((target, 0));
            }
            _ => {}
        }
    }
    if colours.contains(&0) {
        return Err(CarError::Unreachable);
    }
    Ok(())
}

struct Cursor<'a> {
    bytes: &'a [u8],
    position: usize,
}
impl<'a> Cursor<'a> {
    const fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, position: 0 }
    }
    fn take(&mut self, length: usize) -> CarResult<&'a [u8]> {
        let end = self.position.checked_add(length).ok_or(CarError::Limit)?;
        let value = self
            .bytes
            .get(self.position..end)
            .ok_or(CarError::Truncated)?;
        self.position = end;
        Ok(value)
    }
    fn varint(&mut self) -> CarResult<u64> {
        let mut value = 0_u64;
        for index in 0..10 {
            let byte = self.take(1)?[0];
            if index == 9 && byte > 1 {
                return Err(CarError::Varint);
            }
            value |= u64::from(byte & 127) << (index * 7);
            if byte & 128 == 0 {
                if index > 0 && byte == 0 {
                    return Err(CarError::Varint);
                }
                return Ok(value);
            }
        }
        Err(CarError::Varint)
    }
    fn head(&mut self) -> CarResult<(u8, u64)> {
        let byte = self.take(1)?[0];
        let value = match byte & 31 {
            value @ 0..=23 => u64::from(value),
            info @ 24..=27 => {
                let length = 1_usize << (info - 24);
                let mut value = 0_u64;
                for byte in self.take(length)? {
                    value = (value << 8) | u64::from(*byte);
                }
                let minimum = match info {
                    24 => 24,
                    25 => 256,
                    26 => 65536,
                    _ => 4_294_967_296,
                };
                if value < minimum {
                    return Err(CarError::Schema);
                }
                value
            }
            _ => return Err(CarError::Schema),
        };
        Ok((byte >> 5, value))
    }
    fn expect(&mut self, major: u8, value: u64) -> CarResult<()> {
        if self.head()? != (major, value) {
            return Err(CarError::Schema);
        }
        Ok(())
    }
    fn uint(&mut self) -> CarResult<u64> {
        let (major, value) = self.head()?;
        if major != 0 {
            return Err(CarError::Schema);
        }
        Ok(value)
    }
    fn integer(&mut self) -> CarResult<()> {
        if self.head()?.0 > 1 {
            return Err(CarError::Schema);
        }
        Ok(())
    }
    fn integer_text(&mut self) -> CarResult<String> {
        let (major, value) = self.head()?;
        match major {
            0 => Ok(value.to_string()),
            // CBOR negative integer is -1 - value; i128 retains the entire CBOR range.
            1 => Ok((-1_i128 - i128::from(value)).to_string()),
            _ => Err(CarError::Schema),
        }
    }
    /// Only `Block.shredding.{entry_end_idx,shred_end_idx}`: pinned `block.rs`
    /// declares both i64. Preserve signed values; do not import upstream's
    /// unchecked casts, default values or invent meaning for negative indexes.
    fn shredding_index_i64(&mut self) -> CarResult<i64> {
        let (major, magnitude) = self.head()?;
        let magnitude = i64::try_from(magnitude).map_err(|_| CarError::Schema)?;
        match major {
            0 => Ok(magnitude),
            1 => Ok(-1 - magnitude),
            _ => Err(CarError::Schema),
        }
    }
    fn null(&mut self) -> CarResult<bool> {
        if self.bytes.get(self.position) == Some(&0xf6) {
            self.take(1)?;
            return Ok(true);
        }
        Ok(false)
    }
    fn optional_uint(&mut self) -> CarResult<()> {
        if !self.null()? {
            self.uint()?;
        }
        Ok(())
    }
    fn optional_uint_value(&mut self) -> CarResult<Option<u64>> {
        if self.null()? {
            Ok(None)
        } else {
            self.uint().map(Some)
        }
    }
    fn array(&mut self) -> CarResult<usize> {
        let (major, length) = self.head()?;
        if major != 4 {
            return Err(CarError::Schema);
        }
        usize::try_from(length).map_err(|_| CarError::Limit)
    }
    fn bytes(&mut self) -> CarResult<&'a [u8]> {
        let (major, length) = self.head()?;
        if major != 2 {
            return Err(CarError::Schema);
        }
        self.take(usize::try_from(length).map_err(|_| CarError::Limit)?)
    }
    fn text(&mut self) -> CarResult<&'a str> {
        let (major, length) = self.head()?;
        if major != 3 {
            return Err(CarError::Schema);
        }
        std::str::from_utf8(self.take(usize::try_from(length).map_err(|_| CarError::Limit)?)?)
            .map_err(|_| CarError::Schema)
    }
    fn link(&mut self) -> CarResult<Cid> {
        self.expect(6, 42)?;
        let bytes = self.bytes()?;
        if bytes.first() != Some(&0) {
            return Err(CarError::UnsupportedCid);
        }
        cid(&bytes[1..])
    }
    fn finish(&self) -> CarResult<()> {
        if self.position != self.bytes.len() {
            return Err(CarError::Schema);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn authentic_range() -> Vec<u8> {
        let encoded = include_str!(
            "../../../schemas/acquisition/of1/epoch-978-slot-422496000.observed.car.hex"
        );
        hex::decode(encoded.split_whitespace().collect::<String>()).unwrap()
    }

    #[test]
    fn signed_shredding_values_are_i64_without_sentinel_normalization() {
        for (bytes, expected) in [
            (vec![0x20], -1),
            (vec![0x00], 0),
            (
                vec![0x1b, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
                i64::MAX,
            ),
            (
                vec![0x3b, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
                i64::MIN,
            ),
        ] {
            let mut input = Cursor::new(&bytes);
            assert_eq!(input.shredding_index_i64(), Ok(expected));
            assert_eq!(input.finish(), Ok(()));
        }
    }

    #[test]
    fn authentic_shredding_reproduces_the_old_unsigned_call_failure() {
        let raw = authentic_range();
        let mut input = Cursor::new(&raw);
        let mut terminal = &[][..];
        while input.position < raw.len() {
            let length = usize::try_from(input.varint().unwrap()).unwrap();
            let section = input.take(length).unwrap();
            terminal = &section[36..];
        }
        let mut block = Cursor::new(terminal);
        block.expect(4, 6).unwrap();
        block.expect(0, 2).unwrap();
        assert_eq!(block.uint(), Ok(422_496_000));
        let pairs = block.array().unwrap();
        assert_eq!(pairs, 64);
        let mut negative_values = Vec::new();
        for pair in 0..pairs {
            block.expect(4, 2).unwrap();
            for position in 0..2 {
                // Exact former call at the same authentic byte position. No
                // expected byte or CID is rewritten to manufacture acceptance.
                let old_result = Cursor::new(&terminal[block.position..]).uint();
                let corrected_value = block.shredding_index_i64().unwrap();
                if corrected_value < 0 {
                    assert_eq!(old_result, Err(CarError::Schema));
                    negative_values.push((pair, position, corrected_value));
                } else {
                    assert_eq!(old_result, Ok(u64::try_from(corrected_value).unwrap()));
                }
            }
        }
        assert_eq!(
            negative_values,
            (0..63).map(|pair| (pair, 1, -1)).collect::<Vec<_>>()
        );
    }

    #[test]
    fn semantic_cycle_guard_is_exercised_without_claiming_a_cryptographic_fixed_point() {
        let identity = [0_u8; 36];
        let nodes = [Node {
            kind: Kind::DataFrame,
            slot: None,
            transaction_position: None,
            frames: vec![],
            links: vec![Link {
                identity,
                kind: Kind::DataFrame,
            }],
        }];
        let by_cid = BTreeMap::from([(identity, 0)]);
        assert_eq!(verify_graph(&nodes, &by_cid, 0), Err(CarError::Cycle));
    }
}
