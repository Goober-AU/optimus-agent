=== PR #158: Release Optimus Agent v0.1.33: OCC controls and communication flashes (merged 2026-10-16T08:33:12Z) ===
[chatgpt-codex-connector[bot] @ crates/pi-coding-agent/src/modes/interactive/occ_control.rs:40]
**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Send OCC toggles to the daemon that owns the session**

When this connection targets a daemon session configured with a different `agentDir`—for example, a client attached to another profile or a session created with an explicit `agentDir`—this constructs `SettingsManager` from the TUI process's default profile and writes the wrong `settings.json`. The daemon's existing session continues reading its own settings storage, so `/occ 2 on` reports success without changing subsequent messages and may alter an unrelated local profile; expose capability-gated daemon get/set operations instead of mutating client-local settings.

AGENTS.md reference: [AGENTS.md:L31-L35](https://github.com/telemusai/optimus-agent/blob/ba376f4499acfc65d4afe0677adc2f38cef54b15/AGENTS.md#L31-L35)

Useful? React with 👍 / 👎.

<!-- codex-code-review-finding:v1 -->

[ISSUE-COMMENT chatgpt-codex-connector[bot]]
<!-- codex-pull-request-review-summary -->
<!-- codex-review-state:v1 {"code_review":{"commit_sha":"ba376f4499acfc65d4afe0677adc2f38cef54b15","has_any_findings":true,"has_unresolved_findings":true,"status":"completed"},"security_review":{"commit_sha":null,"has_any_findings":null,"has_unresolved_findings":null,"status":null}} -->

## Codex Review Summary

This comment shows the latest Codex review activity on this pull request.

| Review | Status | Commit | Review trigger |
| --- | --- | --- | --- |
| 📝 **Code Review** | ✅ **Completed** <relative-time datetime="2026-10-16T08:23:29.582908Z">2026-10-16T08:23:29.582908Z</relative-time> | `ba376f4` | PR opened |



<details> <summary>ℹ️ About Codex in GitHub</summary>
<br/>

[Your team has set up Codex to review pull requests in this repo](https://chatgpt.com/codex/cloud/settings/general). Reviews are triggered when you
- Open a pull request for review
- Mark a draft as ready
- Comment "@codex review" or "@codex security review".

Codex reacts with 👀 while any review is running, comments if it has suggestions, and reacts with 👍 once all reviews finish with no findings.

</details>

=== PR #159: Keep OCC toggles scoped to each chat session (merged 2026-10-16T08:57:55Z) ===
[chatgpt-codex-connector[bot] @ crates/pi-coding-agent/src/modes/interactive/occ_control.rs:41]
**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Gate session-scoped OCC controls with a new capability**

When a client and daemon straddle this update, both still advertise schema 40 and `occ2_communication`, so this check succeeds despite incompatible storage semantics: a new client writes `occ.json` that an old daemon never reads, while an old client updates global settings that a new daemon ignores. In either case `/occ` reports success but OCC-2 remains unchanged. Introduce a distinct negotiated capability or schema revision and cover both client/daemon version directions.

AGENTS.md reference: [AGENTS.md:L31-L34](https://github.com/telemusai/optimus-agent/blob/a5d74ea3db409ea5e9db0ac29d41672a59784566/AGENTS.md#L31-L34)

Useful? React with 👍 / 👎.

<!-- codex-code-review-finding:v1 -->

[ISSUE-COMMENT chatgpt-codex-connector[bot]]
<!-- codex-pull-request-review-summary -->
<!-- codex-review-state:v1 {"code_review":{"commit_sha":"a5d74ea3db409ea5e9db0ac29d41672a59784566","has_any_findings":true,"has_unresolved_findings":true,"status":"completed"},"security_review":{"commit_sha":null,"has_any_findings":null,"has_unresolved_findings":null,"status":null}} -->

## Codex Review Summary

This comment shows the latest Codex review activity on this pull request.

| Review | Status | Commit | Review trigger |
| --- | --- | --- | --- |
| 📝 **Code Review** | ✅ **Completed** <relative-time datetime="2026-10-16T08:46:35.293027Z">2026-10-16T08:46:35.293027Z</relative-time> | `a5d74ea` | PR opened |



<details> <summary>ℹ️ About Codex in GitHub</summary>
<br/>

[Your team has set up Codex to review pull requests in this repo](https://chatgpt.com/codex/cloud/settings/general). Reviews are triggered when you
- Open a pull request for review
- Mark a draft as ready
- Comment "@codex review" or "@codex security review".

Codex reacts with 👀 while any review is running, comments if it has suggestions, and reacts with 👍 once all reviews finish with no findings.

</details>

=== PR #155: feat(occ): symbolic dialects (OCC-2) + latent communication research (OCC-3) (merged 2026-10-16T08:17:52Z) ===