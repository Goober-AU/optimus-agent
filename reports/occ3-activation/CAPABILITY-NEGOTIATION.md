# OCC-3 capability negotiation

## 1. Four facts that stay separate

| Fact | Producer | Never inferred from |
|---|---|---|
| capability available | connector handshake plus `models.json` | a model name or endpoint shape |
| experimental enabled | two settings plus the operator's `live-verified` record | a successful handshake alone |
| native delivery completed | receiver completed-use receipt | a queued, admitted or capture-only state |
| semantically verified | a measurement | any configuration at all |

The first two are decided by `core/occ3_connectors/activation.rs`. The third is decided by
`core/occ/activity.rs` from a receipt-bound marker, described in `NATIVE-DELIVERY-EVENTS.md`. The fourth is a
research result and is not produced by any code path.

## 2. The ladder

`core/occ/occ3/negotiation.rs` already implemented the deterministic ladder and it is unchanged:
policy, capability presence, strict identity equality, peer fingerprint equality, peer method support,
registered connector with verified capabilities for that identity, envelope budget, then `Occ3`.

Activation runs *before* the ladder and can only ever remove eligibility. It never adds a route the ladder
would have refused.

## 3. Order of gate evaluation and why it matters

`configured_connector` checks the two policy flags before it looks at the model. When a model is configured
but the user has not opted in, the reported reason is `disabled_by_policy`, not `not_configured`. The
operator sees the gate they can actually act on.

## 4. No silent mislabelling

A native attempt that fails or becomes uncertain must not be relabelled. Two mechanisms enforce that:

1. The daemon controller refuses to return a delivered receipt unless the receiver's outcome is `Consumed`.
2. The activity projection suppresses any native marker that is not a completed-use marker, so a suppressed
   native delivery produces no pulse at all rather than a text pulse.

An uncertain native attempt is never replayed as text. The existing policy allows pre-admission fallback only;
post-admission the outcome stays `Uncertain` and is reported as such.

## 5. Enrolment timing

Activation happens when a session runtime is added (`AgentDaemon::add_runtime`), before the session's first
prompt, so the native permit and `occ3_handoff` tool are installed while the session is idle. A session that
is already streaming refuses installation rather than mutating tools mid-turn.

Because the handshake is a property of the runtime rather than of a session, one successful verification is
cached process-wide and keyed by endpoint plus model id. A changed endpoint or model id re-verifies; a runtime
restart invalidates the cache with every other enrollment.
