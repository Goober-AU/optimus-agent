# V3 supervised deployment: candidate persistence policy

The V3 controller performs one prepared, approved attempt: the candidate is
created unstarted, inspected for exact launch parity with the original, swapped
in through the supervised path, verified against every readiness gate, and then
either restored or kept depending on one plan field.

## The plan field

`persistence_policy`, one of two exact values:

| Value | Behaviour after the verified commit point |
| --- | --- |
| `rollback_on_success` | The attempt is one time-boxed experiment. At the verified commit point the candidate is stopped, renamed `-failed`, and the preserved original is renamed back, started and re-verified. Production returns to the known-good container. |
| `persist_candidate_on_success` | The verified candidate keeps running as the new production service. The preserved original stays stopped under its preserved name, and the original container record, image and launch configuration stay recorded in the journal. |

`rollback_on_success` is the default, and it is the policy that applies when the
field is absent. `persist_candidate_on_success` additionally requires a
`preserved_original` record in the plan:

```json
{"persistence_policy": "persist_candidate_on_success",
 "preserved_original": {"container_id": "<64-hex id>",
                        "image_id": "sha256:<64-hex>",
                        "name": "<original container name>"}}
```

`validate_plan` refuses a persist plan without that record, a record that
differs from the attempt original, and any unknown policy value. `prepare`
additionally refuses a record whose name differs from the inspected original
container, before any mutation.

## Readiness gates are never relaxed

Persistence is declared only after the whole readiness gate repeats in a second
pass (`service_persisted`): startup `/health`, ordinary inference (`/v1/chat/completions`
semantic nonce match), authentication enforcement, native OCC-3 readiness with
TP8 ranks and runtime identity, tool calling, and the pinned source/receiver
ingress routes. Health monitoring, resource limits, control-API authentication,
source attestation, GPU memory checks, original container preservation and the
manual rollback all stay in place. The policy never disables a check; it only
decides what happens to a candidate that already passed them.

## Timeouts are separate fields with separate reactions

| Plan field | Meaning | Reaction when it elapses |
| --- | --- | --- |
| `request_timeout_s` | model request timeout for one service check | that gate fails, rollback |
| `startup_timeout_s` | deployment health timeout for candidate readiness | rollback |
| `deployment_timeout_s` | controller deployment deadline | rollback until the readiness gate passed |
| `source_capture_timeout_s` | native source capture deadline | recorded in `stage_deadlines`, never fatal |
| `receiver_inference_timeout_s` | receiver inference deadline | recorded in `stage_deadlines`, never fatal |
| `proof_timeout_s` | experimental proof deadline for one external native proof | rollback under `rollback_on_success`, persistence under `persist_candidate_on_success` |
| `maintenance_window_s` | operator maintenance window | persistence under `persist_candidate_on_success` |
| `persistence_policy` | candidate persistence policy | decides the commit action |

The named deadlines are recorded once in the journal as `timeout_policy`, so the
state names every timeout type explicitly instead of sharing one budget.

## Experiment signals: semantic results never restart anything

The external executor may publish one durable signal per proof phase:

```
<attempt-dir>/experiment-signal-<phase>.json
{"schema": "occ3.experiment.signal.v1", "phase": "initial", "code": "<fixed code>"}
```

Codes are split in two disjoint classes:

- semantic experiment: `receiver_answer_mismatch`, `receiver_partial_answer`,
  `receiver_fallback_used`, `receiver_fact_mismatch`, `source_capture_absent`,
  `proof_not_delivered`. These are recorded in `experiment_signals` and never
  cause a restart or a rollback, in either policy.
- service health: `receiver_transport_error`, `receiver_timeout`,
  `receiver_http_failure`, `source_route_unavailable`,
  `source_capture_transport_error`, `runtime_identity_lost`. These fail the
  gate and restore production.

An unrecognized code is kept in the non-fatal class and flagged (`code_known:
false`), so a new semantic result cannot silently escalate into a rollback.

## Journal and state

The journal records, in its existing JSON format:

- `persistence`: `decision` (`persisted_candidate_on_success` or
  `rolled_back_on_success`), `policy`, `reason`, candidate id, image id, name
  and process epoch, the preserved `original` record (container id, image id,
  name, launch-configuration digest with the credential removed), `readiness_gate`,
  `recovery_command`, and the recovery outcome when a policy rollback ran.
- `persistence_decision`: the same decision value, for phase inspection.
- `preserved_original`: written by `prepare` before any mutation.
- `timeout_policy`, `stage_deadlines`, `experiment_signals`.

Phases: `CANDIDATE_PERSISTED` is the persist-mode commit point (readiness
verified, research deadlines elapsed); `COMPLETE` is the verified commit after
the full protocol; `RESTORED`, `NOOP`, `RESTORE_FAILED` and `PREFLIGHT_FAILED`
are unchanged.

## Operator recovery

The explicit path is the same verified restoration the automatic paths use:

```
python -m deploy.v3.controller restore --attempt-dir <attempt-dir>
```

It works on a persisted (`COMPLETE`) or in-progress attempt, stops and renames
the candidate, restores and re-verifies the preserved original, and records
`persistence.manual_restore`. It never deletes a container or an image, and it
never reopens the attempt. The exact command for one attempt is stored in
`persistence.recovery_command`.

## Later controlled replacement

A persisted candidate is the new production container. A later attempt takes
the persisted candidate id as its `original_id`; the durable fence of the
retired original does not block it, and the new attempt keeps the same
preservation, parity, gate and recovery contract.
