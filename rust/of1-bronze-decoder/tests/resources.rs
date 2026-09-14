//! Synthetic serialization boundaries; authentic measurements are separate receipts.
use of1_bronze_decoder::resources::*;
use serde_json::{Value, json};

#[test]
fn compact_encoding_is_lossless_and_counted_exactly() {
    let record = json!({"u":u64::MAX,"i":i64::MIN,"null":null,"state":"UNAVAILABLE","bytes":"00ff","array":[1,1]});
    for pretty in [false, true] {
        let bytes = if pretty {
            serde_json::to_vec_pretty(&record).unwrap()
        } else {
            serde_json::to_vec(&record).unwrap()
        };
        assert_eq!(serialized_bytes(&record, pretty).unwrap(), bytes.len());
        assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(), record);
    }
    let size = serialized_bytes(&record, false).unwrap();
    assert_eq!(
        bounded_json(&record, "TEST", size).unwrap(),
        serde_json::to_vec(&record).unwrap()
    );
    assert!(
        bounded_json(&record, "TEST", size - 1)
            .unwrap_err()
            .to_string()
            .contains("TEST")
    );
}

#[test]
fn ordered_duplicate_jsonl_records_and_newlines_are_charged() {
    let records = vec![json!({"null":null}), json!({"null":null}), json!({})];
    let expected = records
        .iter()
        .map(|r| serde_json::to_string(r).unwrap() + "\n")
        .collect::<String>();
    assert_eq!(
        bounded_jsonl(&records, expected.len()).unwrap(),
        expected.as_bytes()
    );
    assert!(bounded_jsonl(&records, expected.len() - 1).is_err());
    assert_eq!(bounded_jsonl(&[], 0).unwrap(), Vec::<u8>::new());
}

#[test]
fn individual_record_cap_is_not_the_slot_or_layer_cap() {
    let record = json!("x".repeat(MAX_INDIVIDUAL_RECORD_BYTES - 3));
    assert_eq!(
        record_bytes(&record).unwrap(),
        MAX_INDIVIDUAL_RECORD_BYTES - 1
    );
    assert!(record_bytes(&json!("x".repeat(MAX_INDIVIDUAL_RECORD_BYTES - 2))).is_err());
    assert_eq!(
        bounded_jsonl(&[record], MAX_JSONL_BYTES).unwrap().len(),
        MAX_INDIVIDUAL_RECORD_BYTES
    );
}

#[test]
fn publication_admits_all_files_and_marker_without_overflow() {
    assert_eq!(publication_bytes(&[3, 5], 72).unwrap(), 72);
    assert!(publication_bytes(&[3, 5], 71).is_err());
    assert!(publication_bytes(&[usize::MAX], usize::MAX).is_err());
    // Every independently bounded output can fit the cumulative cap.
    assert!(
        publication_bytes(
            &[
                MAX_QUALITY_BYTES,
                MAX_JSONL_BYTES,
                MAX_JSONL_BYTES,
                MAX_HTML_BYTES,
                MAX_EXECUTION_BYTES
            ],
            MAX_PUBLICATION_BYTES
        )
        .is_ok()
    );
}

#[test]
fn measurement_uses_all_records_and_zero_only_for_observed_empty_silver() {
    let record = json!({"effective_at":{"slot":"7"},"value":null});
    let report = json!({"records":[record.clone(),record.clone()],"silver_records":[],"slots":[{"slot":"7","resource_accounting":{"decoded_metadata_bytes":17}}]});
    let measured = measure(&report).unwrap();
    assert_eq!(measured["totals"]["bronze"]["records"], 2);
    assert_eq!(measured["slots"]["7"]["silver"]["records"], 0);
    assert_eq!(measured["decoded_metadata_bytes_per_slot"]["7"], 17);
    assert_eq!(
        measured["totals"]["bronze"]["jsonl_bytes"],
        2 * (serialized_bytes(&record, false).unwrap() + 1)
    );
    assert_eq!(measured["research_ready"], false);
}
