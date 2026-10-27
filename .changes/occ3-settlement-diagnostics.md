## Occ3 settlement refusals now name the failing check

Native source-capture settlement failures previously collapsed every
refusal into one generic message, so a live window could not tell which
check rejected the captured turn. Settlement now appends a bounded,
operator-side reason to the harness error (`occ3_capture_state_<state>`,
`occ3_capture_hooks_missing`, or the completed-hook's own code such as
`occ3_completed_binding_count`), capped at 160 characters with no
dynamic response data. The public provider error stays the fixed generic
`occ3_source_uncertain`.

Native handoff authorization is also stricter than plain agent
messaging: unrelated top-level sessions (two parentless roots that plain
messaging treats as siblings) are now outside the native family reach
and are refused with the family-reach error.
