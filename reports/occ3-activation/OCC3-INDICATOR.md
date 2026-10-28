# The OCC3 communication indicator

## 1. Result

The existing communication indicator now has a fourth segment:

```
○ TXT   ○ OCC1   ○ OCC2   ○ OCC3
```

and it lights only for a completed native OCC-3 delivery:

```
○ TXT   ○ OCC1   ○ OCC2   ● OCC3
```

Idle rendering, `strip_ansi`, is exactly `○ TXT  ○ OCC1  ○ OCC2  ○ OCC3` - 29 visible columns.

## 2. What was reused rather than added

`modes/interactive/communication_activity.rs` already owned a monotonic, session-scoped pulse
state machine. The change widens its one array from three entries to four and maps
`CommunicationKind::Latent` to index 3 instead of ignoring it. Everything else is the existing
implementation:

- the neon-blue style, unchanged: `\x1b[1;38;5;39m` when active, `\x1b[2;38;5;39m` when idle,
  `\x1b[22;39m` to reset, two spaces between segments;
- `-` instead of `○` for an unsupported roster row;
- family scoping, session identity, duplicate suppression through the bounded `seen` deque,
  observation-epoch and identity reset, baseline-on-reconnect, `MAX_RECEIVERS` and `MAX_SEEN`
  bounds, and the link bookkeeping that feeds the agent graph;
- `PULSE` (1400 ms) and `next_deadline()`, which now covers the fourth slot;
- ANSI and no-colour paths, both unchanged.

No second dashboard, no second state machine, no new visual system.

## 3. Only real communication lights it

The pulse comes from `CommunicationKind::Latent` in the roster projection, and that kind is
produced by exactly one place: `core/occ/activity.rs` when the transcript carries a
receipt-bound completed-use marker. See `NATIVE-DELIVERY-EVENTS.md`.

Nothing else can light OCC3. It does not light for an enabled setting, a selected compatible
model, a created handle, a completed capture, a queued native message, a successful admission, a
test fixture, the text "OCC3", or a typed "hello".

## 4. What the light means

It indicates a completed native communication event. It does not claim semantic accuracy. That
distinction is deliberate and is repeated in `README.md` and `FINAL-REPORT.md`.

## 5. Narrow terminals

Measured with the real caller composition at widths 20 to 200 in steps of 2:

- the string is truncated caller-side, never wrapped into a broken row and never panics down to
  width 2;
- `context_health_indicator::status_line` drops the communication string when communication plus
  TPS plus health exceed `width - 2`;
- `SubagentSummaryLine` ellipsizes; the OCC3 text disappears below about 58 columns;
- at widths 104 to 110 the four-segment version needs one more status row than the three-segment
  version did. The row decision lives in `subagent_summary_line.rs` (`communication_below`), which
  was outside this change. It is recorded as known limitation L-1 in `FINAL-REPORT.md` rather than
  fixed by restructuring the header.

## 6. Tests

17 tests in `communication_activity`, all passing, replacing the old "latent is ignored" assertion
with the new required contract:

| Test | Contract |
|---|---|
| four-segment idle spacing | exact 29-column render |
| per-kind isolation | text/OCC1/symbolic/latent each pulse only their own segment |
| OCC3 neon style | same ANSI codes as the other segments |
| duplicate id | one pulse for a repeated message id |
| replay after expiry | no second pulse |
| expiry | OCC3 dark after `PULSE` |
| outside family | no pulse |
| unsupported row | four `-`, no pulse |
| generation change | array reset |
| narrow width | widths 2 to 120, no panic, no overflow |

`cargo test -p pi-coding-agent --lib communication`: 26 passed. Neighbouring suites unchanged:
`subagent_summary_line` 8 passed, `generation_throughput` 22 passed, `resource_status` 7 passed.
