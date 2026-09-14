//! Typed lossless projection of Rust-authorized metadata observations, not a decoder.
use crate::{
    columns::{Kind, Layer, state, u64_value},
    invalid,
};
use arrow_array::{
    ArrayRef,
    builder::{
        ArrayBuilder, BinaryBuilder, BooleanBuilder, ListBuilder, StringBuilder, StructBuilder,
        UInt64Builder, make_builder,
    },
};
use arrow_schema::{DataType, Field};
use serde_json::{Value, json};
use std::{io, sync::Arc};

fn specs() -> Vec<(&'static str, String, Kind)> {
    let mut fields = vec![];
    for (name, kind) in [
        ("side", Kind::Text),
        ("ordinal", Kind::U64),
        ("account_index", Kind::U64),
        ("account_key", Kind::Text),
        ("mint", Kind::Text),
        ("owner", Kind::Text),
        ("program_id", Kind::Text),
        ("amount_string", Kind::Text),
        ("amount_u64", Kind::U64),
        ("decimals", Kind::U64),
        ("exact_decimal_amount", Kind::Text),
        ("disposition", Kind::Text),
        ("reason", Kind::Text),
        ("raw_token_balance_sha256", Kind::Text),
    ] {
        fields.push((name, format!("/{name}"), kind));
    }
    fields.push((
        "raw_token_balance_bytes",
        "/raw_token_balance_hex".into(),
        Kind::Bytes,
    ));
    fields.push((
        "account_index_field_state",
        "/field_states/account_index".into(),
        Kind::Text,
    ));
    fields.push((
        "decimals_field_state",
        "/field_states/decimals".into(),
        Kind::Text,
    ));
    for (name, path) in [
        ("account_index_present", "account_index"),
        ("mint_present", "mint"),
        ("owner_present", "owner"),
        ("program_id_present", "program_id"),
        ("ui_token_amount_present", "ui_token_amount"),
        ("amount_present", "amount"),
        ("decimals_present", "decimals"),
        ("ui_amount_present", "ui_amount"),
        ("ui_amount_string_present", "ui_amount_string"),
    ] {
        fields.push((name, format!("/presence/{path}"), Kind::Bool));
    }
    fields
}

fn role_specs() -> Vec<(&'static str, String, Kind)> {
    [
        ("role", Kind::Text),
        ("account_index", Kind::U64),
        ("account_key", Kind::Text),
        ("expected_owner", Kind::Text),
        ("pre_observation_indexes", Kind::U64List),
        ("post_observation_indexes", Kind::U64List),
        ("transaction_delta_raw_signed", Kind::Text),
        ("delta_status", Kind::Text),
    ]
    .into_iter()
    .map(|(name, kind)| (name, format!("/{name}"), kind))
    .collect()
}

fn fields(specs: &[(&str, String, Kind)]) -> Vec<Field> {
    specs
        .iter()
        .flat_map(|(name, _, kind)| {
            [
                Field::new(*name, kind.data_type(), true),
                Field::new(format!("{name}_state"), DataType::Utf8, false),
            ]
        })
        .collect()
}

pub(crate) fn data_type() -> DataType {
    DataType::List(Arc::new(Field::new(
        "item",
        DataType::Struct(fields(&specs()).into()),
        true,
    )))
}

pub(crate) fn roles_data_type() -> DataType {
    DataType::List(Arc::new(Field::new(
        "item",
        DataType::Struct(fields(&role_specs()).into()),
        true,
    )))
}

pub(crate) fn descriptor(layer: Layer) -> Value {
    let specs = match layer {
        Layer::Bronze => specs(),
        Layer::Silver => role_specs(),
    };
    json!({"preservation":"ordered observations, duplicates and absence retained; no domain defaults", "fields":specs.into_iter().map(|(name,pointer,kind)|json!({"name":name,"pointer":pointer,"type":format!("{kind:?}"),"state_column":format!("{name}_state")})).collect::<Vec<_>>()})
}

fn append_observation(
    builder: &mut StructBuilder,
    observation: &Value,
    specs: &[(&str, String, Kind)],
) -> io::Result<()> {
    if !observation.is_object() {
        return Err(invalid("TOKEN_BALANCE_OBSERVATION_OBJECT_REQUIRED"));
    }
    for (index, (_, pointer, kind)) in specs.iter().enumerate() {
        let value = observation.pointer(pointer);
        let present = value.filter(|v| !v.is_null());
        let fail = || invalid(format!("TOKEN_BALANCE_FIELD_TYPE_MISMATCH {pointer}"));
        match kind {
            Kind::U64 => builder
                .field_builder::<UInt64Builder>(index * 2)
                .expect("fixed unsigned field")
                .append_option(present.map(u64_value).transpose()?),
            Kind::Text => builder
                .field_builder::<StringBuilder>(index * 2)
                .expect("fixed text field")
                .append_option(present.map(|v| v.as_str().ok_or_else(fail)).transpose()?),
            Kind::Bool => builder
                .field_builder::<BooleanBuilder>(index * 2)
                .expect("fixed bool field")
                .append_option(present.map(|v| v.as_bool().ok_or_else(fail)).transpose()?),
            Kind::Bytes => {
                let bytes = present
                    .map(|v| hex::decode(v.as_str().ok_or_else(fail)?).map_err(invalid))
                    .transpose()?;
                builder
                    .field_builder::<BinaryBuilder>(index * 2)
                    .expect("fixed bytes field")
                    .append_option(bytes.as_deref());
            }
            Kind::U64List => append_unsigned(
                builder
                    .field_builder::<ListBuilder<UInt64Builder>>(index * 2)
                    .expect("fixed unsigned list field"),
                present,
            )?,
            _ => return Err(invalid("UNSUPPORTED_TOKEN_BALANCE_PROJECTION_KIND")),
        }
        builder
            .field_builder::<StringBuilder>(index * 2 + 1)
            .expect("fixed state field")
            .append_value(state(value));
    }
    builder.append(true);
    Ok(())
}

pub(crate) fn array(values: &[Option<&Value>]) -> io::Result<ArrayRef> {
    struct_array(values, &specs())
}

pub(crate) fn roles_array(values: &[Option<&Value>]) -> io::Result<ArrayRef> {
    struct_array(values, &role_specs())
}

fn struct_array(values: &[Option<&Value>], specs: &[(&str, String, Kind)]) -> io::Result<ArrayRef> {
    // Keep nested u64 builders concrete; Arrow's generic factory otherwise boxes
    // their children, which is a different builder type despite the same schema.
    let builders: Vec<Box<dyn ArrayBuilder>> = specs
        .iter()
        .flat_map(|(_, _, kind)| {
            let value: Box<dyn ArrayBuilder> = match kind {
                Kind::U64List => Box::new(ListBuilder::new(UInt64Builder::new())),
                _ => make_builder(&kind.data_type(), 0),
            };
            [
                value,
                Box::new(StringBuilder::new()) as Box<dyn ArrayBuilder>,
            ]
        })
        .collect();
    let mut builder = ListBuilder::new(StructBuilder::new(fields(specs), builders));
    for value in values {
        if let Some(value) = value {
            let observations = value
                .as_array()
                .ok_or_else(|| invalid("TOKEN_BALANCE_ARRAY_REQUIRED"))?;
            for observation in observations {
                append_observation(builder.values(), observation, specs)?;
            }
            builder.append(true);
        } else {
            builder.append(false);
        }
    }
    Ok(Arc::new(builder.finish()))
}

pub(crate) fn unsigned_list(values: &[Option<&Value>]) -> io::Result<ArrayRef> {
    let mut builder = ListBuilder::new(UInt64Builder::new());
    for value in values {
        append_unsigned(&mut builder, *value)?;
    }
    Ok(Arc::new(builder.finish()))
}

fn append_unsigned(
    builder: &mut ListBuilder<UInt64Builder>,
    value: Option<&Value>,
) -> io::Result<()> {
    if let Some(value) = value {
        for item in value
            .as_array()
            .ok_or_else(|| invalid("UNSIGNED_ARRAY_REQUIRED"))?
        {
            builder.values().append_value(u64_value(item)?);
        }
        builder.append(true);
    } else {
        builder.append(false);
    }
    Ok(())
}
