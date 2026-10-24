# Optimus Agent v0.1.40 (draft)

Prepared from v0.1.39 (`94f174fb219107d85047a0c2d0bea6d6e34c2b92`).

## Live agent graph

The fullscreen terminal header now draws a compact force-directed graph immediately left of the telemus.ai and CPU/RAM/TPS panel. The main agent is a diamond, working subagents are filled circles, and idle or completed agents are hollow circles. Nodes and links use the same neon blue as the resource metrics. Working nodes pulse; newly delivered agent messages illuminate the exact sender/receiver link with a moving highlight.

The layout uses spring attraction, node repulsion, damping and a fixed main node. It preserves positions across roster refreshes, includes nested descendants, isolates unrelated sessions and stops its animation timer after the layout settles and agents are idle. Communication history is baselined on attachment and reconnect; duplicate deliveries cannot restart pulses. The graph consumes existing negotiated roster metadata and performs no additional daemon requests or provider calls.

The graph uses 24 columns in the existing three-row brand area at widths of at least 110 columns when the header has enough vertical space. Narrower terminals retain the existing text status and agent summary. Layout work is bounded to 64 nodes, with an overflow count for larger families. Distinct terminal cells prevent visible nodes from erasing each other.

## Status styling

- Jev header and footer indicators, including compaction, use text-centered bullet circles.
- The heartbeat dot uses the context meter's `thinkingText` theme color: neon pink in Neon. Its existing enabled, paused and activation-flash behavior is preserved.
- CTX Health Amber uses the same `warning` theme color as Jev Standard Mode. Context-health calculation and thresholds are unchanged.

## Validation and release status

The workspace, resource manifest, Python runtime and lockfile identities are 0.1.40. Validation uses an isolated profile and offline fixtures; receipts are kept in `/home/anthony/optimus-graph-validation/`. Completed checks:

- `bash scripts/check.sh`: workspace/all-target checking, whitespace, native resource layout, and 282 Python tests passed.
- Focused native UI checks: 90 passed, covering graph topology and animation, delivered-message pulses, session/reconnect isolation, responsive header frames, status colors, heartbeat behavior, resource monitoring and transcript caching.
- `jev_ui_tests`: 64 passed, including full and compact centered-dot status payloads.
- `uv lock --check --offline --project prime-agent-runtime`: passed.
- XTerm previews rendered and inspected at 110 and 160 columns using isolated Xvfb displays. No real session or provider calls were used.

The existing workspace warnings remain; no new warning was introduced in the changed paths. Portable draft assets are staged only after these checks pass. Source/payload provenance, archive checksum, CLI version/help and installation smoke receipts are retained with the draft artifacts.

This release remains a draft. Linux x86_64 is the local build and runtime validation target. Windows and macOS share the Rust terminal presentation code, but require native runtime validation on their respective hosts. GitHub Actions remains disabled.

## Surface and protocol status

3D/Web/TUI parity: this is Optimus terminal presentation. Telemus's 3D and Web surfaces in the separate vr-ai-chat repository and Telegram are unaffected. There are no renderer/platform abstraction changes, new daemon commands, capabilities or payload-shape changes. Missing communication metadata leaves links unlit while roster status remains usable.
