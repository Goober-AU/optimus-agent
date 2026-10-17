//! Process-wide pooled reqwest clients keyed by builder policy.
//!
//! A per-request `reqwest::Client` drops its connection pool with it, so every provider turn
//! paid TCP + TLS setup again. Clients here are cached per builder policy (the redirect,
//! HTTP version and proxy options that vary per provider family) and kept for the process
//! lifetime, so a warm turn reuses the pooled connection instead of handshaking.
//!
//! Invariants:
//! - Credentials are PER REQUEST (Authorization headers), never stored on a client, matching
//!   the `responses_transport::http_client` precedent and its reuse test.
//! - No `ClientBuilder::timeout` is ever set here: that option is a TOTAL deadline
//!   (reqwest client.rs `RequestBuilder::timeout`) and would impose one deadline on every
//!   request sharing the client. Header-only wrapping stays at the call sites
//!   (`openai_completions::post_chat_completions`), total timeouts stay on the request
//!   builders that already pass them.
//! - Pool sizing follows the in-repo pooled precedent: 90s idle retention, otherwise reqwest
//!   defaults.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

/// Idle-connection retention, matching the pooled `responses_transport` client.
const POOL_IDLE_TIMEOUT: Duration = Duration::from_secs(90);

/// The builder options that vary per provider family; equal policies share one client.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub(crate) enum ClientPolicy {
	/// reqwest defaults: standard redirects, HTTP/2 allowed. The plain OpenAI-compatible,
	/// anthropic, google, google-vertex, mistral, azure and codex SSE paths.
	Default,
	/// `redirect(Policy::none())`: the responses transport's opted-in providers and the
	/// bedrock responses client (signed requests that must not follow redirects).
	RedirectNone,
	/// Bedrock converse-stream: `redirect(Policy::none())` plus the conditional
	/// `http1_only()` / `no_proxy()` options resolved from the environment.
	Bedrock { force_http1: bool, no_proxy: bool },
	/// Kiro: `redirect(Policy::none())` + `connect_timeout(15s)`.
	Kiro,
}

static CLIENTS: OnceLock<Mutex<HashMap<ClientPolicy, Result<reqwest::Client, String>>>> = OnceLock::new();

/// Pooled client for the policy. Build failures are cached, so every caller of a failed
/// policy sees the same error string the first build produced.
pub(crate) fn try_shared_client(policy: ClientPolicy) -> Result<reqwest::Client, String> {
	let clients = CLIENTS.get_or_init(|| Mutex::new(HashMap::new()));
	let mut guard = clients.lock().unwrap();
	if let Some(cached) = guard.get(&policy) {
		return cached.clone();
	}
	let built = build(policy).map_err(|error| error.to_string());
	guard.insert(policy, built.clone());
	built
}

/// Infallible form for the sites that previously used `reqwest::Client::new()` or an
/// `expect`-ing pooled builder: a client-construction failure stays fatal.
pub(crate) fn shared_client(policy: ClientPolicy) -> reqwest::Client {
	try_shared_client(policy).expect("Shared HTTP client")
}

impl ClientPolicy {
	/// The builder options this policy represents, in pre-build form. Bedrock's
	/// client-builder tests assert on this Debug output, and the cached client is built
	/// from the same expression, so the options cannot drift between the two.
	pub(crate) fn builder(self) -> reqwest::ClientBuilder {
		// reqwest's default `pool_idle_timeout` is already 90s; set it explicitly so the
		// pooling policy of every shared client is stated, not inherited.
		let builder = reqwest::Client::builder().pool_idle_timeout(POOL_IDLE_TIMEOUT);
		match self {
			ClientPolicy::Default => builder,
			ClientPolicy::RedirectNone => builder.redirect(reqwest::redirect::Policy::none()),
			ClientPolicy::Bedrock { force_http1, no_proxy } => {
				let builder = builder.redirect(reqwest::redirect::Policy::none());
				let builder = if force_http1 { builder.http1_only() } else { builder };
				if no_proxy { builder.no_proxy() } else { builder }
			}
			ClientPolicy::Kiro => builder
				.redirect(reqwest::redirect::Policy::none())
				.connect_timeout(Duration::from_secs(15)),
		}
	}
}

fn build(policy: ClientPolicy) -> reqwest::Result<reqwest::Client> {
	policy.builder().build()
}

#[cfg(test)]
mod tests {
	use super::*;

	/// Two `shared_client` calls with the same policy must reuse one pooled connection:
	/// one accepted socket serves both requests. Mirrors the transport pool reuse test.
	#[tokio::test]
	async fn shared_client_reuses_one_connection_across_calls() {
		use tokio::io::{AsyncReadExt, AsyncWriteExt};
		let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
		let address = listener.local_addr().unwrap();
		let server = tokio::spawn(async move {
			// Exactly one accepted connection: a second accept would hang the test.
			let (mut socket, _) = listener.accept().await.unwrap();
			for _ in 0..2 {
				let mut request = Vec::new();
				let mut buffer = [0_u8; 2048];
				while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
					let count = tokio::time::timeout(Duration::from_secs(5), socket.read(&mut buffer))
						.await
						.expect("request did not reuse the pooled connection")
						.unwrap();
					assert!(count > 0, "pooled connection closed early");
					request.extend_from_slice(&buffer[..count]);
					assert!(request.len() < 8192);
				}
				socket
					.write_all(
						b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok",
					)
					.await
					.unwrap();
			}
		});
		for _ in 0..2 {
			let response = shared_client(ClientPolicy::Default)
				.get(format!("http://{address}/probe"))
				.timeout(Duration::from_secs(5))
				.send()
				.await
				.unwrap();
			assert_eq!(response.status(), reqwest::StatusCode::OK);
			// Fully consume the response so reuse is observable on the next call.
			assert_eq!(response.text().await.unwrap(), "ok");
		}
		server.await.unwrap();
	}

	#[test]
	fn try_shared_client_returns_ok_for_every_policy() {
		try_shared_client(ClientPolicy::Default).unwrap();
		try_shared_client(ClientPolicy::RedirectNone).unwrap();
		try_shared_client(ClientPolicy::Bedrock { force_http1: false, no_proxy: false }).unwrap();
		try_shared_client(ClientPolicy::Bedrock { force_http1: true, no_proxy: true }).unwrap();
		try_shared_client(ClientPolicy::Kiro).unwrap();
	}

	// -----------------------------------------------------------------------
	// Churn bench: `cargo test --package=pi-ai --release --lib tcp_pool_churn_bench
	// -- --ignored --nocapture`
	// -----------------------------------------------------------------------

	use std::sync::atomic::{AtomicUsize, Ordering};
	use std::time::Instant;

	fn churn_percentile(sorted: &[u128], fraction: f64) -> u128 {
		if sorted.is_empty() { return 0; }
		let index = ((sorted.len() as f64 - 1.0) * fraction).round() as usize;
		sorted[index.min(sorted.len() - 1)]
	}

	fn churn_latency_summary(mut samples: Vec<u128>) -> serde_json::Value {
		samples.sort_unstable();
		serde_json::json!({
			"samples": samples.len(),
			"p50_us": churn_percentile(&samples, 0.50),
			"p95_us": churn_percentile(&samples, 0.95),
			"p99_us": churn_percentile(&samples, 0.99),
			"mean_us": if samples.is_empty() { 0 } else { samples.iter().sum::<u128>() / samples.len() as u128 },
		})
	}

	/// Keep-alive HTTP/1.1 echo fixture counting TCP accepts, mirroring the
	/// client-pool bench fixture in `openai_http_client_pool.rs`.
	async fn churn_echo_fixture() -> (std::sync::Arc<AtomicUsize>, String, tokio::task::JoinHandle<()>) {
		use tokio::io::{AsyncReadExt, AsyncWriteExt};
		let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
		let address = listener.local_addr().unwrap().to_string();
		let accepts = std::sync::Arc::new(AtomicUsize::new(0));
		let counted = accepts.clone();
		let server = tokio::spawn(async move {
			loop {
				let (mut socket, _) = match listener.accept().await {
					Ok(accepted) => accepted,
					Err(_) => return,
				};
				counted.fetch_add(1, Ordering::SeqCst);
				tokio::spawn(async move {
					let mut buffer = Vec::new();
					let mut chunk = [0u8; 2048];
					loop {
						while !buffer.windows(4).any(|w| w == b"\r\n\r\n") {
							match socket.read(&mut chunk).await {
								Ok(0) | Err(_) => return,
								Ok(n) => {
									buffer.extend_from_slice(&chunk[..n]);
									if buffer.len() > 16384 { return; }
								}
							}
						}
						buffer.clear();
						if socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok").await.is_err() {
							return;
						}
					}
				});
			}
		});
		(accepts, address, server)
	}

	/// The Count-policy build shape (redirectless) with a configurable idle cap
	/// and switchable TCP keepalive.
	fn churn_count_client(max_idle: Option<usize>, keepalive: bool) -> reqwest::Client {
		let mut builder = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none());
		if let Some(max) = max_idle { builder = builder.pool_max_idle_per_host(max); }
		if !keepalive {
			builder = builder
				.tcp_keepalive(None)
				.tcp_keepalive_interval(None)
				.tcp_keepalive_retries(None);
		}
		builder.build().unwrap()
	}

	#[derive(Clone, Copy)]
	enum ChurnPattern {
		/// Back-to-back requests per worker: the prior client-pool bench shape.
		Steady,
		/// One request per worker per wave, barrier-synchronized waves separated
		/// by an idle gap: the per-turn count-request shape of the guarded pool.
		BurstGap,
	}

	async fn churn_scenario(
		client: &reqwest::Client,
		pattern: ChurnPattern,
		concurrency: usize,
		quota: usize,
		gap: Duration,
	) -> serde_json::Value {
		let (accepts, address, server) = churn_echo_fixture().await;
		let url = format!("http://{address}/churn");
		let latencies: std::sync::Arc<std::sync::Mutex<Vec<u128>>> =
			std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
		let start_barrier = std::sync::Arc::new(tokio::sync::Barrier::new(concurrency));
		let end_barrier = std::sync::Arc::new(tokio::sync::Barrier::new(concurrency));
		let mut tasks = Vec::new();
		for _ in 0..concurrency {
			let client = client.clone();
			let url = url.clone();
			let latencies = latencies.clone();
			let start_barrier = start_barrier.clone();
			let end_barrier = end_barrier.clone();
			tasks.push(tokio::spawn(async move {
				for _ in 0..quota {
					if let ChurnPattern::BurstGap = pattern {
						start_barrier.wait().await;
					}
					let started = Instant::now();
					let response = client.get(&url).timeout(Duration::from_secs(10)).send().await.unwrap();
					assert_eq!(response.status(), reqwest::StatusCode::OK);
					assert_eq!(response.text().await.unwrap(), "ok");
					latencies.lock().unwrap().push(started.elapsed().as_micros());
					if let ChurnPattern::BurstGap = pattern {
						end_barrier.wait().await;
						tokio::time::sleep(gap).await;
					}
				}
			}));
		}
		let started = Instant::now();
		for task in tasks { task.await.unwrap(); }
		let elapsed = started.elapsed();
		server.abort();
		let _ = server.await;
		let samples = std::sync::Arc::try_unwrap(latencies).unwrap().into_inner().unwrap();
		let accepts = accepts.load(Ordering::SeqCst);
		let requests = concurrency * quota;
		serde_json::json!({
			"requests": requests,
			"tcp_accepts": accepts,
			"accepts_per_request": accepts as f64 / requests as f64,
			"wall_ms": elapsed.as_millis(),
			"throughput_rps": requests as f64 / elapsed.as_secs_f64(),
			"latency": churn_latency_summary(samples),
		})
	}

	/// No policy may impose one total deadline on every request sharing the
	/// client: `ClientBuilder` Debug prints `timeout` and `connect_timeout`
	/// when set (reqwest async_impl/client.rs `fmt_fields`), so their absence
	/// pins the header-only-wrapping invariant. Kiro's 15s `connect_timeout`
	/// is intended and is the only allowed timeout field.
	#[test]
	fn no_policy_ever_sets_a_client_builder_total_timeout() {
		for policy in [
			ClientPolicy::Default,
			ClientPolicy::RedirectNone,
			ClientPolicy::Bedrock { force_http1: false, no_proxy: false },
			ClientPolicy::Bedrock { force_http1: true, no_proxy: true },
			ClientPolicy::Kiro,
		] {
			let debug = format!("{:?}", policy.builder());
			let without_connect = debug.replace("connect_timeout", "");
			assert!(
				!without_connect.contains("timeout"),
				"{debug} must not set a total request timeout"
			);
			assert!(
				!without_connect.contains("tcp_nodelay: false"),
				"{debug}: TCP_NODELAY must stay on (reqwest default)"
			);
		}
		let kiro = format!("{:?}", ClientPolicy::Kiro.builder());
		assert!(kiro.contains("connect_timeout"), "Kiro must keep its 15s connect_timeout");
		for policy in [
			ClientPolicy::Default,
			ClientPolicy::RedirectNone,
			ClientPolicy::Bedrock { force_http1: false, no_proxy: false },
			ClientPolicy::Bedrock { force_http1: true, no_proxy: true },
		] {
			assert!(!format!("{:?}", policy.builder()).contains("connect_timeout"));
		}
	}

	/// `ClientBuilder` Debug does not print `pool_idle_timeout` or
	/// `pool_max_idle_per_host`, so the stated retention is pinned by the
	/// constant the builder passes to `pool_idle_timeout`. The churn-free
	/// behavior of the uncapped default is measured in the ignored bench.
	#[test]
	fn keepalive_retention_is_stated_and_testable() {
		assert_eq!(POOL_IDLE_TIMEOUT, Duration::from_secs(90));
	}

	/// Equal policies share one client; distinct policies never do. Default and
	/// RedirectNone against one fixture must open two connections, and a second
	/// Default request must reuse the first (still two accepts). `reqwest::Client`
	/// has no `PartialEq`, so identity is asserted through accept counts.
	#[tokio::test]
	async fn distinct_policies_do_not_share_pooled_connections() {
		let (accepts, address, server) = churn_echo_fixture().await;
		let url = format!("http://{address}/policy-split");
		for policy in [ClientPolicy::Default, ClientPolicy::RedirectNone, ClientPolicy::Default] {
			let response = shared_client(policy)
				.get(&url)
				.timeout(Duration::from_secs(5))
				.send()
				.await
				.unwrap();
			assert_eq!(response.status(), reqwest::StatusCode::OK);
			assert_eq!(response.text().await.unwrap(), "ok");
		}
		server.abort();
		let _ = server.await;
		assert_eq!(accepts.load(Ordering::SeqCst), 2);
	}

	/// Real measurements for the idle-cap and TCP-keepalive audit. Ignored in the
	/// default suite; writes research/network_latency/provider/results/tcp-pool-churn-bench.json.
	#[ignore]
	#[tokio::test]
	async fn tcp_pool_churn_bench() {
		const WAVES: usize = 12;
		const GAP: Duration = Duration::from_millis(25);
		const STEADY_REQUESTS: usize = 320;
		let mut runs = Vec::new();
		for repeat in 0..2 {
			for (arm, max_idle) in [
				("count_policy_max_idle_4", Some(4usize)),
				("max_idle_32", Some(32usize)),
				("no_max_idle_reqwest_default", None),
			] {
				let client = churn_count_client(max_idle, true);
				for concurrency in [8usize, 16, 32] {
					for (pattern_name, pattern) in [
						("steady", ChurnPattern::Steady),
						("burst_gap_25ms", ChurnPattern::BurstGap),
					] {
						let quota = if let ChurnPattern::Steady = pattern {
							STEADY_REQUESTS / concurrency
						} else {
							WAVES
						};
						let scenario = churn_scenario(&client, pattern, concurrency, quota, GAP).await;
						runs.push(serde_json::json!({
							"repeat": repeat, "arm": arm, "pattern": pattern_name,
							"concurrency": concurrency, "scenario": scenario,
						}));
					}
				}
			}
			// The production shared client: no idle cap, reqwest keepalive defaults.
			let shared = shared_client(ClientPolicy::Default);
			for concurrency in [8usize, 16, 32] {
				for (pattern_name, pattern) in [
					("steady", ChurnPattern::Steady),
					("burst_gap_25ms", ChurnPattern::BurstGap),
			] {
					let quota = if let ChurnPattern::Steady = pattern {
						STEADY_REQUESTS / concurrency
					} else {
						WAVES
					};
					let scenario = churn_scenario(&shared, pattern, concurrency, quota, GAP).await;
					runs.push(serde_json::json!({
						"repeat": repeat, "arm": "shared_client_default_policy", "pattern": pattern_name,
						"concurrency": concurrency, "scenario": scenario,
				}));
				}
			}
			// Keepalive fully off vs the reqwest default (on, 15s/15s/3): the
			// per-connection cost is one setsockopt; no probe fires in-bench.
			let off = churn_count_client(None, false);
			for concurrency in [8usize, 16, 32] {
				let quota = STEADY_REQUESTS / concurrency;
			let scenario = churn_scenario(&off, ChurnPattern::Steady, concurrency, quota, GAP).await;
				runs.push(serde_json::json!({
					"repeat": repeat, "arm": "no_max_idle_keepalive_off", "pattern": "steady",
					"concurrency": concurrency, "scenario": scenario,
			}));
			}
		}
		let report = serde_json::json!({
			"note": "loopback keep-alive HTTP/1.1; measured under throttled gate (4 CPUs shared, -j 2, BelowNormal)",
			"runs": runs,
		});
		let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
			.join("../../research/network_latency/provider/results");
		std::fs::create_dir_all(&dir).unwrap();
		std::fs::write(dir.join("tcp-pool-churn-bench.json"), serde_json::to_vec_pretty(&report).unwrap()).unwrap();
		println!("{}", serde_json::to_string_pretty(&report).unwrap());
	}
}
