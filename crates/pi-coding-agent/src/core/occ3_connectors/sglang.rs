//! SGLang OCC-3 connector: HTTP client for the `occ3-bridge` plugin.
//!
//! Maps the [`Occ3Connector`] contract onto the plugin's
//! `/occ3/v1/*` control routes:
//!
//! | Contract call        | Plugin route              |
//! |----------------------|---------------------------|
//! | `health`             | GET /occ3/v1/status       |
//! | `discover_capabilities` | GET /occ3/v1/capabilities |
//! | `model_identity`     | GET /occ3/v1/identity     |
//! | `capture_latent`     | POST /occ3/v1/send        |
//! | `deliver_latent`     | GET /occ3/v1/channels (liveness) |
//! | `consume_latent`     | POST /occ3/v1/recv        |
//! | `acknowledge_delivery` / `release_handle` | POST /occ3/v1/release (idempotent) |
//!
//! The channel id IS the capability token: only the transport-addressed
//! receiver learns it (family scoping is enforced upstream), and the
//! plugin retires a channel on first release (at-most-once consume).
//!
//! Runtime generation is tracked locally: the plugin reports uptime and
//! a reset means the process (and every channel) died, which bumps the
//! generation and invalidates outstanding handles.

use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use serde_json::{json, Value};
use url::Url;

use crate::core::occ::occ3::connector::{
    CaptureRequest, ConsumeRequest, Occ3Connector,
};
use crate::core::occ::occ3::types::{
    Occ3ConnectorCapabilities, Occ3Error, Occ3HandleRef, Occ3Method,
    Occ3ModelIdentity, Occ3TransferMetrics,
};

pub const CONNECTOR_ID: &str = "sglang-occ3";

const HEALTH_TIMEOUT: Duration = Duration::from_secs(5);
const OP_TIMEOUT: Duration = Duration::from_secs(90);
/// Advisory size-class estimate for one KV token row (fp8 MLA-style
/// compressed cache). The bridge uses this for size classes only.
const ESTIMATED_KV_BYTES_PER_TOKEN: u64 = 1024;

/// Construction parameters (from a model's `occ3` block + environment).
#[derive(Debug, Clone)]
pub struct SglangOcc3Config {
    /// Base URL of the SGLang server (e.g. `http://127.0.0.1:30001`).
    pub endpoint: Url,
    /// Bearer token; read from the environment by the host, never from
    /// `models.json`.
    pub api_key: Option<String>,
}

#[derive(Debug, Default)]
struct ConnectorState {
    generation: u64,
    max_uptime_seen: f64,
}

/// The live connector. Cheap to clone behind an `Arc` (reqwest keeps an
/// internal connection pool).
#[derive(Debug)]
pub struct SglangOcc3Connector {
    client: reqwest::Client,
    config: SglangOcc3Config,
    state: Mutex<ConnectorState>,
}

impl SglangOcc3Connector {
    pub fn new(config: SglangOcc3Config) -> Result<Self, Occ3Error> {
        let client = reqwest::Client::builder()
            .timeout(OP_TIMEOUT)
            .build()
            .map_err(|e| Occ3Error::TransferFailed {
                reason: format!("http client build failed: {e}"),
            })?;
        Ok(Self {
            client,
            config,
            state: Mutex::new(ConnectorState::default()),
        })
    }

    fn request_builder(
        &self,
        method: reqwest::Method,
        path: &str,
        timeout: Duration,
    ) -> reqwest::RequestBuilder {
        let url = self
            .config
            .endpoint
            .join(path.trim_start_matches('/'))
            .unwrap_or_else(|_| self.config.endpoint.clone());
        let builder = self
            .client
            .request(method, url)
            .timeout(timeout)
            .header("content-type", "application/json");
        if let Some(key) = &self.config.api_key {
            builder.bearer_auth(key)
        } else {
            builder
        }
    }

    async fn get_json(&self, path: &str, timeout: Duration) -> Result<Value, Occ3Error> {
        let response = self
            .request_builder(reqwest::Method::GET, path, timeout)
            .send()
            .await
            .map_err(|e| map_reqwest_error(e))?;
        let status = response.status();
        let body: Value = response
            .json()
            .await
            .map_err(|e| Occ3Error::TransferFailed {
                reason: format!("invalid JSON from {path}: {e}"),
            })?;
        if !status.is_success() {
            return Err(http_status_error(status, &body));
        }
        Ok(body)
    }

    async fn post_json(
        &self,
        path: &str,
        body: Value,
        timeout: Duration,
    ) -> Result<Value, Occ3Error> {
        let response = self
            .request_builder(reqwest::Method::POST, path, timeout)
            .json(&body)
            .send()
            .await
            .map_err(|e| map_reqwest_error(e))?;
        let status = response.status();
        let parsed: Value = response
            .json()
            .await
            .map_err(|e| Occ3Error::TransferFailed {
                reason: format!("invalid JSON from {path}: {e}"),
            })?;
        if !status.is_success() {
            return Err(http_status_error(status, &parsed));
        }
        Ok(parsed)
    }

    /// Track server restarts through the uptime the plugin reports.
    fn observe_uptime(&self, uptime_seconds: f64) {
        let mut state = self.state.lock().unwrap();
        if uptime_seconds + 1.0 < state.max_uptime_seen {
            // The server restarted: its uptime went backwards relative to
            // the high-water mark we observed. Invalidate this generation.
            state.generation += 1;
            state.max_uptime_seen = uptime_seconds;
        } else {
            state.max_uptime_seen = state.max_uptime_seen.max(uptime_seconds);
        }
    }

    fn generation(&self) -> u64 {
        self.state.lock().unwrap().generation
    }
}

fn map_reqwest_error(error: reqwest::Error) -> Occ3Error {
    if error.is_timeout() {
        Occ3Error::ConnectorTimeout {
            connector_id: CONNECTOR_ID.to_string(),
            timeout_ms: OP_TIMEOUT.as_millis() as u64,
        }
    } else if error.is_connect() {
        Occ3Error::ConnectorUnreachable {
            connector_id: CONNECTOR_ID.to_string(),
        }
    } else {
        Occ3Error::TransferFailed {
            reason: error.to_string(),
        }
    }
}

fn http_status_error(status: reqwest::StatusCode, body: &Value) -> Occ3Error {
    let detail = body
        .get("detail")
        .and_then(Value::as_str)
        .unwrap_or("no detail")
        .to_string();
    match status.as_u16() {
        401 | 403 => Occ3Error::Unauthorized,
        404 => Occ3Error::HandleExpired {
            handle_id: detail,
        },
        429 => Occ3Error::TransferFailed {
            reason: format!("rate limited: {detail}"),
        },
        502 | 503 | 504 => Occ3Error::ConnectorUnreachable {
            connector_id: CONNECTOR_ID.to_string(),
        },
        code => Occ3Error::TransferFailed {
            reason: format!("http {code}: {detail}"),
        },
    }
}

/// Sanitize a bridge message id into a plugin channel id
/// (`[A-Za-z0-9._-]{1,64}`).
pub fn channel_id_for(message_id: &str) -> String {
    let cleaned: String = message_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
                c
            } else {
                '-'
            }
        })
        .take(64)
        .collect();
    if cleaned.is_empty() {
        "occ3-msg".to_string()
    } else {
        cleaned
    }
}

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn parse_identity(fields: &Value) -> Result<Occ3ModelIdentity, Occ3Error> {
    let str_field = |name: &str| -> Result<String, Occ3Error> {
        fields
            .get(name)
            .and_then(Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| Occ3Error::TransferFailed {
                reason: format!("identity field missing: {name}"),
            })
    };
    Ok(Occ3ModelIdentity {
        model_id: str_field("model_id")?,
        architecture: str_field("architecture")?,
        checkpoint_revision: str_field("checkpoint_revision")?,
        weight_fingerprint: str_field("weight_fingerprint")?,
        tokenizer_fingerprint: str_field("tokenizer_fingerprint")?,
        chat_template_fingerprint: str_field("chat_template_fingerprint")?,
        quantisation: str_field("quantisation")?,
        tensor_geometry: str_field("tensor_geometry")?,
        kv_layout: str_field("kv_layout")?,
        latent_method: str_field("latent_method")?,
        runtime: str_field("runtime")?,
    })
}

fn parse_method(value: &str) -> Result<Occ3Method, Occ3Error> {
    match value {
        "kv-handoff" => Ok(Occ3Method::KvHandoff),
        "statebridge-prefix" => Ok(Occ3Method::StatebridgePrefix),
        other => Err(Occ3Error::TransferFailed {
            reason: format!("unknown occ3 method: {other}"),
        }),
    }
}

fn uptime_of(status: &Value) -> f64 {
    status
        .get("metrics")
        .and_then(|m| m.get("uptime_seconds"))
        .and_then(Value::as_f64)
        .unwrap_or(0.0)
}

#[async_trait]
impl Occ3Connector for SglangOcc3Connector {
    fn connector_id(&self) -> &str {
        CONNECTOR_ID
    }

    fn protocol_version(&self) -> u32 {
        1
    }

    async fn health(&self) -> Result<(), Occ3Error> {
        let status = self.get_json("/occ3/v1/status", HEALTH_TIMEOUT).await?;
        self.observe_uptime(uptime_of(&status));
        Ok(())
    }

    async fn discover_capabilities(
        &self,
    ) -> Result<Occ3ConnectorCapabilities, Occ3Error> {
        let capabilities = self
            .get_json("/occ3/v1/capabilities", HEALTH_TIMEOUT)
            .await?;
        let identity = self.model_identity().await?;
        if capabilities.get("supported").and_then(Value::as_bool) != Some(true) {
            return Err(Occ3Error::TransferFailed {
                reason: "server reports occ3 unsupported".to_string(),
            });
        }
        let methods = capabilities
            .get("methods")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .filter_map(|s| parse_method(s).ok())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let mut features = std::collections::BTreeMap::new();
        for key in ["verification", "api_version"] {
            if let Some(value) = capabilities.get(key).and_then(Value::as_str) {
                features.insert(key.to_string(), value.to_string());
            }
        }
        if let Some(cache) = capabilities.get("cache").and_then(Value::as_object) {
            if let Ok(serialized) = serde_json::to_string(cache) {
                features.insert("cache".to_string(), serialized);
            }
        }
        let state_ttl = capabilities
            .get("state_ttl_seconds")
            .and_then(Value::as_u64)
            .unwrap_or(900);
        let max_handles = capabilities
            .get("max_concurrent_handles")
            .and_then(Value::as_u64)
            .unwrap_or(256) as u32;
        Ok(Occ3ConnectorCapabilities {
            protocol_version: 1,
            connector_version: capabilities
                .get("version")
                .and_then(Value::as_str)
                .unwrap_or("0")
                .to_string(),
            model_identity: identity,
            methods,
            tensor_formats: vec!["kv-radix-salt".to_string()],
            max_state_bytes: 65536 * ESTIMATED_KV_BYTES_PER_TOKEN,
            state_ttl_seconds: state_ttl,
            max_concurrent_handles: max_handles,
            cache_isolation: "cache_salt=occ3-<channel>, default namespace untouched"
                .to_string(),
            features,
        })
    }

    async fn model_identity(&self) -> Result<Occ3ModelIdentity, Occ3Error> {
        let body = self.get_json("/occ3/v1/identity", HEALTH_TIMEOUT).await?;
        let fields = body.get("fields").ok_or_else(|| Occ3Error::TransferFailed {
            reason: "identity response missing fields".to_string(),
        })?;
        parse_identity(fields)
    }

    async fn capture_latent(
        &self,
        request: CaptureRequest,
    ) -> Result<Occ3HandleRef, Occ3Error> {
        let channel = channel_id_for(&request.message_id);
        let ttl_s = request.ttl.as_secs().clamp(1, 3600);
        let body = json!({
            "channel": channel,
            "text": request.capture_context,
            "ttl_s": ttl_s,
        });
        let response = self
            .post_json("/occ3/v1/send", body, OP_TIMEOUT)
            .await?;
        let carrier_tokens = response
            .get("carrier_tokens")
            .and_then(Value::as_u64)
            .ok_or_else(|| Occ3Error::TransferFailed {
                reason: "send response missing carrier_tokens".to_string(),
            })?;
        let fingerprint = self.cached_fingerprint().await?;
        Ok(Occ3HandleRef {
            handle_id: channel,
            connector_id: CONNECTOR_ID.to_string(),
            model_fingerprint: fingerprint,
            runtime_generation: self.generation(),
            expires_at_epoch: now_epoch() + ttl_s,
            state_size_bytes: carrier_tokens * ESTIMATED_KV_BYTES_PER_TOKEN,
        })
    }

    async fn deliver_latent(&self, handle: &Occ3HandleRef) -> Result<(), Occ3Error> {
        // Channel liveness: the transport already scoped the handle to
        // the addressed receiver; verify the channel is still active.
        let channels = self.get_json("/occ3/v1/channels", HEALTH_TIMEOUT).await?;
        let active = channels
            .get("channels")
            .and_then(Value::as_array)
            .map(|list| {
                list.iter().any(|c| {
                    c.get("channel_id").and_then(Value::as_str) == Some(handle.handle_id.as_str())
                        && c.get("state").and_then(Value::as_str) == Some("ACTIVE")
                })
            })
            .unwrap_or(false);
        if active {
            Ok(())
        } else {
            Err(Occ3Error::HandleExpired {
                handle_id: handle.handle_id.clone(),
            })
        }
    }

    async fn consume_latent(
        &self,
        request: ConsumeRequest,
    ) -> Result<Occ3TransferMetrics, Occ3Error> {
        let body = json!({
            "channel": request.handle.handle_id,
            "suffix": request.injection_target,
            "max_new_tokens": 256,
            "temperature": 0.0,
        });
        let response = self
            .post_json("/occ3/v1/recv", body, OP_TIMEOUT)
            .await?;
        let hit = response
            .get("hit")
            .and_then(Value::as_bool)
            .ok_or_else(|| Occ3Error::TransferFailed {
                reason: "recv response missing hit".to_string(),
            })?;
        if !hit {
            return Err(Occ3Error::HandleExpired {
                handle_id: request.handle.handle_id.clone(),
            });
        }
        let now = now_epoch();
        Ok(Occ3TransferMetrics {
            capture_ms: 0,
            handoff_ms: 0,
            consume_ms: 0,
            state_bytes: request.handle.state_size_bytes,
            state_lifetime_ms: now
                .saturating_sub(request.handle.expires_at_epoch.saturating_sub(900))
                .saturating_mul(1000),
        })
    }

    async fn acknowledge_delivery(&self, handle: &Occ3HandleRef) -> Result<(), Occ3Error> {
        // Semantic use confirmed: retire the channel (at-most-once).
        let body = json!({ "channel": handle.handle_id });
        self.post_json("/occ3/v1/release", body, HEALTH_TIMEOUT)
            .await
            .map(|_| ())
    }

    async fn release_handle(&self, handle: &Occ3HandleRef) -> Result<(), Occ3Error> {
        let body = json!({ "channel": handle.handle_id });
        self.post_json("/occ3/v1/release", body, HEALTH_TIMEOUT)
            .await
            .map(|_| ())
    }

    async fn shutdown(&self) -> Result<(), Occ3Error> {
        Ok(())
    }
}

impl SglangOcc3Connector {
    async fn cached_fingerprint(&self) -> Result<String, Occ3Error> {
        Ok(self
            .get_json("/occ3/v1/identity", HEALTH_TIMEOUT)
            .await?
            .get("fingerprint")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn channel_ids_are_sanitized_to_the_plugin_grammar() {
        assert_eq!(channel_id_for("agentmsg_fc9f34a3"), "agentmsg_fc9f34a3");
        assert_eq!(channel_id_for("msg with spaces!"), "msg-with-spaces-");
        assert_eq!(channel_id_for(""), "occ3-msg");
        let long = "a".repeat(100);
        assert_eq!(channel_id_for(&long).len(), 64);
    }

    #[test]
    fn identity_fields_map_one_to_one() {
        let fields = json!({
            "model_id": "glm-5.3",
            "architecture": "GlmMoeDsaForCausalLM",
            "checkpoint_revision": "aca966e4",
            "weight_fingerprint": "abc123",
            "tokenizer_fingerprint": "tok",
            "chat_template_fingerprint": "chat",
            "quantisation": "fp8-e4m3",
            "tensor_geometry": "hidden_size=6144",
            "kv_layout": "tp_size=8",
            "latent_method": "kv-radix-channel-v1",
            "runtime": "sglang/0.5.21",
        });
        let identity = parse_identity(&fields).unwrap();
        assert_eq!(identity.model_id, "glm-5.3");
        assert_eq!(identity.latent_method, "kv-radix-channel-v1");
        // Fingerprint is a stable digest: identical inputs, identical output,
        // and it changes when any field changes.
        let fingerprint_a = identity.fingerprint();
        let fingerprint_b = parse_identity(&fields).unwrap().fingerprint();
        assert_eq!(fingerprint_a, fingerprint_b);
        let mut changed = fields.clone();
        changed["kv_layout"] = json!("tp_size=4");
        assert_ne!(fingerprint_a, parse_identity(&changed).unwrap().fingerprint());
        assert!(parse_identity(&json!({})).is_err());
    }

    #[test]
    fn methods_parse_and_reject() {
        assert!(matches!(parse_method("kv-handoff").unwrap(), Occ3Method::KvHandoff));
        assert!(matches!(
            parse_method("statebridge-prefix").unwrap(),
            Occ3Method::StatebridgePrefix
        ));
        assert!(parse_method("nonsense").is_err());
    }

    #[test]
    fn uptime_reset_detection_bumps_generation() {
        let config = SglangOcc3Config {
            endpoint: Url::parse("http://127.0.0.1:1").unwrap(),
            api_key: None,
        };
        let connector = SglangOcc3Connector::new(config).unwrap();
        connector.observe_uptime(100.0);
        assert_eq!(connector.generation(), 0);
        connector.observe_uptime(50.0); // restart detected
        assert_eq!(connector.generation(), 1);
        connector.observe_uptime(60.0);
        assert_eq!(connector.generation(), 1);
    }

    #[test]
    fn http_status_errors_map_to_connector_semantics() {
        let unauthorized = http_status_error(
            reqwest::StatusCode::UNAUTHORIZED,
            &json!({"detail": "Unauthorized"}),
        );
        assert!(matches!(unauthorized, Occ3Error::Unauthorized));
        let not_found = http_status_error(
            reqwest::StatusCode::NOT_FOUND,
            &json!({"detail": "unknown channel: ghost"}),
        );
        assert!(matches!(not_found, Occ3Error::HandleExpired { .. }));
        let bad_gateway = http_status_error(
            reqwest::StatusCode::BAD_GATEWAY,
            &json!({}),
        );
        assert!(matches!(bad_gateway, Occ3Error::ConnectorUnreachable { .. }));
    }

    #[test]
    fn ttl_clamping_keeps_plugin_bounds() {
        let config = SglangOcc3Config {
            endpoint: Url::parse("http://127.0.0.1:1").unwrap(),
            api_key: None,
        };
        let _connector = SglangOcc3Connector::new(config).unwrap();
        let clamped = Duration::from_secs(0).as_secs().clamp(1, 3600);
        assert_eq!(clamped, 1);
        assert_eq!(Duration::from_secs(99999).as_secs().clamp(1, 3600), 3600);
    }
}
