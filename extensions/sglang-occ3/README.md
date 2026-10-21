# occ3-bridge: SGLang plugin for OCC-3 latent communication

OCC-3 is latent agent communication: a sender deposits a message as
GPU-resident model state, and a receiver consumes that state directly
instead of re-reading text. This package is the SGLang-side connector
for the generic OCC-3 bridge in `crates/pi-coding-agent/src/core/occ/occ3`.

## How it works (v1 primary arm: KV-radix channel)

1. `POST /occ3/v1/send` executes an ordinary generation request whose
   prompt is the message content, under a channel-scoped
   `cache_salt` (`occ3-<channel>`). On completion the scheduler
   inserts the KV row set into the radix tree under that salted
   namespace. The latent now lives only as KV slots on the GPU.
2. `POST /occ3/v1/recv` replays the same carrier token ids under the
   same salt with the receiver's task suffix appended. The radix
   lookup hands the receiver the sender's KV slots zero-copy; only
   the suffix is prefilled.
3. `cached_tokens` in the response verifies the hit deterministically
   (miss = evicted or racing; the caller resends).
4. `POST /occ3/v1/release` retires the channel bookkeeping.

Because sender and receiver run on the same server, same weights,
same TP topology and same KV dtype, the receiver consumes exactly the
KV it would have computed itself: zero representation-space mapping
error by construction. Cross-model transfer is out of scope for v1
(strict same-exact-model rule, enforced by identity fingerprints).

## Containment

- The salt namespaces the channel away from all default-namespace
  traffic; ordinary requests can never observe or reuse channel KV.
- Receiver suffixes extend the salted branch only.
- Use a fresh carrier nonce (distinct message content) per round to
  avoid branch growth.
- Eviction window: between send and recv the rows are LRU-evictable.
  Keep the gap short; verify with `cached_tokens`; resend on miss.

## Optional latent-row injection (hybrid StateBridge arm)

`POST /occ3/v1/send` accepts `embeds` (rows of floats) and
`positions`; the plugin injects them into the carrier via
`positional_embed_overrides` (in-process `PositionalEmbeds`), so the
channel can carry latent thought content, not just token KV.

## Optional hidden-state capture (dormant)

Set `OCC3_CAPTURE_ENABLED=1` (and optionally `OCC3_CAPTURE_DIR`) in
the server environment to arm an AROUND hook on
`run_eagle_verify` that clones accepted-token hidden states for
observability and the portable StateBridge arm. Off by default.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | /occ3/v1/status | plugin version, channel counts, metrics |
| GET | /occ3/v1/identity | 11-field model identity + fingerprint |
| GET | /occ3/v1/capabilities | capability advertisement for negotiation |
| GET | /occ3/v1/channels | channel table (no token ids) |
| POST | /occ3/v1/send | write a message's KV into a salted channel |
| POST | /occ3/v1/recv | read a channel as a prefix hit |
| POST | /occ3/v1/release | retire a channel |

All routes sit behind the server's api-key middleware (auth level
NORMAL; no `/health` prefix so nothing bypasses the key).

## Installation

The package is installed into the SGLang server image (see
`deploy/Dockerfile`). The SGLang plugin loader discovers it via the
`sglang.srt.plugins` entry-point group at process start; no source
patches, no extra launch flags. Optionally restrict to this plugin
with `SGLANG_PLUGINS=occ3_bridge`.

Requires: SGLang >= 0.5.21 (positional_embed_overrides, cache_salt,
plugin framework). The server restart for plugin load is a one-time
deployment step; see `deploy/README.md`.

## Development

```
cd extensions/sglang-occ3
python -m pytest tests/   # offline; no GPU, no sglang install needed
```

Tests stub the SGLang API surface (`tests/conftest.py`) and verify
request construction, channel bookkeeping, verification logic and
route behavior, plus field-order parity with the Rust bridge types.

## License

MIT, same as the repository.
