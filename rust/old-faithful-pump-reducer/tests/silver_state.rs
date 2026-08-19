use old_faithful_pump_reducer::evaluate_pump_silver_state_fixture;

fn shared_vectors() -> serde_json::Value {
    serde_json::from_str(include_str!(
        "../../../tests/fixtures/pump-silver/state-vectors.json"
    ))
    .expect("state vectors must be valid JSON")
}

#[test]
fn matches_the_shared_typescript_state_vectors_byte_for_byte() {
    let fixture = shared_vectors();
    for vector in fixture["vectors"]
        .as_array()
        .expect("vectors must be an array")
    {
        let observed =
            evaluate_pump_silver_state_fixture(&vector["eventBinding"], &vector["evidence"]);
        assert_eq!(observed, vector["expected"], "vector {}", vector["name"]);
        assert_eq!(observed["researchReady"], false);
        assert_eq!(observed["pilotEligible"], false);
    }
}

#[test]
fn matches_shared_typescript_adversarial_quarantines_byte_for_byte() {
    let fixture = shared_vectors();
    for vector in fixture["adversarialVectors"]
        .as_array()
        .expect("adversarial vectors must be an array")
    {
        let observed =
            evaluate_pump_silver_state_fixture(&vector["eventBinding"], &vector["evidence"]);
        assert_eq!(observed, vector["expected"], "vector {}", vector["name"]);
        assert_eq!(observed["status"], "QUARANTINED");
        assert_eq!(observed["researchReady"], false);
    }
}
