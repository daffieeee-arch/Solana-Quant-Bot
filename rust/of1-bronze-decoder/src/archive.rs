//! Source-shaped projection AFTER the retained canonical CAR/CID/slot/graph gate.
//! PR #111's useful link-order/provenance distinction is retained, not its larger limits.
use crate::invalid;
use of1_range_recorder::car::{VerificationLimits, verify_slot_sections};
use serde::{Deserialize, Serialize};
use serde_cbor::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    io,
};

pub const MAX_SLOT_BYTES: usize = 16_777_216;
pub const MAX_FRAME_BYTES: usize = 2_097_152;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Frame {
    pub checksum: Option<u64>,
    pub index: Option<u64>,
    pub total: Option<u64>,
    pub bytes: Vec<u8>,
    pub next: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Envelope {
    pub cid_hex: String,
    pub physical_node_index: usize,
    pub raw_offset: usize,
    pub raw_length: usize,
    pub entry_index: usize,
    pub transaction_index_in_entry: usize,
    pub transaction_index_in_slot: usize,
    pub source_transaction_index: Option<u64>,
    pub data: Frame,
    pub metadata: Frame,
}

pub struct Archive {
    pub envelopes: Vec<Envelope>,
    pub continuations: BTreeMap<String, Frame>,
    pub entries: usize,
    pub verified_nodes: usize,
    pub verified_links: usize,
}

fn array(v: &Value) -> io::Result<&[Value]> {
    if let Value::Array(a) = v {
        Ok(a)
    } else {
        Err(invalid("ARCHIVAL_ARRAY"))
    }
}
fn uint(v: &Value) -> io::Result<u64> {
    if let Value::Integer(n) = v {
        u64::try_from(*n).map_err(invalid)
    } else {
        Err(invalid("ARCHIVAL_UINT"))
    }
}
fn optional(v: &Value) -> io::Result<Option<u64>> {
    if *v == Value::Null {
        Ok(None)
    } else {
        uint(v).map(Some)
    }
}
fn bytes(v: &Value) -> io::Result<&[u8]> {
    if let Value::Bytes(b) = v {
        Ok(b)
    } else {
        Err(invalid("ARCHIVAL_BYTES"))
    }
}
fn link(v: &Value) -> io::Result<String> {
    let value = if let Value::Tag(42, v) = v {
        v.as_ref()
    } else {
        v
    };
    let b = bytes(value)?;
    if b.len() != 37 || b[0] != 0 {
        return Err(invalid("ARCHIVAL_LINK"));
    }
    Ok(hex::encode(&b[1..]))
}
fn links(v: &Value) -> io::Result<Vec<String>> {
    array(v)?.iter().map(link).collect()
}

/// Signed source checksum integers encode a u64 bit pattern, NOT a signed quantity.
/// Upstream dataframe.rs casts i128 to u64; here only i64-negative/u64-positive fit.
/// # Errors
/// Rejects malformed shapes, out-of-domain checksums or oversized inline data.
pub fn frame(v: &Value) -> io::Result<Frame> {
    let a = array(v)?;
    if !(5..=6).contains(&a.len()) || uint(&a[0])? != 6 {
        return Err(invalid("DATAFRAME_SHAPE"));
    }
    let checksum = match &a[1] {
        Value::Null => None,
        Value::Integer(n) if *n < 0 => Some(u64::from_ne_bytes(
            i64::try_from(*n).map_err(invalid)?.to_ne_bytes(),
        )),
        Value::Integer(n) => Some(u64::try_from(*n).map_err(invalid)?),
        _ => return Err(invalid("DATAFRAME_CHECKSUM_TYPE")),
    };
    let b = bytes(&a[4])?;
    if b.len() > MAX_FRAME_BYTES {
        return Err(invalid("DATAFRAME_BYTE_LIMIT"));
    }
    let next = if a.len() == 6 && a[5] != Value::Null {
        links(&a[5])?
    } else {
        vec![]
    };
    Ok(Frame {
        checksum,
        index: optional(&a[2])?,
        total: optional(&a[3])?,
        bytes: b.to_vec(),
        next,
    })
}

fn varint(raw: &[u8], pos: &mut usize) -> io::Result<usize> {
    let mut n = 0_u64;
    for shift in 0..10 {
        let b = *raw.get(*pos).ok_or_else(|| invalid("CAR_TRUNCATED"))?;
        *pos += 1;
        if shift == 9 && b > 1 {
            return Err(invalid("CAR_VARINT"));
        }
        n |= u64::from(b & 127) << (7 * shift);
        if b & 128 == 0 {
            return usize::try_from(n).map_err(invalid);
        }
    }
    Err(invalid("CAR_VARINT"))
}

/// One complete selected slot; no I/O, callbacks or domain defaults.
/// # Errors
/// Fails the slot on CAR/graph/order ambiguity rather than omitting an envelope.
pub fn inspect(slot: u64, raw: &[u8]) -> io::Result<Archive> {
    let verified = verify_slot_sections(
        slot,
        raw,
        VerificationLimits {
            max_total_bytes: MAX_SLOT_BYTES,
            max_section_bytes: MAX_SLOT_BYTES,
            max_nodes: 4096,
            max_links: 16384,
        },
    )
    .map_err(invalid)?;
    let mut nodes = BTreeMap::new();
    let mut physical = Vec::new();
    let mut pos = 0;
    while pos < raw.len() {
        let start = pos;
        let size = varint(raw, &mut pos)?;
        let end = pos.checked_add(size).ok_or_else(|| invalid("CAR_LIMIT"))?;
        let section = raw.get(pos..end).ok_or_else(|| invalid("CAR_TRUNCATED"))?;
        let cid = hex::encode(&section[..36]);
        let v: Value = serde_cbor::from_slice(&section[36..]).map_err(invalid)?;
        nodes.insert(cid.clone(), (physical.len(), start, end - start, v));
        physical.push(cid);
        pos = end;
    }
    let terminal = physical.last().ok_or_else(|| invalid("CAR_BLOCK"))?;
    let block = array(&nodes[terminal].3)?;
    let entry_cids = links(&block[3])?;
    let mut envelopes = Vec::new();
    let mut seen = BTreeSet::new();
    let mut seen_entries = BTreeSet::new();
    for (entry_index, entry_cid) in entry_cids.iter().enumerate() {
        if !seen_entries.insert(entry_cid) {
            return Err(invalid("DUPLICATE_ENTRY_REFERENCE"));
        }
        let entry = array(&nodes[entry_cid].3)?;
        for (within, tx_cid) in links(&entry[3])?.iter().enumerate() {
            if !seen.insert(tx_cid.clone()) {
                return Err(invalid("DUPLICATE_TRANSACTION_REFERENCE"));
            }
            let (physical_node_index, raw_offset, raw_length, v) = &nodes[tx_cid];
            let tx = array(v)?;
            let source_index = if tx.len() == 5 {
                optional(&tx[4])?
            } else {
                None
            };
            if source_index.is_some_and(|n| n != envelopes.len() as u64) {
                return Err(invalid("TRANSACTION_INDEX_DISAGREEMENT"));
            }
            envelopes.push(Envelope {
                cid_hex: tx_cid.clone(),
                physical_node_index: *physical_node_index,
                raw_offset: *raw_offset,
                raw_length: *raw_length,
                entry_index,
                transaction_index_in_entry: within,
                transaction_index_in_slot: envelopes.len(),
                source_transaction_index: source_index,
                data: frame(&tx[1])?,
                metadata: frame(&tx[2])?,
            });
        }
    }
    if envelopes.len() != verified.archival_node_counts.transaction
        || entry_cids.len() != verified.archival_node_counts.entry
    {
        return Err(invalid("ARCHIVAL_ORDER_COVERAGE"));
    }
    let mut continuations = BTreeMap::new();
    for (cid, (_, _, _, v)) in &nodes {
        if uint(&array(v)?[0])? == 6 {
            continuations.insert(cid.clone(), frame(v)?);
        }
    }
    Ok(Archive {
        envelopes,
        continuations,
        entries: entry_cids.len(),
        verified_nodes: verified.verified_nodes,
        verified_links: verified.verified_links,
    })
}

/// Assemble the pinned node.rs next-list order; non-last sibling branches are
/// explicitly unsupported (upstream silently ignores those branches).
/// # Errors
/// Rejects missing/duplicate frames, inconsistent indexes/counts and amplification.
pub fn assemble(first: &Frame, rest: &BTreeMap<String, Frame>) -> io::Result<Vec<u8>> {
    if first.bytes.len() > MAX_FRAME_BYTES {
        return Err(invalid("DATAFRAME_BYTE_LIMIT"));
    }
    let mut output = first.bytes.clone();
    let mut frames = vec![first];
    let mut next = &first.next;
    let mut seen = BTreeSet::new();
    while !next.is_empty() {
        let mut following = None;
        for (i, cid) in next.iter().enumerate() {
            if !seen.insert(cid) {
                return Err(invalid("DATAFRAME_DUPLICATE_OR_CYCLE"));
            }
            let f = rest.get(cid).ok_or_else(|| invalid("MISSING_DATAFRAME"))?;
            if i + 1 < next.len() && !f.next.is_empty() {
                return Err(invalid("UNSUPPORTED_DATAFRAME_BRANCH"));
            }
            if output
                .len()
                .checked_add(f.bytes.len())
                .is_none_or(|n| n > MAX_FRAME_BYTES)
            {
                return Err(invalid("DATAFRAME_BYTE_LIMIT"));
            }
            output.extend_from_slice(&f.bytes);
            frames.push(f);
            following = Some(&f.next);
        }
        next = following.ok_or_else(|| invalid("DATAFRAME_NEXT"))?;
    }
    for (index, f) in frames.iter().enumerate() {
        if f.index.is_some_and(|n| n != index as u64)
            || f.total.is_some_and(|n| n != frames.len() as u64)
        {
            return Err(invalid("DATAFRAME_INDEX_TOTAL"));
        }
        if f.checksum.is_some() && first.checksum.is_some() && f.checksum != first.checksum {
            return Err(invalid("DATAFRAME_CHECKSUM_DECLARATION"));
        }
    }
    Ok(output)
}
