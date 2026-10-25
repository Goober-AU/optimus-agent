use super::*;
use base64::Engine;
use std::io::{Read, Write};

fn request(mode: &str) -> SearchRequest {
    serde_json::from_value(json!({"query":"fixture query", "mode":mode})).unwrap()
}

fn response() -> Value {
    json!({"status":"completed", "output":[
        {"type":"web_search_call", "status":"completed", "action":{"type":"search","sources":[
            {"url":"https://example.com/report?utm_source=openai"},
            {"url":"https://example.org/extra", "title":"Extra source"}]}},
        {"type":"message","content":[{"type":"output_text", "text":"A verified finding.", "annotations":[
            {"type":"url_citation","title":"Original title", "url":"https://example.com/report#section"},
            {"type":"url_citation","url":"javascript:alert(1)"},
            {"type":"url_citation","url":"https://user:secret@example.com/private"}]}]}]})
}

#[test]
fn codex_search_requests_hosted_search_and_preserves_citations() {
    let live = request("live");
    assert!(live.valid());
    assert_eq!(live.body()["tools"][0], json!({"type":"web_search","external_web_access":true}));
    assert_eq!(live.body()["tool_choice"], "required");
    assert_eq!(live.body()["store"], false);
    assert_eq!(live.body()["include"], json!(["web_search_call.action.sources"]));
    assert_eq!(request("cached").body()["tools"][0]["external_web_access"], false);
    let result = parse_response(&response(), 5);
    assert_eq!(result.status, "ok");
    assert_eq!(result.search_calls, 1);
    assert_eq!(result.sources.len(), 2);
    assert_eq!(result.sources[0].title, "Original title");
    assert_eq!(result.sources[0].url, "https://example.com/report");
    assert_eq!(parse_response(&response(), 1).sources.len(), 1);
    assert!(source_url("file:///etc/passwd").is_none());
    assert_eq!(source_url("https://EXAMPLE.com/?id=1&utm_medium=x#f").unwrap(), "https://example.com/?id=1");
    let mut unicode = request("live");
    unicode.query = "界".repeat(2700);
    assert!(unicode.valid(), "query limit is UTF-8 bytes, independent of JSON escaping");
}

#[test]
fn codex_search_requires_completed_search_and_sources() {
    let mut root = response();
    root["status"] = json!("incomplete");
    assert_eq!(parse_response(&root, 5).error.as_deref(), Some("incomplete_response"));
    root = response();
    root["output"].as_array_mut().unwrap().remove(0);
    assert_eq!(parse_response(&root, 5).error.as_deref(), Some("search_not_executed"));
    root = json!({"status":"completed","output":[{"type":"web_search_call","status":"completed"}]});
    assert_eq!(parse_response(&root, 5).error.as_deref(), Some("no_sources"));
    let mut many_parts = response();
    many_parts["output"][1]["content"] = json!((0..100).map(|_| json!({"type":"output_text","text":"界".repeat(1000)})).collect::<Vec<_>>());
    assert_eq!(parse_response(&many_parts, 5).summary.chars().count(), 16_000);
    for (key, value) in [("query", json!(" ")), ("num_results", json!(21)), ("timeout_seconds", json!(91)), ("model", json!("bad\nmodel"))] {
        let mut invalid = json!({"query":"fixture"});
        invalid[key] = value;
        assert!(!serde_json::from_value::<SearchRequest>(invalid).unwrap().valid());
    }
}

#[test]
fn codex_search_decodes_sse_and_rejects_failure_without_echoing_bodies() {
    let wire = format!(": heartbeat\r\nevent: response.created\r\ndata: {{\"type\":\"response.created\"}}\r\n\r\nevent: response.completed\r\ndata: {}\r\n\r\ndata: [DONE]\r\n\r\n",
        json!({"type":"response.completed","response":response()}));
    assert_eq!(decode_response(wire.as_bytes()).unwrap(), response());
    assert_eq!(decode_response(serde_json::to_string(&response()).unwrap().as_bytes()).unwrap(), response());
    assert_eq!(decode_response(b"data: {\"type\":\"error\",\"message\":\"SECRET\"}\n\n").unwrap_err(), "provider_error");
    assert_eq!(decode_response(b"data: invalid SECRET\n\n").unwrap_err(), "invalid_response");
    assert_eq!(decode_response(b"data: [DONE]\n\n").unwrap_err(), "incomplete_response");
}

#[test]
fn codex_search_assembles_streamed_items_when_terminal_output_is_empty() {
    let expected = response();
    let item = |index: usize| json!({"type":"response.output_item.done", "output_index":index, "item":expected["output"][index]});
    let prefix = format!("data: {}\n\ndata: {}\n\ndata: {}\n\n", item(1), item(0), item(0));
    let wire = format!("{prefix}data: {}\n\n", json!({"type":"response.completed", "response":{"status":"completed","output":[]}}));
    let decoded = decode_response(wire.as_bytes()).unwrap();
    assert_eq!(decoded, expected);
    let result = parse_response(&decoded, 5);
    assert_eq!(result.status, "ok");
    assert_eq!(result.search_calls, 1, "duplicate item events must not inflate completed searches");
    assert_eq!(result.sources[0].title, "Original title");
    assert_eq!(result.summary, "A verified finding.");
    assert_eq!(decode_response(prefix.as_bytes()).unwrap_err(), "incomplete_response");
}

#[tokio::test]
async fn codex_search_without_oauth_stays_offline() {
    let mut registry = ModelRegistry::in_memory(AuthStorage::in_memory(Default::default(), None));
    let result = search_with_auth(&request("live"), &mut registry, "http://127.0.0.1:1").await;
    assert_eq!(result.status, "unavailable");
    assert_eq!(result.error.as_deref(), Some("codex_login_required"));
}

#[tokio::test]
async fn codex_search_uses_existing_models_credential_command_without_stored_login() {
    let directory = tempfile::Builder::new().prefix("codex search credentials ").tempdir().unwrap();
    let credential = directory.path().join("synthetic-token.txt");
    std::fs::write(&credential, token()).unwrap();
    let command = if cfg!(windows) {
        let script = directory.path().join("credential.ps1");
        std::fs::write(&script, format!("Get-Content -Raw -LiteralPath '{}'\n",
            credential.display().to_string().replace('\'', "''"))).unwrap();
        format!("!powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"{}\"", script.display())
    } else {
        format!("!cat '{}'", credential.display().to_string().replace('\'', "'\\''"))
    };
    let models = directory.path().join("models.json");
    std::fs::write(&models, json!({"providers":{"openai-codex":{
        "apiKey":command, "models":[{"id":"gpt-6-astra"}]
    }}}).to_string()).unwrap();
    let mut registry = ModelRegistry::create(AuthStorage::in_memory(Default::default(), None),
        Some(models.to_string_lossy().into_owned()));
    assert!(registry.get_error().is_none(), "{:?}", registry.get_error());
    assert!(registry.auth_storage().get(PROVIDER).is_none());
    let (endpoint, server_thread) = server(200, serde_json::to_vec(&response()).unwrap());
    let result = search_with_auth(&request("live"), &mut registry, &endpoint).await;
    assert_eq!(result.status, "ok");
    assert_eq!(result.search_calls, 1);
    server_thread.join().unwrap();
    assert!(registry.auth_storage().get(PROVIDER).is_none(), "must not copy the shared credential");

    // Re-read the configured source on each request, including rotations and failures.
    std::fs::write(&credential, "invalid rotated synthetic token").unwrap();
    let failed = search_with_auth(&request("live"), &mut registry, "http://127.0.0.1:1").await;
    assert_eq!(failed.error.as_deref(), Some("codex_login_required"));
    std::fs::remove_file(&credential).unwrap();
    let missing = search_with_auth(&request("live"), &mut registry, "http://127.0.0.1:1").await;
    assert_eq!(missing.status, "unavailable");
}

fn token() -> String {
    let payload = json!({"https://api.openai.com/auth":{"chatgpt_account_id":"fixture-account"}});
    format!("header.{}.signature", base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload.to_string()))
}

fn server(status: u16, body: Vec<u8>) -> (String, std::thread::JoinHandle<()>) {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}/codex/responses", listener.local_addr().unwrap());
    let thread = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        let mut bytes = Vec::new();
        let body_start;
        loop {
            let mut chunk = [0; 4096];
            let n = stream.read(&mut chunk).unwrap();
            assert_ne!(n, 0);
            bytes.extend_from_slice(&chunk[..n]);
            assert!(bytes.len() < 32_768);
            if let Some(start) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                let headers = std::str::from_utf8(&bytes[..start]).unwrap().to_ascii_lowercase();
                assert!(headers.contains("chatgpt-account-id: fixture-account"));
                assert!(headers.contains("authorization: bearer "));
                let size: usize = headers.lines().find_map(|line| line.strip_prefix("content-length: ")).unwrap().parse().unwrap();
                if bytes.len() >= start + 4 + size { body_start = start + 4; break; }
            }
        }
        let request: Value = serde_json::from_slice(&bytes[body_start..]).unwrap();
        assert_eq!(request["tools"][0]["type"], "web_search");
        assert_eq!(request["tool_choice"], "required");
        write!(stream, "HTTP/1.1 {status} Fixture\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
        let _ = stream.write_all(&body);
    });
    (endpoint, thread)
}

#[tokio::test]
async fn codex_search_http_transport_handles_success_errors_and_limits() {
    let body = format!("data: {}\n\ndata: [DONE]\n\n", json!({"type":"response.completed","response":response()}));
    let (endpoint, server_thread) = server(200, body.into_bytes());
    let result = request_search(&request("live"), &token(), &endpoint).await;
    assert_eq!(result.status, "ok");
    assert_eq!(result.sources.len(), 2);
    server_thread.join().unwrap();
    for (status, expected) in [(401, "codex_login_required"), (429, "rate_limited"), (500, "http_error")] {
        let (endpoint, server_thread) = server(status, b"upstream SECRET credential".to_vec());
        let result = request_search(&request("live"), &token(), &endpoint).await;
        assert_eq!(result.error.as_deref(), Some(expected));
        assert!(!serde_json::to_string(&result).unwrap().contains("SECRET"));
        server_thread.join().unwrap();
    }
    let (endpoint, server_thread) = server(200, vec![b'x'; MAX_RESPONSE_BYTES + 1]);
    assert_eq!(request_search(&request("live"), &token(), &endpoint).await.error.as_deref(), Some("response_too_large"));
    server_thread.join().unwrap();
}
