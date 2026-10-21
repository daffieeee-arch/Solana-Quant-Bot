//! Generic Anchor IDL event decoder.
//!
//! Event layouts come straight from the official IDL JSON files in `idl/` (vendored from
//! `pump-fun/pump-public-docs`), so no layout is written by hand. Pump only ever appends fields
//! to its events, so an older on-chain event is a strict prefix of the current IDL layout: we
//! decode field by field and mark the missing tail as null (`DecodeStatus::Prefix`).

use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value as J};
#[cfg(test)]
use sha2::{Digest, Sha256};
use std::collections::HashMap;

/// Anchor `emit_cpi!` prefix: the self-CPI instruction data starts with this tag, followed by
/// the 8-byte event discriminator and the Borsh-encoded event.
/// Anchor defines it as the u64 `0x1d9acb512ea545e4` (= `sha256("anchor:event")[..8]` read big-endian)
/// and writes it little-endian.
pub const EVENT_IX_TAG: [u8; 8] = [0xe4, 0x45, 0xa5, 0x2e, 0x51, 0xcb, 0x9a, 0x1d];

#[derive(Debug, Clone, PartialEq)]
pub enum Ty {
    Bool,
    U8,
    U16,
    U32,
    U64,
    U128,
    I8,
    I16,
    I32,
    I64,
    I128,
    // IEEE floats (Meteora DAMM v1's IDL has f64 fields): decoded to JSON numbers, as text columns.
    F32,
    F64,
    Pubkey,
    String,
    Bytes,
    Option(Box<Ty>),
    Vec(Box<Ty>),
    Array(Box<Ty>, usize),
    Defined(String),
}

#[derive(Debug, Clone)]
pub struct Field {
    pub name: String,
    pub ty: Ty,
}

#[derive(Debug, Clone)]
enum VariantFields {
    Unit,
    Named(Vec<Field>),
    Tuple(Vec<Ty>),
}

#[derive(Debug, Clone)]
enum TypeDef {
    Struct(Vec<Field>),
    Enum(Vec<(String, VariantFields)>),
}

#[derive(Debug, Clone)]
pub struct InstructionDef {
    pub name: String,
    pub discriminator: [u8; 8],
    pub args: Vec<Field>,
    /// Account names in the order the instruction takes them (nested groups flattened).
    pub accounts: Vec<String>,
}

/// Instruction accounts that also get their own column, where the variant has them.
pub const FIXED_ACCOUNTS: [&str; 5] = ["pool", "user", "global_config", "base_mint", "quote_mint"];

/// An instruction's accounts by IDL name.
#[derive(Debug, Clone, PartialEq)]
pub struct NamedAccounts {
    /// JSON object {name: key}. Keys beyond the IDL list (remaining accounts) are under "_extra";
    /// "_missing" counts IDL names without a key when the instruction has fewer accounts.
    pub json: String,
    /// The FIXED_ACCOUNTS in order; None where the variant has no account of that name.
    pub fixed: [Option<String>; 5],
}

#[derive(Debug, Clone)]
pub struct EventDef {
    pub name: String,
    pub discriminator: [u8; 8],
    pub fields: Vec<Field>,
}

/// Column kind used when an event field is written to Parquet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Bool,
    U64,
    I64,
    Utf8,
}

/// One decoded cell. 128-bit integers are kept as decimal strings and nested values as JSON,
/// so decoding stays lossless.
#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    Null,
    Bool(bool),
    U64(u64),
    I64(i64),
    Str(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeStatus {
    /// Every IDL field decoded and no bytes were left over.
    Ok,
    /// The payload ended exactly on a field boundary before the last IDL field (older layout).
    Prefix,
    /// Every IDL field decoded but bytes were left over (newer layout than the vendored IDL).
    Extra,
    /// The payload ended in the middle of a field.
    Error,
}

impl DecodeStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            DecodeStatus::Ok => "ok",
            DecodeStatus::Prefix => "prefix",
            DecodeStatus::Extra => "extra",
            DecodeStatus::Error => "error",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Decoded {
    pub values: Vec<Val>,
    pub status: DecodeStatus,
    #[cfg_attr(not(test), allow(dead_code))]
    pub fields_present: usize,
}

#[derive(Debug, Clone)]
pub struct Idl {
    pub address: String,
    pub events: Vec<EventDef>,
    pub instructions: Vec<InstructionDef>,
    instruction_by_disc: HashMap<[u8; 8], usize>,
    types: HashMap<String, TypeDef>,
}

#[cfg(test)]
pub fn event_discriminator(name: &str) -> [u8; 8] {
    let digest = Sha256::digest(format!("event:{name}").as_bytes());
    let mut out = [0u8; 8];
    out.copy_from_slice(&digest[..8]);
    out
}

fn parse_ty(j: &J) -> Result<Ty> {
    if let Some(s) = j.as_str() {
        return Ok(match s {
            "bool" => Ty::Bool,
            "u8" => Ty::U8,
            "u16" => Ty::U16,
            "u32" => Ty::U32,
            "u64" => Ty::U64,
            "u128" => Ty::U128,
            "i8" => Ty::I8,
            "i16" => Ty::I16,
            "i32" => Ty::I32,
            "i64" => Ty::I64,
            "i128" => Ty::I128,
            "f32" => Ty::F32,
            "f64" => Ty::F64,
            "pubkey" | "publicKey" => Ty::Pubkey,
            "string" => Ty::String,
            "bytes" => Ty::Bytes,
            other => bail!("unsupported IDL type {other}"),
        });
    }
    let obj = j.as_object().ok_or_else(|| anyhow!("bad IDL type {j}"))?;
    if let Some(inner) = obj.get("vec") {
        return Ok(Ty::Vec(Box::new(parse_ty(inner)?)));
    }
    if let Some(inner) = obj.get("option") {
        return Ok(Ty::Option(Box::new(parse_ty(inner)?)));
    }
    if let Some(arr) = obj.get("array") {
        let a = arr.as_array().ok_or_else(|| anyhow!("bad array type {j}"))?;
        let len = a
            .get(1)
            .and_then(J::as_u64)
            .ok_or_else(|| anyhow!("array without literal length {j}"))?;
        return Ok(Ty::Array(Box::new(parse_ty(&a[0])?), usize::try_from(len)?));
    }
    if let Some(def) = obj.get("defined") {
        let name = def
            .as_str()
            .or_else(|| def.get("name").and_then(J::as_str))
            .ok_or_else(|| anyhow!("bad defined type {j}"))?;
        return Ok(Ty::Defined(name.to_owned()));
    }
    bail!("unsupported IDL type {j}")
}

/// Account names of an instruction in order, nested account groups flattened.
fn account_names(j: &J) -> Vec<String> {
    let mut out = Vec::new();
    for a in j.as_array().map(Vec::as_slice).unwrap_or(&[]) {
        match a.get("accounts") {
            Some(inner) => out.extend(account_names(inner)),
            None => out.push(a["name"].as_str().unwrap_or("?").to_owned()),
        }
    }
    out
}

/// Named fields: every element is an object with a "name" (`{"name": .., "type": ..}`). Otherwise the fields are a
/// tuple of types, which can be objects themselves (`[{"defined": {"name": "X"}}]`: Raydium LaunchLab's
/// PlatformConfigParam variants, Orca's DynamicTick::Initialized).
fn named_fields(j: &J) -> bool {
    j.as_array().is_some_and(|a| a.iter().all(|f| f.is_object() && f.get("name").is_some_and(J::is_string)))
}

fn parse_fields(j: &J) -> Result<Vec<Field>> {
    j.as_array()
        .ok_or_else(|| anyhow!("fields must be an array"))?
        .iter()
        .map(|f| {
            Ok(Field {
                name: f["name"].as_str().ok_or_else(|| anyhow!("field without name"))?.to_owned(),
                ty: parse_ty(&f["type"])?,
            })
        })
        .collect()
}

impl Idl {
    pub fn parse(text: &str) -> Result<Self> {
        let j: J = serde_json::from_str(text).context("IDL is not valid JSON")?;
        let address = j["address"].as_str().ok_or_else(|| anyhow!("IDL without address"))?.to_owned();

        let mut types = HashMap::new();
        for t in j["types"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            let tname = t["name"].as_str().ok_or_else(|| anyhow!("type without name"))?;
            let body = &t["type"];
            let def = match body["kind"].as_str() {
                Some("struct") => TypeDef::Struct(match body.get("fields") {
                    Some(f) if named_fields(f) => parse_fields(f)?,
                    Some(f) if f.is_array() => {
                        // Tuple struct: unnamed fields.
                        f.as_array()
                            .unwrap()
                            .iter()
                            .enumerate()
                            .map(|(i, t)| Ok(Field { name: format!("_{i}"), ty: parse_ty(t)? }))
                            .collect::<Result<_>>()?
                    }
                    _ => Vec::new(),
                }),
                Some("enum") => {
                    let mut variants = Vec::new();
                    for v in body["variants"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
                        let vname = v["name"].as_str().unwrap_or("?").to_owned();
                        let fields = match v.get("fields") {
                            None => VariantFields::Unit,
                            Some(f) if named_fields(f) => {
                                VariantFields::Named(parse_fields(f)?)
                            }
                            Some(f) => VariantFields::Tuple(
                                f.as_array()
                                    .ok_or_else(|| anyhow!("bad variant fields"))?
                                    .iter()
                                    .map(parse_ty)
                                    .collect::<Result<_>>()?,
                            ),
                        };
                        variants.push((vname, fields));
                    }
                    TypeDef::Enum(variants)
                }
                other => bail!("type {tname}: unsupported kind {other:?}"),
            };
            types.insert(tname.to_owned(), def);
        }

        let mut events = Vec::new();
        for e in j["events"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            let ename = e["name"].as_str().ok_or_else(|| anyhow!("event without name"))?.to_owned();
            let disc: Vec<u8> = e["discriminator"]
                .as_array()
                .ok_or_else(|| anyhow!("event {ename} without discriminator"))?
                .iter()
                .map(|b| b.as_u64().and_then(|v| u8::try_from(v).ok()).ok_or_else(|| anyhow!("bad byte")))
                .collect::<Result<_>>()?;
            let discriminator: [u8; 8] = disc.try_into().map_err(|_| anyhow!("event {ename}: discriminator is not 8 bytes"))?;
            let fields = match types.get(&ename) {
                Some(TypeDef::Struct(f)) => f.clone(),
                _ => bail!("event {ename} has no struct type definition"),
            };
            events.push(EventDef { name: ename, discriminator, fields });
        }
        let mut instructions = Vec::new();
        for ix in j["instructions"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            let name = ix["name"].as_str().ok_or_else(|| anyhow!("instruction without name"))?.to_owned();
            let disc: Vec<u8> = ix["discriminator"]
                .as_array()
                .ok_or_else(|| anyhow!("instruction {name} without discriminator"))?
                .iter()
                .map(|b| b.as_u64().and_then(|v| u8::try_from(v).ok()).ok_or_else(|| anyhow!("bad byte")))
                .collect::<Result<_>>()?;
            let Ok(discriminator) = <[u8; 8]>::try_from(disc) else { continue };
            let args = parse_fields(ix.get("args").unwrap_or(&J::Array(Vec::new())))?;
            let accounts = account_names(ix.get("accounts").unwrap_or(&J::Null));
            instructions.push(InstructionDef { name, discriminator, args, accounts });
        }
        let instruction_by_disc = instructions.iter().enumerate().map(|(i, ix)| (ix.discriminator, i)).collect();
        Ok(Self { address, events, instructions, instruction_by_disc, types })
    }

    /// Decode an instruction of this program: its IDL name and its arguments as a JSON object.
    /// Arguments are decoded in order and stop at the end of the data (older layouts had fewer
    /// trailing arguments); `_decode` records ok / prefix / extra / error.
    pub fn decode_instruction(&self, data: &[u8]) -> Option<(&str, String)> {
        let disc: [u8; 8] = data.get(..8)?.try_into().ok()?;
        let ix = &self.instructions[*self.instruction_by_disc.get(&disc)?];
        let mut cur = Cursor { buf: &data[8..], pos: 0 };
        let mut map = serde_json::Map::new();
        let mut status = DecodeStatus::Ok;
        for arg in &ix.args {
            if cur.remaining() == 0 {
                status = DecodeStatus::Prefix;
                break;
            }
            match self.decode_json(&arg.ty, &mut cur, 0) {
                Some(v) => {
                    map.insert(arg.name.clone(), v);
                }
                None => {
                    status = DecodeStatus::Error;
                    break;
                }
            }
        }
        if status == DecodeStatus::Ok && cur.remaining() > 0 {
            status = DecodeStatus::Extra;
        }
        map.insert("_decode".to_owned(), J::String(status.as_str().to_owned()));
        Some((ix.name.as_str(), J::Object(map).to_string()))
    }

    /// The accounts of an instruction of this program by IDL name, from its account keys in
    /// order (None for a key the transaction could not resolve). None when the discriminator is
    /// not in the IDL.
    pub fn named_accounts(&self, data: &[u8], keys: &[Option<String>]) -> Option<NamedAccounts> {
        let disc: [u8; 8] = data.get(..8)?.try_into().ok()?;
        let ix = &self.instructions[*self.instruction_by_disc.get(&disc)?];
        let key_json = |k: &Option<String>| k.as_ref().map_or(J::Null, |k| J::String(k.clone()));
        let mut map = serde_json::Map::new();
        for (name, key) in ix.accounts.iter().zip(keys) {
            map.insert(name.clone(), key_json(key));
        }
        if keys.len() > ix.accounts.len() {
            map.insert("_extra".to_owned(), J::Array(keys[ix.accounts.len()..].iter().map(key_json).collect()));
        } else if keys.len() < ix.accounts.len() {
            map.insert("_missing".to_owned(), J::from(ix.accounts.len() - keys.len()));
        }
        let fixed = FIXED_ACCOUNTS.map(|n| map.get(n).and_then(J::as_str).map(str::to_owned));
        Some(NamedAccounts { json: J::Object(map).to_string(), fixed })
    }

    #[cfg(test)]
    pub fn event(&self, name: &str) -> Option<&EventDef> {
        self.events.iter().find(|e| e.name == name)
    }

    /// Parquet column kind for a top-level event field.
    pub fn kind_of(ty: &Ty) -> Kind {
        match ty {
            Ty::Bool => Kind::Bool,
            Ty::U8 | Ty::U16 | Ty::U32 | Ty::U64 => Kind::U64,
            Ty::I8 | Ty::I16 | Ty::I32 | Ty::I64 => Kind::I64,
            _ => Kind::Utf8,
        }
    }

    /// Decode an event body (the bytes after tag and discriminator) against the IDL layout.
    pub fn decode_event(&self, ev: &EventDef, body: &[u8]) -> Decoded {
        let mut cur = Cursor { buf: body, pos: 0 };
        let mut values = Vec::with_capacity(ev.fields.len());
        let mut status = DecodeStatus::Ok;
        let mut fields_present = 0;
        for field in &ev.fields {
            if status != DecodeStatus::Ok {
                values.push(Val::Null);
                continue;
            }
            if cur.remaining() == 0 {
                status = DecodeStatus::Prefix;
                values.push(Val::Null);
                continue;
            }
            match self.decode_top(&field.ty, &mut cur) {
                Some(v) => {
                    values.push(v);
                    fields_present += 1;
                }
                None => {
                    status = DecodeStatus::Error;
                    values.push(Val::Null);
                }
            }
        }
        if status == DecodeStatus::Ok && cur.remaining() > 0 {
            status = DecodeStatus::Extra;
        }
        if fields_present == 0 {
            // An empty body is not an older layout.
            status = DecodeStatus::Error;
        }
        Decoded { values, status, fields_present }
    }

    fn decode_top(&self, ty: &Ty, cur: &mut Cursor<'_>) -> Option<Val> {
        Some(match ty {
            Ty::Bool => Val::Bool(cur.bool()?),
            Ty::U8 => Val::U64(u64::from(cur.u8()?)),
            Ty::U16 => Val::U64(u64::from(u16::from_le_bytes(cur.array()?))),
            Ty::U32 => Val::U64(u64::from(u32::from_le_bytes(cur.array()?))),
            Ty::U64 => Val::U64(u64::from_le_bytes(cur.array()?)),
            Ty::I8 => Val::I64(i64::from(i8::from_le_bytes(cur.array()?))),
            Ty::I16 => Val::I64(i64::from(i16::from_le_bytes(cur.array()?))),
            Ty::I32 => Val::I64(i64::from(i32::from_le_bytes(cur.array()?))),
            Ty::I64 => Val::I64(i64::from_le_bytes(cur.array()?)),
            Ty::Pubkey => Val::Str(bs58::encode(cur.take(32)?).into_string()),
            Ty::String => Val::Str(cur.string()?),
            other => {
                let j = self.decode_json(other, cur, 0)?;
                match j {
                    J::String(s) => Val::Str(s),
                    j => Val::Str(j.to_string()),
                }
            }
        })
    }

    fn decode_json(&self, ty: &Ty, cur: &mut Cursor<'_>, depth: usize) -> Option<J> {
        if depth > 16 {
            return None;
        }
        Some(match ty {
            Ty::Bool => J::Bool(cur.bool()?),
            Ty::U8 => json!(cur.u8()?),
            Ty::U16 => json!(u16::from_le_bytes(cur.array()?)),
            Ty::U32 => json!(u32::from_le_bytes(cur.array()?)),
            Ty::U64 => json!(u64::from_le_bytes(cur.array()?)),
            Ty::U128 => J::String(u128::from_le_bytes(cur.array()?).to_string()),
            Ty::I8 => json!(i8::from_le_bytes(cur.array()?)),
            Ty::I16 => json!(i16::from_le_bytes(cur.array()?)),
            Ty::I32 => json!(i32::from_le_bytes(cur.array()?)),
            Ty::I64 => json!(i64::from_le_bytes(cur.array()?)),
            Ty::I128 => J::String(i128::from_le_bytes(cur.array()?).to_string()),
            // NaN and infinities are no JSON numbers: null.
            Ty::F32 => serde_json::Number::from_f64(f64::from(f32::from_le_bytes(cur.array()?))).map_or(J::Null, J::Number),
            Ty::F64 => serde_json::Number::from_f64(f64::from_le_bytes(cur.array()?)).map_or(J::Null, J::Number),
            Ty::Pubkey => J::String(bs58::encode(cur.take(32)?).into_string()),
            Ty::String => J::String(cur.string()?),
            Ty::Bytes => {
                let n = cur.len_prefix()?;
                J::String(hex::encode(cur.take(n)?))
            }
            Ty::Option(inner) => match cur.u8()? {
                0 => J::Null,
                1 => self.decode_json(inner, cur, depth + 1)?,
                _ => return None,
            },
            Ty::Vec(inner) => {
                let n = cur.len_prefix()?;
                let mut out = Vec::with_capacity(n.min(cur.remaining()));
                for _ in 0..n {
                    out.push(self.decode_json(inner, cur, depth + 1)?);
                }
                J::Array(out)
            }
            Ty::Array(inner, n) => {
                let mut out = Vec::with_capacity(*n);
                for _ in 0..*n {
                    out.push(self.decode_json(inner, cur, depth + 1)?);
                }
                J::Array(out)
            }
            Ty::Defined(name) => match self.types.get(name)? {
                TypeDef::Struct(fields) => {
                    let mut map = serde_json::Map::new();
                    for f in fields {
                        map.insert(f.name.clone(), self.decode_json(&f.ty, cur, depth + 1)?);
                    }
                    J::Object(map)
                }
                TypeDef::Enum(variants) => {
                    let idx = usize::from(cur.u8()?);
                    let (vname, vfields) = variants.get(idx)?;
                    match vfields {
                        VariantFields::Unit => J::String(vname.clone()),
                        VariantFields::Named(fields) => {
                            let mut map = serde_json::Map::new();
                            for f in fields {
                                map.insert(f.name.clone(), self.decode_json(&f.ty, cur, depth + 1)?);
                            }
                            json!({ vname: J::Object(map) })
                        }
                        VariantFields::Tuple(tys) => {
                            let mut out = Vec::new();
                            for t in tys {
                                out.push(self.decode_json(t, cur, depth + 1)?);
                            }
                            json!({ vname: out })
                        }
                    }
                }
            },
        })
    }
}

struct Cursor<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    fn remaining(&self) -> usize {
        self.buf.len() - self.pos
    }
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        if self.remaining() < n {
            return None;
        }
        let s = &self.buf[self.pos..self.pos + n];
        self.pos += n;
        Some(s)
    }
    fn array<const N: usize>(&mut self) -> Option<[u8; N]> {
        self.take(N).map(|s| s.try_into().expect("length checked"))
    }
    fn u8(&mut self) -> Option<u8> {
        self.take(1).map(|s| s[0])
    }
    /// Borsh bool: only 0 and 1 are valid, anything else means a misaligned layout.
    fn bool(&mut self) -> Option<bool> {
        match self.u8()? {
            0 => Some(false),
            1 => Some(true),
            _ => None,
        }
    }
    fn len_prefix(&mut self) -> Option<usize> {
        let n = usize::try_from(u32::from_le_bytes(self.array()?)).ok()?;
        // A length larger than the remaining bytes can never decode; fail early instead of
        // allocating.
        (n <= self.remaining()).then_some(n)
    }
    fn string(&mut self) -> Option<String> {
        let n = self.len_prefix()?;
        // Borsh strings are valid UTF-8; invalid bytes mean a misaligned layout.
        String::from_utf8(self.take(n)?.to_vec()).ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PUMP: &str = include_str!("../idl/pump.json");
    const PUMP_AMM: &str = include_str!("../idl/pump_amm.json");

    #[test]
    fn idl_discriminators_match_anchor_hash() {
        for text in [PUMP, PUMP_AMM] {
            let idl = Idl::parse(text).unwrap();
            assert!(!idl.events.is_empty());
            for ev in &idl.events {
                assert_eq!(ev.discriminator, event_discriminator(&ev.name), "{}", ev.name);
            }
        }
    }

    #[test]
    fn event_ix_tag_is_anchor_event_hash() {
        let mut h: [u8; 8] = Sha256::digest(b"anchor:event")[..8].try_into().unwrap();
        h.reverse();
        assert_eq!(h, EVENT_IX_TAG);
        assert_eq!(u64::from_le_bytes(EVENT_IX_TAG), 0x1d9a_cb51_2ea5_45e4);
    }

    /// Instruction data that only carries the discriminator (enough to pick the instruction).
    fn ix_data(idl: &Idl, name: &str) -> Vec<u8> {
        idl.instructions.iter().find(|i| i.name == name).unwrap_or_else(|| panic!("no instruction {name}")).discriminator.to_vec()
    }

    /// Account keys in the instruction's IDL order: the known keys by name, a placeholder otherwise.
    fn keys_for(idl: &Idl, name: &str, known: &[(&str, &str)]) -> Vec<Option<String>> {
        let ix = idl.instructions.iter().find(|i| i.name == name).unwrap();
        ix.accounts
            .iter()
            .enumerate()
            .map(|(i, a)| Some(known.iter().find(|(n, _)| *n == a.as_str()).map_or(format!("other{i}"), |(_, k)| (*k).to_owned())))
            .collect()
    }

    // Keys from the dataset: a PumpSwap buy (slot 451000002, tx 301; pool, user, the pool's mints)
    // and a pump.fun buy_v2 (slot 451000049, tx 719; mint and user). The IDL gives no fixed address
    // for global_config, so that key is a placeholder.
    const AMM_POOL: &str = "2DeW4HVXmwmQonkBHMnA3ujGJUmsgh5fWT8KPLsS5DWA";
    const AMM_USER: &str = "7GoKr1AsbNySih44zWpMJVfv911dnHbQGBfPCnCMQP3u";
    const AMM_MINT: &str = "8TC93eg35bVJShh7CAcoXNhDCKWnmsdLN39z9Ywkpump";
    const WSOL: &str = "So11111111111111111111111111111111111111112";
    const CURVE_MINT: &str = "2c7AzoxqEF8o9wuTJwdGrhhbqEa32FtdRybEWaxEpump";
    const CURVE_USER: &str = "HgEM6iXuf5Pt2yHh1cuN8MoziC4W1FVKvoqe4H9aww7T";
    const GLOBAL_CONFIG: &str = "global-config-key";

    #[test]
    fn instruction_accounts_follow_the_idl_order() {
        for text in [PUMP, PUMP_AMM] {
            let idl = Idl::parse(text).unwrap();
            let j: J = serde_json::from_str(text).unwrap();
            for ix in j["instructions"].as_array().unwrap() {
                let Some(def) = idl.instructions.iter().find(|d| d.name == ix["name"].as_str().unwrap()) else { continue };
                let names: Vec<&str> =
                    ix["accounts"].as_array().map(Vec::as_slice).unwrap_or(&[]).iter().map(|a| a["name"].as_str().unwrap()).collect();
                assert_eq!(def.accounts, names, "{}", def.name);
            }
        }
    }

    /// Account positions differ per variant (e.g. deposit has global_config before user, the sweeps
    /// have the pool third, pump v2/v3 put the mints right after global); mapping is by name.
    #[test]
    fn named_accounts_per_variant() {
        let amm = Idl::parse(PUMP_AMM).unwrap();
        let known = [("pool", AMM_POOL), ("user", AMM_USER), ("global_config", GLOBAL_CONFIG), ("base_mint", AMM_MINT), ("quote_mint", WSOL)];
        let all = [Some(AMM_POOL), Some(AMM_USER), Some(GLOBAL_CONFIG), Some(AMM_MINT), Some(WSOL)];
        let no_user = [Some(AMM_POOL), None, Some(GLOBAL_CONFIG), Some(AMM_MINT), Some(WSOL)];
        let sweep = [Some(AMM_POOL), None, Some(GLOBAL_CONFIG), None, Some(WSOL)];
        for (name, expect) in [
            ("buy", all),
            ("buy_v2", all),
            ("buy_exact_quote_in", all),
            ("buy_exact_quote_in_v2", all),
            ("sell", all),
            ("sell_v2", all),
            ("deposit", all),
            ("withdraw", all),
            ("boost_buy_and_burn", no_user),
            ("create_pool", no_user),
            ("sweep_protocol_fee", sweep),
            ("sweep_creator_fee", sweep),
        ] {
            let n = amm.named_accounts(&ix_data(&amm, name), &keys_for(&amm, name, &known)).unwrap();
            assert_eq!(n.fixed, expect.map(|k| k.map(str::to_owned)), "pump_amm {name}");
            let j: J = serde_json::from_str(&n.json).unwrap();
            assert_eq!(j["pool"], AMM_POOL, "pump_amm {name}");
            assert!(j.get("_extra").is_none() && j.get("_missing").is_none(), "pump_amm {name}");
        }
        let pump = Idl::parse(PUMP).unwrap();
        let known = [("user", CURVE_USER), ("base_mint", CURVE_MINT), ("mint", CURVE_MINT), ("quote_mint", WSOL)];
        let v1 = [None, Some(CURVE_USER), None, None, None];
        let v23 = [None, Some(CURVE_USER), None, Some(CURVE_MINT), Some(WSOL)];
        let sweep = [None, None, None, Some(CURVE_MINT), Some(WSOL)];
        for (name, expect) in [
            ("buy", v1),
            ("sell", v1),
            ("buy_exact_sol_in", v1),
            ("buy_v2", v23),
            ("sell_v2", v23),
            ("buy_exact_quote_in_v2", v23),
            ("buy_v3", v23),
            ("sell_v3", v23),
            ("buy_exact_quote_in_v3", v23),
            ("sweep_protocol_fee", sweep),
            ("sweep_creator_fee", sweep),
        ] {
            let n = pump.named_accounts(&ix_data(&pump, name), &keys_for(&pump, name, &known)).unwrap();
            assert_eq!(n.fixed, expect.map(|k| k.map(str::to_owned)), "pump {name}");
            let j: J = serde_json::from_str(&n.json).unwrap();
            let mint = if expect[3].is_some() { &j["base_mint"] } else { &j["mint"] };
            assert_eq!(mint, CURVE_MINT, "pump {name}");
        }
    }

    #[test]
    fn named_accounts_extra_missing_unresolved_and_unknown() {
        let amm = Idl::parse(PUMP_AMM).unwrap();
        let data = ix_data(&amm, "buy");
        let mut keys = keys_for(&amm, "buy", &[("pool", AMM_POOL)]);
        keys.push(Some("remaining-1".to_owned()));
        let j: J = serde_json::from_str(&amm.named_accounts(&data, &keys).unwrap().json).unwrap();
        assert_eq!(j["_extra"], json!(["remaining-1"]));
        keys.truncate(3);
        let n = amm.named_accounts(&data, &keys).unwrap();
        let j: J = serde_json::from_str(&n.json).unwrap();
        assert_eq!(j["_missing"], json!(20));
        assert_eq!(n.fixed[3], None); // base_mint is the fourth account
        keys[1] = None; // a key the transaction could not resolve
        let n = amm.named_accounts(&data, &keys).unwrap();
        assert_eq!(n.fixed[1], None);
        assert_eq!(serde_json::from_str::<J>(&n.json).unwrap()["user"], J::Null);
        assert!(amm.named_accounts(&[0u8; 8], &keys).is_none());
        assert!(amm.named_accounts(&[1, 2], &keys).is_none());
    }

    #[test]
    fn program_addresses() {
        assert_eq!(Idl::parse(PUMP).unwrap().address, "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");
        assert_eq!(Idl::parse(PUMP_AMM).unwrap().address, "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
    }

    /// Borsh-encode a TradeEvent with the first `n_fields` fields of the current IDL.
    fn encode_trade_prefix(idl: &Idl, n_fields: usize) -> Vec<u8> {
        let ev = idl.event("TradeEvent").unwrap();
        let mut out = Vec::new();
        for (i, f) in ev.fields.iter().take(n_fields).enumerate() {
            let i = i as u64;
            match &f.ty {
                Ty::Pubkey => out.extend_from_slice(&[i as u8; 32]),
                Ty::U64 => out.extend_from_slice(&(1000 + i).to_le_bytes()),
                Ty::I64 => out.extend_from_slice(&(-(i as i64)).to_le_bytes()),
                Ty::Bool => out.push(1),
                Ty::String => {
                    out.extend_from_slice(&3u32.to_le_bytes());
                    out.extend_from_slice(b"buy");
                }
                Ty::Vec(_) => {
                    // one Shareholder { address, share_bps: u16 }
                    out.extend_from_slice(&1u32.to_le_bytes());
                    out.extend_from_slice(&[9u8; 32]);
                    out.extend_from_slice(&2500u16.to_le_bytes());
                }
                other => panic!("test encoder does not support {other:?}"),
            }
        }
        out
    }

    #[test]
    fn trade_event_full_prefix_extra_and_error() {
        let idl = Idl::parse(PUMP).unwrap();
        let ev = idl.event("TradeEvent").unwrap();
        let n = ev.fields.len();

        let full = encode_trade_prefix(&idl, n);
        let d = idl.decode_event(ev, &full);
        assert_eq!(d.status, DecodeStatus::Ok);
        assert_eq!(d.fields_present, n);
        assert_eq!(d.values[1], Val::U64(1001)); // sol_amount
        assert_eq!(d.values[3], Val::Bool(true)); // is_buy
        let sh = ev.fields.iter().position(|f| f.name == "shareholders").unwrap();
        let Val::Str(s) = &d.values[sh] else { panic!() };
        assert!(s.contains("\"share_bps\":2500"), "{s}");

        // An older layout that stops after `ix_name` decodes as a prefix with a null tail.
        let ix_name = ev.fields.iter().position(|f| f.name == "ix_name").unwrap();
        let old = encode_trade_prefix(&idl, ix_name + 1);
        let d = idl.decode_event(ev, &old);
        assert_eq!(d.status, DecodeStatus::Prefix);
        assert_eq!(d.fields_present, ix_name + 1);
        assert_eq!(d.values[ix_name], Val::Str("buy".into()));
        assert!(d.values[ix_name + 1..].iter().all(|v| *v == Val::Null));

        let mut newer = full.clone();
        newer.extend_from_slice(&[1, 2, 3]);
        assert_eq!(idl.decode_event(ev, &newer).status, DecodeStatus::Extra);

        let cut = &full[..full.len() - 3];
        assert_eq!(idl.decode_event(ev, cut).status, DecodeStatus::Error);
    }

    #[test]
    fn strict_bool_utf8_and_empty_body() {
        let idl = Idl::parse(PUMP).unwrap();
        let ev = idl.event("TradeEvent").unwrap();
        let is_buy = ev.fields.iter().position(|f| f.name == "is_buy").unwrap();
        let ix_name = ev.fields.iter().position(|f| f.name == "ix_name").unwrap();
        let full = encode_trade_prefix(&idl, ev.fields.len());

        // Byte offset of is_buy: mint (32) + sol_amount (8) + token_amount (8).
        assert_eq!(is_buy, 3);
        let mut bad_bool = full.clone();
        bad_bool[48] = 2;
        assert_eq!(idl.decode_event(ev, &bad_bool).status, DecodeStatus::Error);

        let old = encode_trade_prefix(&idl, ix_name + 1);
        let mut bad_utf8 = old.clone();
        let n = bad_utf8.len();
        bad_utf8[n - 2] = 0xff;
        assert_eq!(idl.decode_event(ev, &bad_utf8).status, DecodeStatus::Error);

        assert_eq!(idl.decode_event(ev, &[]).status, DecodeStatus::Error);
    }

    #[test]
    fn decodes_trade_instruction_args() {
        let idl = Idl::parse(PUMP).unwrap();
        // buy(amount = 1000, max_sol_cost = 2_000_000, track_volume = OptionBool(true))
        let mut data = vec![0x66, 0x06, 0x3d, 0x12, 0x01, 0xda, 0xeb, 0xea];
        data.extend_from_slice(&1000u64.to_le_bytes());
        data.extend_from_slice(&2_000_000u64.to_le_bytes());
        data.push(1);
        let (name, args) = idl.decode_instruction(&data).unwrap();
        assert_eq!(name, "buy");
        let v: J = serde_json::from_str(&args).unwrap();
        assert_eq!(v["amount"], 1000);
        assert_eq!(v["max_sol_cost"], 2_000_000);
        assert_eq!(v["track_volume"]["_0"], true);
        assert_eq!(v["_decode"], "prefix"); // partial_fill was added later
        assert!(idl.decode_instruction(&[0u8; 8]).is_none());
        let amm = Idl::parse(PUMP_AMM).unwrap();
        let mut sell = vec![0x33, 0xe6, 0x85, 0xa4, 0x01, 0x7f, 0x83, 0xad];
        sell.extend_from_slice(&5u64.to_le_bytes());
        sell.extend_from_slice(&7u64.to_le_bytes());
        let (name, args) = amm.decode_instruction(&sell).unwrap();
        assert_eq!(name, "sell");
        assert!(args.contains("\"min_quote_amount_out\":7") && args.contains("\"_decode\":\"ok\""), "{args}");
    }

    #[test]
    fn hostile_length_prefix_does_not_allocate() {
        let idl = Idl::parse(PUMP).unwrap();
        let ev = idl.event("CreateEvent").unwrap();
        // name: string with a 4 GiB length prefix.
        let body = [0xff, 0xff, 0xff, 0xff, 1, 2, 3];
        let d = idl.decode_event(ev, &body);
        assert_eq!(d.status, DecodeStatus::Error);
    }
}
