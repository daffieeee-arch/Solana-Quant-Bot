//! Exact JSON-pointer projection; typed columns never supply domain defaults.
use crate::{hash, invalid};
use arrow_array::{
    ArrayRef, BinaryArray, BooleanArray, Int64Array, RecordBatch, StringArray, UInt64Array,
};
use arrow_schema::{DataType, Field, Schema};
use serde_json::{Value, json};
use std::{io, sync::Arc};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Layer {
    Bronze,
    Silver,
}
impl Layer {
    #[must_use]
    pub fn name(self) -> &'static str {
        match self {
            Self::Bronze => "bronze",
            Self::Silver => "silver",
        }
    }
    #[must_use]
    pub fn record_schema(self) -> &'static str {
        match self {
            Self::Bronze => "OF1_BRONZE_TRANSACTION_1",
            Self::Silver => "PUMP_SILVER_RECORDED_SELL_1",
        }
    }
}
#[derive(Clone, Copy, Debug)]
pub enum Kind {
    U64,
    I64,
    Text,
    Bool,
    Bytes,
}
impl Kind {
    fn data_type(self) -> DataType {
        match self {
            Self::U64 => DataType::UInt64,
            Self::I64 => DataType::Int64,
            Self::Text => DataType::Utf8,
            Self::Bool => DataType::Boolean,
            Self::Bytes => DataType::Binary,
        }
    }
}
pub struct Column {
    pub name: String,
    pub pointer: String,
    pub kind: Kind,
}
fn add(out: &mut Vec<Column>, kind: Kind, prefix: &str, fields: &[(&str, &str)]) {
    out.extend(fields.iter().map(|(name, path)| Column {
        name: (*name).into(),
        pointer: format!("{prefix}{path}"),
        kind,
    }));
}
#[must_use]
pub fn specs(layer: Layer) -> Vec<Column> {
    use Kind::{Bool, Text, U64};
    let mut out = Vec::new();
    add(
        &mut out,
        U64,
        "/effective_at/",
        &[
            ("slot", "slot"),
            ("transaction_index", "transaction_index_in_slot"),
            ("entry_index", "entry_index"),
            ("transaction_index_in_entry", "transaction_index_in_entry"),
            ("source_transaction_index", "source_transaction_index"),
        ],
    );
    add(
        &mut out,
        Text,
        "/",
        &[
            ("record_schema", "schema"),
            ("slice_class", "slice_class"),
            ("input_kind", "input_kind"),
            ("receipt_evidence", "receipt_evidence"),
            ("decoder_source_sha256", "decoder_source_sha256"),
            ("observed_at", "observed_at"),
            ("actionable_at", "actionable_at"),
            ("execution_opportunity_at", "execution_opportunity_at"),
            ("observation_model_id", "observation_model_id"),
        ],
    );
    add(
        &mut out,
        Bool,
        "/",
        &[("atomic_observation_package", "atomic_observation_package")],
    );
    add(
        &mut out,
        Text,
        "/source/",
        &[
            ("run_id", "run_id"),
            ("raw_sha256", "raw_sha256"),
            ("raw_path", "raw_path"),
            ("transaction_node_cid_hex", "transaction_node_cid_hex"),
            (
                "acquisition_executable_sha256",
                "acquisition_executable_sha256",
            ),
            ("manifest_sha256", "bindings/manifest_sha256"),
            (
                "payload_manifest_sha256",
                "bindings/payload_manifest_sha256",
            ),
        ],
    );
    add(
        &mut out,
        U64,
        "/source/",
        &[
            ("receipt_sequence", "receipt_sequence"),
            ("raw_section_offset", "raw_section_offset"),
            ("raw_section_length", "raw_section_length"),
            ("acquired_at_unix_ms", "acquired_at_unix_ms"),
        ],
    );
    layer_specs(&mut out, layer);
    out
}
fn layer_specs(out: &mut Vec<Column>, layer: Layer) {
    use Kind::{Bool, Bytes, Text, U64};
    match layer {
        Layer::Bronze => {
            add(
                out,
                Text,
                "/",
                &[("disposition", "disposition"), ("reason", "reason")],
            );
            add(
                out,
                Text,
                "/transaction/",
                &[
                    ("transaction_status", "status"),
                    ("first_signature", "signatures/0"),
                    ("wire_sha256", "wire_sha256"),
                    ("metadata_sha256", "metadata_sha256"),
                    ("name", "name"),
                    ("ticker", "ticker"),
                    ("launch_at", "launch_at"),
                    ("economic_identity", "economic_identity"),
                ],
            );
            add(
                out,
                U64,
                "/transaction/",
                &[
                    ("fee_lamports", "fee_lamports"),
                    ("compute_units_consumed", "compute_units_consumed"),
                    ("cost_units", "cost_units"),
                ],
            );
            add(
                out,
                Bool,
                "/transaction/",
                &[("pump_program_involvement", "pump_program_involvement")],
            );
            add(
                out,
                Bytes,
                "/transaction/",
                &[
                    ("wire_bytes", "wire_hex"),
                    ("status_metadata_bytes", "protobuf_metadata_hex"),
                ],
            );
        }
        Layer::Silver => silver_specs(out),
    }
}
fn silver_specs(out: &mut Vec<Column>) {
    use Kind::{Text, U64};
    add(
        out,
        Text,
        "/",
        &[
            ("bronze_record_sha256", "bronze_record_sha256"),
            ("record_kind", "record_kind"),
            ("candidate_id", "candidate_id"),
            ("candidate_evidence", "candidate_evidence"),
            ("source_evidence_sha256", "source_evidence_sha256"),
            ("transaction_status", "transaction_status"),
            ("first_signature", "signatures/0"),
            ("wire_sha256", "wire_sha256"),
            ("metadata_sha256", "metadata_sha256"),
            ("quote_mint_identity", "quote_mint_identity"),
            ("historical_activation", "historical_activation"),
            ("root_to_slot_membership", "root_to_slot_membership"),
            ("committed_account_state", "committed_account_state"),
            (
                "signature_crypto_verification",
                "signature_crypto_verification",
            ),
            ("name", "name"),
            ("ticker", "ticker"),
            ("launch_at", "launch_at"),
            ("executable_price", "executable_price"),
            ("net_proceeds", "net_proceeds"),
        ],
    );
    add(
        out,
        U64,
        "/",
        &[
            ("fee_lamports", "transaction_fee_lamports"),
            ("base_decimals", "base_decimals"),
            ("quote_decimals", "quote_decimals"),
        ],
    );
    add(
        out,
        U64,
        "/instruction/",
        &[
            ("amount_raw_u64", "amount_raw_u64"),
            ("min_sol_output_raw_u64", "min_sol_output_raw_u64"),
        ],
    );
    add(
        out,
        Text,
        "/event_reported/",
        &[
            ("mint", "mint_address"),
            ("user_address", "user_address"),
            ("quote_mint_raw_hex", "quote_mint_raw_hex"),
            ("event_instruction_name", "ix_name"),
            ("fee_recipient", "fee_recipient"),
            ("creator_address_reported", "creator_address_reported"),
        ],
    );
    silver_event_numbers(out);
    silver_context(out);
}
fn silver_event_numbers(out: &mut Vec<Column>) {
    use Kind::{Bool, I64, U64};
    for name in [
        "sol_amount",
        "token_amount",
        "virtual_sol_reserves",
        "virtual_token_reserves",
        "real_sol_reserves",
        "real_token_reserves",
        "fee_basis_points",
        "fee",
        "creator_fee_basis_points",
        "creator_fee",
        "total_unclaimed_tokens",
        "total_claimed_tokens",
        "current_sol_volume",
        "cashback_fee_basis_points",
        "cashback",
        "buyback_fee_basis_points",
        "buyback_fee",
        "quote_amount",
        "virtual_quote_reserves",
        "real_quote_reserves",
    ] {
        let key = format!("{name}_raw_u64");
        add(out, U64, "/event_reported/", &[(&key, &key)]);
    }
    add(
        out,
        I64,
        "/event_reported/",
        &[
            ("timestamp_raw_i64", "timestamp_raw_i64"),
            (
                "last_update_timestamp_raw_i64",
                "last_update_timestamp_raw_i64",
            ),
        ],
    );
    add(
        out,
        Bool,
        "/event_reported/",
        &[
            ("is_buy", "is_buy"),
            ("track_volume", "track_volume"),
            ("mayhem_mode", "mayhem_mode"),
        ],
    );
    add(
        out,
        Bool,
        "/",
        &[
            ("research_ready", "research_ready"),
            ("account_contents_verified", "account_contents_verified"),
        ],
    );
}
fn silver_context(out: &mut Vec<Column>) {
    use Kind::{Bool, Bytes, Text, U64};
    add(
        out,
        U64,
        "/event_context/",
        &[
            ("outer_index", "outer_index"),
            ("instruction_inner_order", "parent/inner_order"),
            (
                "instruction_stack_height",
                "selected_invocation/stack_height",
            ),
            ("event_inner_order", "inner_order"),
            ("event_stack_height", "stack_height"),
            ("subtree_start", "subtree/start_inner_order"),
            ("subtree_end_exclusive", "subtree/end_inner_order_exclusive"),
        ],
    );
    add(
        out,
        Text,
        "/event_context/",
        &[
            ("event_association", "association"),
            ("parent_program_id", "parent/program_id"),
            ("root_program_id", "selected_invocation/parent_program_id"),
            ("cpi_flags_evidence", "cpi_account_flags/evidence"),
        ],
    );
    add(
        out,
        Bool,
        "/event_context/cpi_account_flags/",
        &[("cpi_signer", "signer"), ("cpi_writable", "writable")],
    );
    add(
        out,
        Bytes,
        "/",
        &[
            ("instruction_bytes", "instruction_data_hex"),
            ("event_cpi_bytes", "event_context/event_cpi_hex"),
        ],
    );
}

/// State is distinct even when both missing and JSON null map to a SQL NULL.
#[must_use]
pub fn state(value: Option<&Value>) -> &'static str {
    match value {
        None => "MISSING",
        Some(Value::Null) => "NULL",
        Some(_) => "VALUE",
    }
}
#[must_use]
pub fn schema_descriptor(layer: Layer) -> Value {
    json!({"schema":"OF1_COLUMNAR_PROJECTION_1","layer":layer.name(),"preserved_record":"exact canonical JSON plus original LF in record_bytes","logical_hash":"SHA256 of length-prefixed canonical JSON records in source order; lengths u64 LE; LF excluded", "fields":schema(layer).fields().iter().map(|f|json!({"name":f.name(),"arrow_type":format!("{:?}",f.data_type()),"nullable":f.is_nullable(),"metadata":f.metadata()})).collect::<Vec<_>>(), "columns":specs(layer).iter().map(|s|json!({"name":s.name,"pointer":s.pointer,"arrow_type":format!("{:?}",s.kind),"state_column":format!("{}_state",s.name)})).collect::<Vec<_>>()})
}
#[must_use]
pub fn schema(layer: Layer) -> Arc<Schema> {
    let mut fields = vec![
        Field::new("record_ordinal", DataType::UInt64, false),
        Field::new("record_sha256", DataType::Utf8, false),
        Field::new("record_bytes", DataType::Binary, false),
    ];
    for s in specs(layer) {
        fields.push(Field::new(&s.name, s.kind.data_type(), true));
        fields.push(Field::new(
            format!("{}_state", s.name),
            DataType::Utf8,
            false,
        ));
    }
    Arc::new(Schema::new(fields))
}
fn u64_value(v: &Value) -> io::Result<u64> {
    if let Some(s) = v.as_str() {
        let n = s.parse::<u64>().map_err(invalid)?;
        if n.to_string() != s {
            return Err(invalid("NONCANONICAL_UNSIGNED_INTEGER"));
        }
        Ok(n)
    } else {
        v.as_u64()
            .ok_or_else(|| invalid("UNSIGNED_INTEGER_REQUIRED"))
    }
}
fn i64_value(v: &Value) -> io::Result<i64> {
    if let Some(s) = v.as_str() {
        let n = s.parse::<i64>().map_err(invalid)?;
        if n.to_string() != s {
            return Err(invalid("NONCANONICAL_SIGNED_INTEGER"));
        }
        Ok(n)
    } else {
        v.as_i64().ok_or_else(|| invalid("SIGNED_INTEGER_REQUIRED"))
    }
}
fn present(v: Option<&Value>) -> Option<&Value> {
    v.filter(|v| !v.is_null())
}
fn typed_column(spec: &Column, records: &[Value]) -> io::Result<ArrayRef> {
    let values: Vec<_> = records
        .iter()
        .map(|r| present(r.pointer(&spec.pointer)))
        .collect();
    let fail = || invalid(format!("FIELD_TYPE_MISMATCH {}", spec.pointer));
    let array: ArrayRef = match spec.kind {
        Kind::U64 => Arc::new(UInt64Array::from(
            values
                .iter()
                .map(|v| v.map(u64_value).transpose())
                .collect::<io::Result<Vec<_>>>()?,
        )),
        Kind::I64 => Arc::new(Int64Array::from(
            values
                .iter()
                .map(|v| v.map(i64_value).transpose())
                .collect::<io::Result<Vec<_>>>()?,
        )),
        Kind::Text => Arc::new(StringArray::from(
            values
                .iter()
                .map(|v| v.map(|x| x.as_str().ok_or_else(fail)).transpose())
                .collect::<io::Result<Vec<_>>>()?,
        )),
        Kind::Bool => Arc::new(BooleanArray::from(
            values
                .iter()
                .map(|v| v.map(|x| x.as_bool().ok_or_else(fail)).transpose())
                .collect::<io::Result<Vec<_>>>()?,
        )),
        Kind::Bytes => {
            let bytes = values
                .iter()
                .map(|v| {
                    v.map(|x| hex::decode(x.as_str().ok_or_else(fail)?).map_err(invalid))
                        .transpose()
                })
                .collect::<io::Result<Vec<_>>>()?;
            Arc::new(bytes.iter().map(|b| b.as_deref()).collect::<BinaryArray>())
        }
    };
    Ok(array)
}
/// Validate the exact previously decoded record; no schema migration.
/// # Errors
/// Rejects noncanonical JSON, unsupported schemas or missing required bindings.
pub fn parse_record(layer: Layer, line: &[u8]) -> io::Result<Value> {
    let raw = line
        .strip_suffix(b"\n")
        .ok_or_else(|| invalid("JSONL_LF_REQUIRED"))?;
    let record: Value = serde_json::from_slice(raw).map_err(invalid)?;
    if !record.is_object() || serde_json::to_vec(&record).map_err(invalid)? != raw {
        return Err(invalid(
            "CANONICAL_JSON_REQUIRED_NO_DUPLICATE_KEYS_OR_NORMALIZATION",
        ));
    }
    if record["schema"] != layer.record_schema()
        || record["slice_class"] != "ENGINEERING_VALIDATION_ONLY"
    {
        return Err(invalid("UNSUPPORTED_RECORD_SCHEMA_OR_SLICE_CLASS"));
    }
    for key in [
        "/effective_at/slot",
        "/effective_at/transaction_index_in_slot",
    ] {
        u64_value(record.pointer(key).ok_or_else(|| invalid(key))?)?;
    }
    for key in ["/source/raw_sha256", "/decoder_source_sha256"] {
        let s = record
            .pointer(key)
            .and_then(Value::as_str)
            .ok_or_else(|| invalid(key))?;
        if s.len() != 64
            || !s
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(invalid("SOURCE_HASH_REQUIRED"));
        }
    }
    Ok(record)
}
/// Arrow batch contains exact original bytes alongside checked typed projections.
/// # Errors
/// Rejects invalid records, integer/type errors, order overflow or Arrow mismatch.
pub fn batch(layer: Layer, lines: &[Vec<u8>], offset: u64) -> io::Result<RecordBatch> {
    let records = lines
        .iter()
        .map(|l| parse_record(layer, l))
        .collect::<io::Result<Vec<_>>>()?;
    let hashes: Vec<_> = lines.iter().map(|l| hash(&l[..l.len() - 1])).collect();
    let ordinals = (0..lines.len())
        .map(|i| {
            offset
                .checked_add(i as u64)
                .ok_or_else(|| invalid("ORDER_OVERFLOW"))
        })
        .collect::<io::Result<Vec<_>>>()?;
    let mut arrays: Vec<ArrayRef> = vec![
        Arc::new(UInt64Array::from(ordinals)),
        Arc::new(StringArray::from(hashes)),
        Arc::new(BinaryArray::from_iter_values(
            lines.iter().map(Vec::as_slice),
        )),
    ];
    for spec in specs(layer) {
        arrays.push(typed_column(&spec, &records)?);
        arrays.push(Arc::new(StringArray::from_iter_values(
            records.iter().map(|r| state(r.pointer(&spec.pointer))),
        )));
    }
    RecordBatch::try_new(schema(layer), arrays).map_err(invalid)
}
