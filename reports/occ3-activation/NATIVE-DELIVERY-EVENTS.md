# Native delivery events: the only producer of a Latent activity kind

## 1. The problem this replaces

`core/occ/activity.rs` classified a delivery by its body text. A native OCC-3 handoff delivers a
control-only body (`Continue the authorized task using the attached state.`), so the existing
classifier reported it as `Text`. The TXT light would therefore pulse for an OCC-3 handoff. That
is the defect the campaign brief predicted, and it is now closed.

## 2. Ordering

The delivery record is created and prompted before the receiver's consumption outcome is known, so
the marker has two states:

1. `pending` is stamped on the record before the receiver is prompted.
2. `latent_native` is written in place, on the same record and the same message id, only after the
   receiver reports `Consumed` and its completed-use receipt digest exists.

There is never a second record. The rewrite is idempotent and covered by a test.

## 3. Marker shape (content-free)

`details.occ3Delivery`:

| Field | Meaning |
|---|---|
| `kind` | `pending` or `latent_native` |
| `methodId` | the native method id, bounded to 128 chars |
| `runtimeGeneration` | the receiver runtime generation |
| `runtimeIdentitySha256` | sha256 over runtime id, generation and model fingerprint |
| `receiptSha256` | sha256 over the completed-use receipt, present only when completed |
| `completedUse` | true only for `latent_native` |

No handle, no tensor bytes, no receipt blob, no message text, no token ids. A test asserts the
serialized activity contains only `id`, `kind`, `sourceSessionId` and `timestamp`.

## 4. Projection rule

```
occ3Delivery absent                       -> existing occ2Delivery mapping, then classify()
kind == latent_native && completedUse && 64-hex receiptSha256 && bounded methodId
                                          -> CommunicationKind::Latent
anything else present but inconsistent     -> no delivery at all (no pulse, never Text)
```

An inconsistent marker produces nothing rather than a guess. That is what makes capture-only,
queued, admitted-but-unconsumed, failed and uncertain deliveries dark by construction.

## 5. Daemon controller change

`DaemonAgentMessageController::send_prepared_native_handoff` previously returned a delivered
receipt without ever checking the receiver outcome. It now:

- stamps the pending marker;
- returns an error for `Uncertain`, `Failed` and `Expired`, with no completion marker and no replay;
- returns an error when a `Consumed` or `Acknowledged` outcome exists but the completed-use receipt
  digest does not, which means the marker cannot be trusted;
- returns the ordinary transport receipt (for example `queued`) for a delivery that is admitted but
  not yet consumed, with no completion marker.

The existing queued-admission test now also asserts that no latent completion is projected for that
message id.

## 6. Runtime restart

The marker is live agent state only; the persisted session entry is not rewritten. After a restart
or rehydration the record reads `pending`, so no stale event can be resurrected, and the activity
generation string invalidates on restart independently. Requirement "runtime restart invalidates
stale events" therefore holds by construction. A stale persisted `latent_native` cannot exist.

## 7. Tests

`crates/pi-coding-agent/tests/occ3_latent_activity.rs`, 7 passing, including a loopback fixture
that reaches `Consumed` only through a verified `x-optimus-occ3-receipt` header:

1. completed consumption produces exactly one latent delivery, wire kind `latent`;
2. a native delivery is never reported as text, OCC1 or symbolic while `classify()` on the same
   body still returns `Text`;
3. the serialized activity carries no body, handle, receipt or digest;
4. a duplicated record with one id yields one delivery;
5. pending, `completedUse: false`, missing receipt digest, unknown kind, malformed marker and an
   over-long method id all yield zero deliveries;
6. the in-place rewrite keeps one record, one id and one delivery, and is idempotent;
7. marker constructors are bounded and content-free.

Plus `cargo test -p pi-coding-agent occ3`: 72 passed, 0 failed, 2 ignored (pre-existing fixture-hang
tests).
