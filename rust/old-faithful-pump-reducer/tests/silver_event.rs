use old_faithful_pump_reducer::decode_pump_silver_event_hex;

#[test]
fn matches_the_shared_typescript_golden_vectors_exactly() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/pump-silver/event-vectors.json"
    ))
    .unwrap();
    assert_eq!(
        fixture["schemaVersion"],
        "PUMP_SILVER_EVENT_GOLDEN_VECTORS_1"
    );
    assert_eq!(
        fixture["provenance"]["gitSha"],
        "9c82f61cb711b044a17f770ab8ce9f9bdf78f333"
    );
    assert_eq!(
        fixture["provenance"]["idlSha256"],
        "b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49"
    );

    for vector in fixture["vectors"].as_array().unwrap() {
        let actual = decode_pump_silver_event_hex(vector["dataHex"].as_str().unwrap()).unwrap();
        assert_eq!(actual, vector["expected"], "vector {}", vector["name"]);
    }
}

#[test]
fn rejects_event_fabrication_and_payload_drift_fail_closed() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/pump-silver/event-vectors.json"
    ))
    .unwrap();
    let valid = fixture["vectors"][0]["dataHex"].as_str().unwrap();

    let wrong_tag = format!("00{}", &valid[2..]);
    assert_eq!(
        decode_pump_silver_event_hex(&wrong_tag)
            .unwrap_err()
            .to_string(),
        "invalid_event_cpi_tag"
    );

    let unknown = format!("{}deadbeefcafebabe{}", &valid[..16], &valid[32..]);
    assert_eq!(
        decode_pump_silver_event_hex(&unknown)
            .unwrap_err()
            .to_string(),
        "unknown_event_discriminator"
    );

    let trailing = format!("{valid}00");
    assert_eq!(
        decode_pump_silver_event_hex(&trailing)
            .unwrap_err()
            .to_string(),
        "trailing_event_bytes"
    );

    let trade = fixture["vectors"][0]["dataHex"].as_str().unwrap();
    let truncated_text = trade.replacen("03000000627579", "00040000627579", 1);
    assert_ne!(truncated_text, trade);
    assert_eq!(
        decode_pump_silver_event_hex(&truncated_text)
            .unwrap_err()
            .to_string(),
        "invalid_event_text"
    );

    let shareholder_tail_bytes = 4 + 32 + 2 + 32 + 8 + 8 + 8;
    let shareholder_offset = trade.len() - shareholder_tail_bytes * 2;
    let first_shareholder_start = shareholder_offset + 8;
    let first_shareholder_end = first_shareholder_start + 68;
    let duplicate_address_end = first_shareholder_start + 64;
    let duplicate_truncated = format!(
        "{}02000000{}{}",
        &trade[..shareholder_offset],
        &trade[first_shareholder_start..first_shareholder_end],
        &trade[first_shareholder_start..duplicate_address_end]
    );
    assert_eq!(
        decode_pump_silver_event_hex(&duplicate_truncated)
            .unwrap_err()
            .to_string(),
        "duplicate_shareholder"
    );
}
