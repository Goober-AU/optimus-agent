# Context health beside Streaming TPS

The agents status bar shows `STREAM TPS …  CTX HEALTH GREEN 92`. The number is
Optimus's existing 0–100 context-risk heuristic, where higher is better. The
colour is the more cautious of that score and context-window pressure:

| Badge | Meaning |
| --- | --- |
| Green | Known valid usage, score at least 80, below 70% window usage, and not compacting |
| Amber | Score below 80, window usage at least 70%, or compaction in progress |
| Red | Score below 40 or window usage at least 85% |
| Unknown (grey) | No trustworthy current token count, window size, or compaction count |

Red takes priority over amber. During compaction the badge says `compacting`
instead of displaying a potentially stale score. After compaction, missing token
usage stays unknown until a new context estimate arrives. Only the selected
session's context is evaluated; Streaming TPS continues to cover its observed
agent family. Cached context snapshots and the existing in-flight token estimate
drive the badge without extra network requests or provider calls.

The retained score is `100 - utilisation penalty - compaction penalty`. The
utilisation penalty grows linearly from zero at 35% usage to 45 points at 95%.
Each completed compaction costs six points, capped at 30 points. These weights
are the heuristic introduced in PR #179. The new 70%/85% pressure thresholds can
raise the colour even when the score has not reached its warning band. The raw
token counter and existing tray score remain available below the agents bar.

This is an operational warning, not a measured probability of accuracy or proof
of semantic context loss. Green does not guarantee that an important fact was
retained; a compaction count alone cannot verify what a summary preserved.

On narrow terminals the badge shortens to `CTX G92`, `CTX A76`, `CTX R25`, or
`CTX ?`. Communication labels yield space to the TPS/health pair, then very
narrow terminals retain the compact health badge. Text labels preserve the
meaning when colour is unavailable. Both classic and neon layouts use the same
green/amber/red classification and ANSI colours. The change is presentation-only:
it does not change compaction settings, the task-state ledger, or daemon protocol.

## Compaction count in the bottom-right tray

When space permits, `Compactions: 3` appears immediately before the context
meter and existing context-health/token reading. The count comes from the
current session's persisted compaction entries. It updates after compaction
ends and follows the selected session when you resume or switch chats.

A known zero appears as `Compactions: 0`. Missing or invalid counts are not
invented. The count hides on crowded rows before existing Jev, heartbeat,
goal, or context labels are shortened. This display adds no setting or model call.

## Open-source attribution

The default pressure classifier is a Rust port of
[`getContextColor` in Claude HUD](https://github.com/jarrodwatts/claude-hud/blob/5f725e737a882211bb62b8fc56ab593d4d97f18a/src/render/colors.ts),
by Jarrod Watts, at commit `5f725e737a882211bb62b8fc56ab593d4d97f18a`.
Claude HUD uses green below 70%, yellow from 70%, and red from 85% context usage.
Optimus displays the warning band as amber, combines the policy with its existing
score, validates missing/non-finite inputs, and adds compaction-state handling.
The upstream MIT notice is bundled in [claude-hud-MIT.txt](third-party/claude-hud-MIT.txt).
