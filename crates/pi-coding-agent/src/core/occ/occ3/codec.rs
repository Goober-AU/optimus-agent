//! OCC-3 envelope codec: the textual envelope that rides the ordinary
//! agent-message string (same seam as OCC-2's wrap_outgoing_body).
//!
//! Constraints from the transport audit: UTF-8 JSON inside a 16384-char
//! bound, never truncated (R1), self-describing header so receivers
//! can classify without a registry. The envelope carries NO tensors:
//! only the opaque handle reference and a bounded OCC-1 control
//! summary that survives handle expiry.

use serde_json::json;

use super::types::{
    Occ3Envelope, Occ3Error, Occ3HandleRef, Occ3Method, OCC3_MAX_SUMMARY_CHARS,
};

/// Wire header prefix: receivers classify on this alone.
pub const OCC3_HEADER: &str = "OCC/3";

/// Encode an envelope to its wire form: a JSON object on one line,
/// prefixed by the self-describing header. Deterministic field order
/// via serde_json's preserve_order-free BTreeMap-backed object.
pub fn encode_envelope(envelope: &Occ3Envelope) -> Result<String, Occ3Error> {
    // Enforce the bounded control summary (R1: reject, never truncate).
    if envelope.control_summary.chars().count() > OCC3_MAX_SUMMARY_CHARS {
        return Err(Occ3Error::TransferFailed {
            reason: "control summary exceeds bound".into(),
        });
    }
    let body = serde_json::to_string(envelope).map_err(|e| Occ3Error::TransferFailed {
        reason: format!("envelope encode failed: {e}"),
    })?;
    Ok(format!("{OCC3_HEADER} {body}"))
}

/// Decode a wire form back into an envelope. Returns `None` for text
/// that is not an OCC-3 envelope (callers fall back to their normal
/// classification), and `Err` for malformed OCC-3 (a strike, not a
/// silent fallback).
pub fn decode_envelope(text: &str) -> Result<Option<Occ3Envelope>, Occ3Error> {
    let trimmed = text.trim();
    let Some(rest) = trimmed.strip_prefix(OCC3_HEADER) else {
        return Ok(None);
    };
    let rest = rest.trim_start();
    let envelope: Occ3Envelope =
        serde_json::from_str(rest).map_err(|e| Occ3Error::TransferFailed {
            reason: format!("envelope decode failed: {e}"),
        })?;
    if envelope.schema != "optimus.occ3.v1" {
        return Err(Occ3Error::TransferFailed {
            reason: format!("unknown schema: {}", envelope.schema),
        });
    }
    if envelope.protocol_version != 1 {
        return Err(Occ3Error::TransferFailed {
            reason: format!(
                "unsupported protocol version: {}",
                envelope.protocol_version
            ),
        });
    }
    Ok(Some(envelope))
}

/// Build the minimal envelope for a captured handle.
#[allow(clippy::too_many_arguments)]
pub fn build_envelope(
    message_id: String,
    sender_session_id: String,
    receiver_session_id: String,
    model_fingerprint: String,
    runtime_id: String,
    connector_id: String,
    method: Occ3Method,
    handle: Occ3HandleRef,
    control_summary: String,
    fallback_reference: String,
) -> Result<Occ3Envelope, Occ3Error> {
    if control_summary.chars().count() > OCC3_MAX_SUMMARY_CHARS {
        return Err(Occ3Error::TransferFailed {
            reason: "control summary exceeds bound".into(),
        });
    }
    Ok(Occ3Envelope {
        schema: "optimus.occ3.v1".into(),
        protocol_version: 1,
        message_id,
        sender_session_id,
        receiver_session_id,
        model_fingerprint,
        runtime_id,
        connector_id,
        latent_method: method,
        state_handle: handle,
        control_summary,
        fallback_reference,
    })
}

/// Render the human-visible preview line for a delivered OCC-3
/// message (the compact form users see in the transcript).
pub fn render_preview(envelope: &Occ3Envelope) -> String {
    json!({
        "occ3": {
            "method": envelope.latent_method.as_str(),
            "stateHandle": envelope.state_handle.handle_id,
            "modelFingerprint": envelope.model_fingerprint,
        }
    })
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn handle() -> Occ3HandleRef {
        Occ3HandleRef {
            handle_id: "h-123".into(),
            connector_id: "sglang-occ3".into(),
            model_fingerprint: "occ3-mi-0000000000000001".into(),
            runtime_generation: 1,
            expires_at_epoch: 1893456000,
            state_size_bytes: 1024,
        }
    }

    #[test]
    fn round_trip() {
        let envelope = build_envelope(
            "agentmsg_1".into(),
            "sender".into(),
            "receiver".into(),
            "occ3-mi-0000000000000001".into(),
            "sglang-0.5.21/gen-1".into(),
            "sglang-occ3".into(),
            Occ3Method::KvHandoff,
            handle(),
            "T=probe K=RESULT S=done F1=gpu-verified".into(),
            "occ1".into(),
        )
        .unwrap();
        let wire = encode_envelope(&envelope).unwrap();
        assert!(wire.starts_with("OCC/3 "));
        let decoded = decode_envelope(&wire).unwrap().unwrap();
        assert_eq!(decoded, envelope);
    }

    #[test]
    fn plain_text_is_not_an_envelope() {
        assert!(decode_envelope("OCC/1 K=hello").unwrap().is_none());
        assert!(decode_envelope("just text").unwrap().is_none());
        assert!(decode_envelope("OCC/2 D=sym-v1@1 C=1efce9992ee2").unwrap().is_none());
    }

    #[test]
    fn malformed_envelope_is_an_error_not_a_fallback() {
        assert!(decode_envelope("OCC/3 {not json").is_err());
        assert!(decode_envelope("OCC/3 {\"schema\":\"wrong\",\"protocolVersion\":1}")
            .is_err());
    }

    #[test]
    fn oversized_summary_is_rejected_not_truncated() {
        let result = build_envelope(
            "m".into(),
            "s".into(),
            "r".into(),
            "fp".into(),
            "rt".into(),
            "c".into(),
            Occ3Method::StatebridgePrefix,
            handle(),
            "x".repeat(OCC3_MAX_SUMMARY_CHARS + 1),
            "occ1".into(),
        );
        assert!(result.is_err());
    }
}
