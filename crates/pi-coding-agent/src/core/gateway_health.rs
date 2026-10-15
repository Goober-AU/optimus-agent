//! Local model-gateway health probing and recovery.
//!
//! Hosts that run their models through a local gateway (for example a
//! `node.exe` listener on `127.0.0.1:43120`) lose every provider call when the
//! gateway process dies: base URLs like `http://127.0.0.1:43120/azure-openai/v1`
//! then time out with connection-level errors, and long-running background
//! jobs (memory extraction, refinement review) fail one after another.
//!
//! This module answers three questions for such providers:
//!
//! 1. Is a base URL a loopback gateway? ([`is_loopback_base_url`])
//! 2. Is a completed call a connection-level failure? ([`looks_like_connection_failure`])
//! 3. Can the gateway be restarted and the call retried?
//!    ([`ensure_gateway_with`] / [`ensure_gateway`] / [`complete_with_gateway_recovery`])
//!
//! All waits are bounded by [`LocalGatewaySettings`] timeouts, and no function
//! in this module panics on malformed input.

use std::net::{Ipv4Addr, Ipv6Addr};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use futures_util::future::BoxFuture;
use pi_ai::types::{AssistantMessage, STOP_REASON_ERROR};
use serde_json::Value;

const DEFAULT_PROBE_TIMEOUT_MS: u64 = 1500;
const DEFAULT_START_TIMEOUT_MS: u64 = 20000;
/// How often the recovery poll re-checks the port while waiting for the
/// start command's gateway to come up.
const PORT_POLL_INTERVAL: Duration = Duration::from_millis(200);
/// Grace window after spawning the start command, long enough to observe an
/// immediate non-zero exit (bad path, syntax error) without waiting out the
/// whole port-poll budget.
const START_COMMAND_GRACE: Duration = Duration::from_millis(250);

/// `localGateway` settings block (camelCase keys in settings.json).
///
/// ```json
/// {
///   "localGateway": {
///     "autoStart": true,
///     "startCommand": "node C:/gateways/main.js",
///     "probeTimeoutMs": 1500,
///     "startTimeoutMs": 20000
///   }
/// }
/// ```
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalGatewaySettings {
    /// settings key `localGateway.autoStart` (default false).
    pub auto_start: bool,
    /// settings key `localGateway.startCommand`; shell command that starts
    /// the gateway (default absent).
    pub start_command: Option<String>,
    /// Per-attempt TCP probe timeout in milliseconds (default 1500).
    pub probe_timeout_ms: u64,
    /// How long to wait for the started gateway's port to come up, in
    /// milliseconds (default 20000).
    pub start_timeout_ms: u64,
}

impl Default for LocalGatewaySettings {
    fn default() -> Self {
        Self {
            auto_start: false,
            start_command: None,
            probe_timeout_ms: DEFAULT_PROBE_TIMEOUT_MS,
            start_timeout_ms: DEFAULT_START_TIMEOUT_MS,
        }
    }
}

impl LocalGatewaySettings {
    /// Read from a settings.json-shaped [`serde_json::Value`] at key
    /// `"localGateway"` (camelCase).
    ///
    /// Accepts either the whole settings object (the `"localGateway"` value
    /// is looked up inside it) or the `"localGateway"` sub-object itself.
    /// Missing keys, non-object values, or zero timeouts fall back to the
    /// defaults; a blank `startCommand` is treated as unconfigured.
    pub fn from_settings_value(value: Option<&Value>) -> Self {
        let mut settings = Self::default();
        let Some(value) = value else {
            return settings;
        };
        let gateway = value.get("localGateway").unwrap_or(value);
        let Some(gateway) = gateway.as_object() else {
            return settings;
        };
        if let Some(auto_start) = gateway.get("autoStart").and_then(Value::as_bool) {
            settings.auto_start = auto_start;
        }
        if let Some(command) = gateway.get("startCommand").and_then(Value::as_str) {
            let command = command.trim();
            if !command.is_empty() {
                settings.start_command = Some(command.to_string());
            }
        }
        if let Some(probe_timeout_ms) = read_timeout_ms(gateway, "probeTimeoutMs") {
            settings.probe_timeout_ms = probe_timeout_ms;
        }
        if let Some(start_timeout_ms) = read_timeout_ms(gateway, "startTimeoutMs") {
            settings.start_timeout_ms = start_timeout_ms;
        }
        settings
    }
}

/// Read a positive millisecond timeout (integer or float) from a settings map.
fn read_timeout_ms(gateway: &serde_json::Map<String, Value>, key: &str) -> Option<u64> {
    let number = gateway.get(key)?.as_f64()?;
    if !number.is_finite() || number <= 0.0 {
        return None;
    }
    Some(number.min(u64::MAX as f64) as u64)
}

/// True for `http(s)://127.0.0.1:*`, `http://localhost:*`, and `http://[::1]:*`
/// base URLs (any port, port optional; host matching is case-insensitive and
/// covers the whole IPv4 loopback range `127.0.0.0/8`).
pub fn is_loopback_base_url(base_url: &str) -> bool {
    match split_authority(base_url) {
        Some((host, _, _)) => is_loopback_host(&host),
        None => false,
    }
}

/// True when an [`AssistantMessage`] is a connection-level failure:
/// `stop_reason == "error"` and the error message mentions `"Connection error"`
/// or `"timed out"` (the exact texts produced by the pi-ai provider transports
/// in `openai_completions.rs` `post_chat_completions`: `Connection error: ...`
/// and `Request timed out.`).
pub fn looks_like_connection_failure(message: &AssistantMessage) -> bool {
    if message.stop_reason != STOP_REASON_ERROR {
        return false;
    }
    let Some(error) = message.error_message.as_deref() else {
        return false;
    };
    let error = error.to_ascii_lowercase();
    error.contains("connection error") || error.contains("timed out")
}

/// What [`ensure_gateway_with`] / [`ensure_gateway`] did about the gateway.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GatewayRecoveryOutcome {
    /// Not a loopback URL; no gateway recovery applies.
    NotApplicable,
    /// The probe succeeded; the gateway was alive.
    AlreadyUp,
    /// The start command ran and the port came up within `start_timeout_ms`.
    Recovered,
    /// Recovery was attempted or considered and failed; carries an actionable
    /// reason.
    Failed(String),
}

/// Factory that starts the local gateway and resolves once the start attempt
/// itself has finished (successfully or not). It does not need to wait for the
/// port to come up; the caller polls for that.
pub type GatewayStarter = std::sync::Arc<
    dyn Fn() -> futures_util::future::BoxFuture<'static, Result<(), String>> + Send + Sync,
>;

/// Probe loopback `host:port`; if down and configured, run `starter`, then
/// poll the port until `start_timeout_ms`.
///
/// - Non-loopback or unparseable base URL -> [`GatewayRecoveryOutcome::NotApplicable`].
/// - Port alive -> [`GatewayRecoveryOutcome::AlreadyUp`] (`starter` untouched).
/// - Port down and `auto_start` disabled or `start_command` unconfigured ->
///   [`GatewayRecoveryOutcome::Failed`] with a loud, actionable reason.
/// - Otherwise the starter runs (bounded by `start_timeout_ms`), then the port
///   is polled until `start_timeout_ms` elapses.
pub async fn ensure_gateway_with(
    base_url: &str,
    settings: &LocalGatewaySettings,
    starter: GatewayStarter,
) -> GatewayRecoveryOutcome {
    let Some((host, port)) = host_port(base_url) else {
        return GatewayRecoveryOutcome::NotApplicable;
    };
    if !is_loopback_host(&host) {
        return GatewayRecoveryOutcome::NotApplicable;
    }
    let probe_timeout = Duration::from_millis(settings.probe_timeout_ms.max(1));
    if probe_once(&host, port, probe_timeout).await {
        return GatewayRecoveryOutcome::AlreadyUp;
    }
    let configured = settings.auto_start
        && settings
            .start_command
            .as_deref()
            .map(|command| !command.trim().is_empty())
            .unwrap_or(false);
    if !configured {
        return GatewayRecoveryOutcome::Failed(
            "local gateway down and auto-start disabled or unconfigured".to_string(),
        );
    }
    let start_timeout = Duration::from_millis(settings.start_timeout_ms);
    match tokio::time::timeout(start_timeout, starter()).await {
        Err(_) => {
            return GatewayRecoveryOutcome::Failed(format!(
                "start command did not finish within {}ms",
                settings.start_timeout_ms
            ));
        }
        Ok(Err(error)) => {
            return GatewayRecoveryOutcome::Failed(format!("start command failed: {error}"));
        }
        Ok(Ok(())) => {}
    }
    let deadline = tokio::time::Instant::now() + start_timeout;
    loop {
        if probe_once(&host, port, probe_timeout).await {
            return GatewayRecoveryOutcome::Recovered;
        }
        if tokio::time::Instant::now() >= deadline {
            return GatewayRecoveryOutcome::Failed(format!(
                "local gateway did not come up within {}ms after running the start command",
                settings.start_timeout_ms
            ));
        }
        tokio::time::sleep(PORT_POLL_INTERVAL).await;
    }
}

/// Convenience wrapper over [`ensure_gateway_with`] that builds the starter
/// from `settings.start_command` via [`tokio::process::Command`] (shell
/// invocation: `cmd /C <command>` on Windows, `sh -c <command>` elsewhere).
///
/// The command is spawned detached from our stdio and never awaited to exit;
/// a short grace window turns immediate non-zero exits into a
/// [`GatewayRecoveryOutcome::Failed`] instead of burning the poll budget.
pub async fn ensure_gateway(base_url: &str, settings: &LocalGatewaySettings) -> GatewayRecoveryOutcome {
    let Some(command) = settings
        .start_command
        .as_deref()
        .map(str::trim)
        .filter(|command| !command.is_empty())
    else {
        // No usable command: run the shared gate so the failure text matches
        // ensure_gateway_with.
        let noop: GatewayStarter = Arc::new(|| Box::pin(async { Ok(()) }));
        return ensure_gateway_with(base_url, settings, noop).await;
    };
    let command = command.to_string();
    let starter: GatewayStarter = Arc::new(move || {
        let command = command.clone();
        Box::pin(async move {
            let mut process = shell_command(&command);
            let mut child = process
                .spawn()
                .map_err(|error| format!("failed to spawn start command {command:?}: {error}"))?;
            // Grace window: catch commands that fail immediately (bad path,
            // syntax error) instead of waiting out the port-poll budget.
            tokio::time::sleep(START_COMMAND_GRACE).await;
            match child.try_wait() {
                Ok(Some(status)) if !status.success() => Err(format!(
                    "start command exited with {status}"
                )),
                Ok(_) => Ok(()),
                Err(error) => Err(format!("failed to poll start command: {error}")),
            }
        })
    });
    ensure_gateway_with(base_url, settings, starter).await
}

/// Retry wrapper used at completion call sites.
///
/// Runs `call()`; if the result is not a connection-level failure it is
/// returned unchanged. Otherwise the gateway is probed/recovered with
/// [`ensure_gateway`] and:
///
/// - `AlreadyUp`/`Recovered`: the call is retried once and the second result
///   is returned. If the retry still fails, `" [local gateway was recovered
///   but the retry still failed]"` is appended to its error message.
/// - Anything else: the FIRST result is returned with `" [local gateway
///   recovery: <reason>]"` appended, so the failure is loud and actionable.
///
/// Never panics; all waits are bounded by the settings timeouts.
pub async fn complete_with_gateway_recovery<F, Fut>(
    base_url: &str,
    settings: &LocalGatewaySettings,
    call: F,
) -> AssistantMessage
where
    F: Fn() -> Fut,
    Fut: std::future::Future<Output = AssistantMessage>,
{
    let first = call().await;
    if !looks_like_connection_failure(&first) {
        return first;
    }
    match ensure_gateway(base_url, settings).await {
        GatewayRecoveryOutcome::AlreadyUp | GatewayRecoveryOutcome::Recovered => {
            let mut second = call().await;
            if second.stop_reason == STOP_REASON_ERROR {
                append_error_note(
                    &mut second,
                    "[local gateway was recovered but the retry still failed]",
                );
            }
            second
        }
        GatewayRecoveryOutcome::NotApplicable => {
            let mut first = first;
            append_error_note(
                &mut first,
                "[local gateway recovery: not a loopback base URL]",
            );
            first
        }
        GatewayRecoveryOutcome::Failed(reason) => {
            let mut first = first;
            append_error_note(&mut first, &format!("[local gateway recovery: {reason}]"));
            first
        }
    }
}

/// Append a bracketed note to an [`AssistantMessage`]'s error message,
/// creating one when absent, so the failure stays loud and auditable.
fn append_error_note(message: &mut AssistantMessage, note: &str) {
    let combined = match message.error_message.take() {
        Some(existing) if !existing.trim().is_empty() => format!("{existing} {note}"),
        _ => note.to_string(),
    };
    message.error_message = Some(combined);
}

/// True for `localhost`, IPv4 loopback addresses (127.0.0.0/8), and the IPv6
/// loopback address `::1`, case-insensitively.
fn is_loopback_host(host: &str) -> bool {
    if host.eq_ignore_ascii_case("localhost") {
        return true;
    }
    if let Ok(address) = host.parse::<Ipv4Addr>() {
        return address.is_loopback();
    }
    if let Ok(address) = host.parse::<Ipv6Addr>() {
        return address.is_loopback();
    }
    false
}

/// Split an absolute `http(s)://[user@]host[:port]` base URL into
/// `(host, explicit port, is_https)`. Returns `None` for anything else.
fn split_authority(base_url: &str) -> Option<(String, Option<u16>, bool)> {
    let trimmed = base_url.trim();
    let scheme_end = trimmed.find("://")?;
    let scheme = trimmed[..scheme_end].to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return None;
    }
    let rest = &trimmed[scheme_end + 3..];
    let authority_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let mut authority = &rest[..authority_end];
    if let Some(at) = authority.rfind('@') {
        authority = &authority[at + 1..];
    }
    if authority.is_empty() {
        return None;
    }
    if let Some(open) = authority.find('[') {
        // Bracketed IPv6 literal: `[::1]` or `[::1]:port`.
        let close = authority.find(']')?;
        let host = &authority[open + 1..close];
        // The bracketed host may be followed by `:port`, by nothing, or by
        // garbage (which makes the whole authority invalid).
        let port = match authority[close + 1..].strip_prefix(':') {
            Some(raw) => Some(parse_port(raw)?),
            None => None,
        };
        return Some((host.to_string(), port, scheme == "https"));
    }
    match authority.rfind(':') {
        Some(colon) => {
            let host = &authority[..colon];
            if host.is_empty() {
                return None;
            }
            let port = parse_port(&authority[colon + 1..])?;
            Some((host.to_string(), Some(port), scheme == "https"))
        }
        None => Some((authority.to_string(), None, scheme == "https")),
    }
}

/// `(host, port)` with the scheme default applied (80 for http, 443 for https).
fn host_port(base_url: &str) -> Option<(String, u16)> {
    let (host, port, https) = split_authority(base_url)?;
    let port = port.unwrap_or(if https { 443 } else { 80 });
    Some((host, port))
}

fn parse_port(raw: &str) -> Option<u16> {
    if raw.is_empty() || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    raw.parse::<u16>().ok()
}

/// One bounded TCP connect attempt. The `(host, port)` form resolves IP
/// literals directly (no DNS) and tries every address a named host such as
/// `localhost` resolves to, all inside the timeout.
async fn probe_once(host: &str, port: u16, timeout: Duration) -> bool {
    let connect = tokio::net::TcpStream::connect((host, port));
    matches!(tokio::time::timeout(timeout, connect).await, Ok(Ok(_)))
}

/// Build the shell invocation for the configured start command.
fn shell_command(command: &str) -> tokio::process::Command {
    let mut process = if cfg!(windows) {
        let mut process = tokio::process::Command::new("cmd");
        process.arg("/C").arg(command);
        process
    } else {
        let mut process = tokio::process::Command::new("sh");
        process.arg("-c").arg(command);
        process
    };
    // The gateway owns its own lifetime; keep it off our stdio so a chatty
    // start command cannot block on a pipe nobody drains.
    process
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    process
}

#[cfg(test)]
mod tests {
    use super::*;
    use pi_ai::types::STOP_REASON_ERROR;
    use serde_json::json;
    use std::cell::Cell;

    fn connection_failure(error: &str) -> AssistantMessage {
        let mut message = AssistantMessage::default();
        message.stop_reason = STOP_REASON_ERROR.to_string();
        message.error_message = Some(error.to_string());
        message
    }

    fn failing_starter(reason: &str) -> GatewayStarter {
        let reason = reason.to_string();
        Arc::new(move || {
            let reason = reason.clone();
            Box::pin(async move { Err(reason) })
        })
    }

    fn recovery_settings() -> LocalGatewaySettings {
        LocalGatewaySettings {
            auto_start: true,
            start_command: Some("unused-start-command".to_string()),
            probe_timeout_ms: 500,
            start_timeout_ms: 5000,
        }
    }

    /// Reserve an ephemeral loopback port and free it again so tests can
    /// treat it as "down" (or let a starter bind it).
    async fn reserve_ephemeral_port() -> u16 {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        port
    }

    #[test]
    fn is_loopback_base_url_matches_loopback_hosts_only() {
        for base_url in [
            "http://127.0.0.1:43120",
            "https://127.0.0.1:43120/azure-openai/v1",
            "http://127.0.0.1",
            "http://LOCALHOST:43120",
            "http://localhost:43120/v1/",
            "https://localhost",
            "http://[::1]:43120",
            "https://[::1]:43120/azure-openai/v1",
            "http://[::1]",
            "http://user:token@127.0.0.1:43120/v1",
        ] {
            assert!(is_loopback_base_url(base_url), "{base_url} should be loopback");
        }
        for base_url in [
            "https://api.example.com",
            "https://api.example.com/v1",
            "http://192.168.1.5:43120",
            "http://10.0.0.1:43120",
            "garbage",
            "",
            "localhost:43120",
            "ftp://127.0.0.1:43120",
            "http://example.com#http://127.0.0.1",
            "http://:43120",
        ] {
            assert!(
                !is_loopback_base_url(base_url),
                "{base_url} should not be loopback"
            );
        }
    }

    #[test]
    fn looks_like_connection_failure_matches_transport_errors_only() {
        // Exact transport texts produced by pi-ai providers.
        assert!(looks_like_connection_failure(&connection_failure(
            "Connection error: error sending request for url (http://127.0.0.1:43120/)"
        )));
        assert!(looks_like_connection_failure(&connection_failure(
            "Request timed out."
        )));
        // Normal completions and non-transport errors are not recovery
        // candidates.
        assert!(!looks_like_connection_failure(&AssistantMessage::default()));
        assert!(!looks_like_connection_failure(&connection_failure(
            "Provider finish_reason: content_filter"
        )));
        assert!(!looks_like_connection_failure(&connection_failure(
            "No API key for provider: local"
        )));
        // A connection-shaped message with a healthy stop reason is not a
        // failure at all.
        let mut wrong_stop = AssistantMessage::default();
        wrong_stop.error_message = Some("Connection error: x".to_string());
        assert!(!looks_like_connection_failure(&wrong_stop));
        // Error stop reason without a message cannot be classified.
        let mut no_message = AssistantMessage::default();
        no_message.stop_reason = STOP_REASON_ERROR.to_string();
        assert!(!looks_like_connection_failure(&no_message));
    }

    #[test]
    fn local_gateway_settings_read_camel_case_keys_and_default_safely() {
        let defaults = LocalGatewaySettings::from_settings_value(None);
        assert_eq!(
            defaults,
            LocalGatewaySettings {
                auto_start: false,
                start_command: None,
                probe_timeout_ms: 1500,
                start_timeout_ms: 20000,
            }
        );

        let settings = json!({
            "localGateway": {
                "autoStart": true,
                "startCommand": "node C:/gateways/main.js",
                "probeTimeoutMs": 250,
                "startTimeoutMs": 5000,
            }
        });
        let parsed = LocalGatewaySettings::from_settings_value(Some(&settings));
        assert!(parsed.auto_start);
        assert_eq!(parsed.start_command.as_deref(), Some("node C:/gateways/main.js"));
        assert_eq!(parsed.probe_timeout_ms, 250);
        assert_eq!(parsed.start_timeout_ms, 5000);

        // The localGateway sub-object can be passed directly.
        let sub_object = &settings["localGateway"];
        assert_eq!(LocalGatewaySettings::from_settings_value(Some(sub_object)), parsed);

        // Unknown shapes and partial objects fall back to defaults.
        let invalid = json!({ "localGateway": "not-an-object" });
        assert_eq!(
            LocalGatewaySettings::from_settings_value(Some(&invalid)),
            LocalGatewaySettings::default()
        );
        let partial = json!({ "localGateway": { "autoStart": true } });
        let parsed_partial = LocalGatewaySettings::from_settings_value(Some(&partial));
        assert!(parsed_partial.auto_start);
        assert_eq!(parsed_partial.start_command, None);
        assert_eq!(parsed_partial.probe_timeout_ms, 1500);
        assert_eq!(parsed_partial.start_timeout_ms, 20000);
        let blank_command = json!({ "localGateway": { "autoStart": true, "startCommand": "   " } });
        let parsed_blank = LocalGatewaySettings::from_settings_value(Some(&blank_command));
        assert_eq!(parsed_blank.start_command, None);
    }

    #[tokio::test]
    async fn ensure_gateway_with_reports_already_up_for_a_live_listener() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let base_url = format!("http://127.0.0.1:{port}");
        // The starter fails loudly if it is ever run, proving AlreadyUp
        // short-circuits before any start attempt.
        let outcome = ensure_gateway_with(&base_url, &recovery_settings(), failing_starter("must not start")).await;
        assert_eq!(outcome, GatewayRecoveryOutcome::AlreadyUp);
    }

    #[tokio::test]
    async fn ensure_gateway_with_recovers_when_the_starter_binds_the_port() {
        let port = reserve_ephemeral_port().await;
        let base_url = format!("http://127.0.0.1:{port}");
        let starter: GatewayStarter = {
            let port = port;
            Arc::new(move || {
                let port = port;
                Box::pin(async move {
                    // Simulate a gateway that takes a moment to boot.
                    tokio::time::sleep(Duration::from_millis(150)).await;
                    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
                        .await
                        .map_err(|error| format!("bind failed: {error}"))?;
                    tokio::spawn(async move {
                        loop {
                            let _ = listener.accept().await;
                        }
                    });
                    Ok(())
                })
            })
        };
        let outcome = ensure_gateway_with(&base_url, &recovery_settings(), starter).await;
        assert_eq!(outcome, GatewayRecoveryOutcome::Recovered);
    }

    #[tokio::test]
    async fn ensure_gateway_with_fails_loudly_when_the_starter_errors() {
        let port = reserve_ephemeral_port().await;
        let base_url = format!("http://127.0.0.1:{port}");
        let outcome =
            ensure_gateway_with(&base_url, &recovery_settings(), failing_starter("gateway launcher missing")).await;
        match outcome {
            GatewayRecoveryOutcome::Failed(reason) => {
                assert!(reason.contains("gateway launcher missing"), "reason: {reason}");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn ensure_gateway_with_fails_when_auto_start_is_disabled() {
        let port = reserve_ephemeral_port().await;
        let base_url = format!("http://127.0.0.1:{port}");
        let outcome = ensure_gateway_with(
            &base_url,
            &LocalGatewaySettings::default(),
            failing_starter("must not start"),
        )
        .await;
        assert_eq!(
            outcome,
            GatewayRecoveryOutcome::Failed(
                "local gateway down and auto-start disabled or unconfigured".to_string()
            )
        );
    }

    #[tokio::test]
    async fn ensure_gateway_with_is_not_applicable_for_remote_urls() {
        let outcome = ensure_gateway_with(
            "https://api.example.com/v1",
            &recovery_settings(),
            failing_starter("must not start"),
        )
        .await;
        assert_eq!(outcome, GatewayRecoveryOutcome::NotApplicable);
    }

    #[tokio::test]
    async fn ensure_gateway_fails_when_the_start_command_cannot_run() {
        let port = reserve_ephemeral_port().await;
        let base_url = format!("http://127.0.0.1:{port}");
        let settings = LocalGatewaySettings {
            auto_start: true,
            start_command: Some("optimus-gateway-definitely-not-a-command-9f2c".to_string()),
            probe_timeout_ms: 300,
            // Short enough to keep the test fast, long enough for the spawn
            // grace window to observe the non-zero exit.
            start_timeout_ms: 1500,
        };
        match ensure_gateway(&base_url, &settings).await {
            GatewayRecoveryOutcome::Failed(reason) => {
                assert!(!reason.is_empty(), "failure must carry a reason");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn complete_with_gateway_recovery_passes_through_healthy_results() {
        let healthy = AssistantMessage::default();
        let calls = Cell::new(0u32);
        let result = complete_with_gateway_recovery(
            "http://127.0.0.1:1",
            &LocalGatewaySettings::default(),
            || {
                calls.set(calls.get() + 1);
                std::future::ready(healthy.clone())
            },
        )
        .await;
        assert_eq!(calls.get(), 1);
        assert_eq!(result, healthy);
    }

    #[tokio::test]
    async fn complete_with_gateway_recovery_retries_once_when_the_gateway_is_already_up() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let base_url = format!("http://127.0.0.1:{port}");
        let calls = Cell::new(0u32);
        let result = complete_with_gateway_recovery(
            &base_url,
            &LocalGatewaySettings::default(),
            || {
                let attempt = calls.get();
                calls.set(attempt + 1);
                if attempt == 0 {
                    std::future::ready(connection_failure("Connection error: tcp connect error"))
                } else {
                    std::future::ready(AssistantMessage::default())
                }
            },
        )
        .await;
        assert_eq!(calls.get(), 2, "the call must be retried exactly once");
        assert_eq!(result, AssistantMessage::default());
    }

    #[tokio::test]
    async fn complete_with_gateway_recovery_extends_the_first_error_when_recovery_fails() {
        let port = reserve_ephemeral_port().await;
        let base_url = format!("http://127.0.0.1:{port}");
        let calls = Cell::new(0u32);
        let result = complete_with_gateway_recovery(
            &base_url,
            &LocalGatewaySettings::default(),
            || {
                calls.set(calls.get() + 1);
                std::future::ready(connection_failure("Connection error: tcp connect error"))
            },
        )
        .await;
        assert_eq!(calls.get(), 1, "a failed recovery must not retry the call");
        assert_eq!(result.stop_reason, STOP_REASON_ERROR);
        assert_eq!(
            result.error_message.as_deref(),
            Some(
                "Connection error: tcp connect error [local gateway recovery: local gateway down and auto-start disabled or unconfigured]"
            )
        );
    }

    #[tokio::test]
    async fn complete_with_gateway_recovery_notes_missing_retry_on_second_failure() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let base_url = format!("http://127.0.0.1:{port}");
        let calls = Cell::new(0u32);
        let result = complete_with_gateway_recovery(
            &base_url,
            &LocalGatewaySettings::default(),
            || {
                calls.set(calls.get() + 1);
                std::future::ready(connection_failure("Connection error: tcp connect error"))
            },
        )
        .await;
        assert_eq!(calls.get(), 2);
        assert_eq!(
            result.error_message.as_deref(),
            Some(
                "Connection error: tcp connect error [local gateway was recovered but the retry still failed]"
            )
        );
    }

    #[tokio::test]
    async fn complete_with_gateway_recovery_skips_recovery_for_remote_urls() {
        let calls = Cell::new(0u32);
        let result = complete_with_gateway_recovery(
            "https://api.example.com/v1",
            &LocalGatewaySettings::default(),
            || {
                calls.set(calls.get() + 1);
                std::future::ready(connection_failure("Request timed out."))
            },
        )
        .await;
        assert_eq!(calls.get(), 1);
        assert_eq!(
            result.error_message.as_deref(),
            Some(
                "Request timed out. [local gateway recovery: not a loopback base URL]"
            )
        );
    }
}
