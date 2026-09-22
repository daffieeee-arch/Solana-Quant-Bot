//! Static, escaped presentation of Rust-authorized archival diagnostics. No scripts or I/O.
use serde_json::Value;
use std::fmt::Write;

fn escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn text(value: &Value) -> String {
    escape(
        value
            .as_str()
            .map_or_else(|| value.to_string(), str::to_owned)
            .as_str(),
    )
}

fn span(value: &Value) -> String {
    match (value["offset"].as_u64(), value["length"].as_u64()) {
        (Some(start), Some(length)) => format!("[{start}, {}) · {length} bytes", start + length),
        _ => "Unavailable".into(),
    }
}

/// Render a complete inspection, including explicit incomplete/quarantine states.
/// All dynamic values are text-escaped; no remote resources or executable markup.
/// # Errors
/// Rejects other report schemas or an unrenderable JSON value.
pub fn render(report: &Value) -> Result<String, Box<dyn std::error::Error>> {
    if report["schema"] != "OF1_RAW_ARCHIVAL_INSPECTION_1" {
        return Err("expected archival inspection schema".into());
    }
    let mut html = String::from(
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Raw archive byte inspection</title><style>body{font:16px system-ui;margin:2rem;max-width:110rem;color:#192533;background:#fafbfc}table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #c8d0da;padding:.45rem;text-align:left;vertical-align:top}code,pre{overflow-wrap:anywhere;white-space:pre-wrap}summary{cursor:pointer}th{background:#e7eef6}.note{padding:1rem;background:#fff3cc}</style><h1>Raw archive byte inspection</h1><p class=\"note\">Engineering diagnostics only. Archival transaction envelopes are not decoded Solana transactions or admitted Silver facts. Missing domain decode is unknown, never zero activity. This does not prove whole-epoch/root membership, historical program activation, lifecycle completeness or Research Ready.</p>",
    );
    write!(
        html,
        "<p>Run <code>{}</code> · CAR status <strong>{}</strong> · error <code>{}</code></p>",
        text(&report["run_id"]),
        text(&report["stages"]["car_slot"]),
        text(&report["integrity"]["error"])
    )?;
    html.push_str("<h2>How to read byte ranges</h2><p>Every range is zero-based and half-open: [start, end). The section includes its length varint and CID; CBOR excludes them; data includes only bytestring content. Offsets below refer to the assembled slot range, not the complete CAR. Intersect each span with the receipt ranges; split at receipt boundaries. Original Raw offset = raw_offset + assembled offset − assembled_offset. Original CAR offset = car_offset + assembled offset − assembled_offset.</p><p>Physical storage order, Block → Entry → Transaction link order and nullable declared transaction positions are separate. Link order is not a claim of decoded execution semantics. Data/status frames remain paired in one opaque transaction envelope. Continuation links retain source order; payload assembly, checksum verification and decompression are NOT performed by this view.</p>");
    if let Some(slots) = report["integrity"]["slots"].as_array() {
        for slot in slots {
            write!(
                html,
                "<h2>Slot {}</h2><p>Archival node counts: <code>{}</code></p><h3>Original receipt ranges</h3><pre>{}</pre>",
                text(&slot["slot"]),
                text(&slot["archival_node_counts"]),
                escape(&serde_json::to_string_pretty(&slot["ranges"])?)
            )?;
            if slot["archival_node_counts"]["dataframe"] == 0 {
                html.push_str("<p class=\"note\">This capture contains no standalone continuation DataFrame. Continuation byte-span correctness is covered by the sealed structural fixture, not demonstrated by this capture.</p>");
            }
            if slot["ranges"]
                .as_array()
                .is_some_and(|ranges| ranges.len() == 1)
            {
                html.push_str("<p>This slot uses one Raw receipt. Assembly across multiple receipt boundaries is fixture coverage only.</p>");
            }
            if let Some(first) = slot["transaction_envelopes"]
                .as_array()
                .and_then(|a| a.first())
            {
                write!(
                    html,
                    "<h3>Worked example: first linked transaction envelope</h3><p>Entry ordinal {}, transaction within entry {}, archival ordinal {}; declared position {}. Its opaque data is at <code>{}</code> and status metadata at <code>{}</code>. Look up CID <code>{}</code> in the physical table. Apply the receipt mapping above to read those exact bytes from the original Raw file. These byte locations do not assert a successful transaction, trade or account role.</p>",
                    text(&first["block_entry_ordinal"]),
                    text(&first["entry_transaction_ordinal"]),
                    text(&first["archival_ordinal"]),
                    text(&first["transaction_position"]),
                    span(&first["data"]["data_span"]),
                    span(&first["metadata"]["data_span"]),
                    text(&first["transaction_cid_hex"])
                )?;
            }
            html.push_str("<h3>All archive nodes in physical order</h3><table><thead><tr><th>Physical ordinal / type / CID (hex)</th><th>Section / CBOR</th><th>Inline or standalone DataFrames</th><th>Links in source order</th></tr></thead><tbody>");
            if let Some(nodes) = slot["archival_nodes"].as_array() {
                for node in nodes {
                    write!(
                        html,
                        "<tr><td>{} · {}<br><code>{}</code></td><td>Section {}<br>CBOR {}</td><td>",
                        text(&node["physical_ordinal"]),
                        text(&node["kind"]),
                        text(&node["cid_hex"]),
                        span(&node["section_span"]),
                        span(&node["raw_cbor_span"])
                    )?;
                    if let Some(frames) = node["frames"].as_array() {
                        for (ordinal, frame) in frames.iter().enumerate() {
                            write!(
                                html,
                                "<p>Frame {ordinal}: CBOR {}<br>Data {}<br>Index {} / total {}; opaque checksum {}<br>Next <code>{}</code></p>",
                                span(&frame["raw_cbor_span"]),
                                span(&frame["data_span"]),
                                text(&frame["frame_index"]),
                                text(&frame["total"]),
                                text(&frame["checksum"]),
                                text(&frame["next_cid_hex"])
                            )?;
                        }
                    }
                    write!(
                        html,
                        "</td><td><code>{}</code></td></tr>",
                        text(&node["linked_cid_hex"])
                    )?;
                }
            }
            html.push_str("</tbody></table><details><summary>All atomic transaction envelopes in link order</summary><pre>");
            html.push_str(&escape(&serde_json::to_string_pretty(
                &slot["transaction_envelopes"],
            )?));
            html.push_str("</pre></details>");
        }
    }
    html.push_str("<h2>Bindings and reproducibility</h2><p>The JSON includes every node and envelope; no display limit applies. Binary and compiled-source hashes identify the inspection implementation. Receipt/manifest hashes identify immutable inputs. Operational clocks remain in original receipts; they are not historical event time. Reproduction uses of1-verify-recorded RUN_ROOT --inspect-json or --inspect-html under the approved offline resource scope.</p><details><summary>Complete machine-readable report (escaped JSON)</summary><pre>");
    html.push_str(&escape(&serde_json::to_string_pretty(report)?));
    html.push_str("</pre></details></html>");
    Ok(html)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn untrusted_report_text_cannot_become_html_or_a_script() {
        let report = serde_json::json!({"schema":"OF1_RAW_ARCHIVAL_INSPECTION_1","run_id":"<script>alert('x')</script>"});
        let html = render(&report).unwrap();
        assert!(!html.contains("<script>"));
        assert!(html.contains("&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;"));
        assert!(render(&serde_json::json!({})).is_err());
    }
}
