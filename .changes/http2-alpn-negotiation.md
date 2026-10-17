- Enabled ALPN-based HTTP/2 negotiation for provider transports by turning on
  reqwest's native-tls-alpn feature: HTTPS providers that advertise h2 via ALPN
  are now multiplexed over HTTP/2 connections instead of HTTP/1.1. Previously
  the build compiled the http2 code but never offered ALPN, so every provider
  request was HTTP/1.1 on every platform regardless of server capability.
  Cargo.lock is unchanged (feature-only). The offline benchmark suite cannot
  express the WAN handshake-elimination benefit; the flip and its risk notes
  (middlebox h2 failures, schannel Win 8.1+ requirement) are documented in
  research/network_latency/provider/http2-analysis.md.
