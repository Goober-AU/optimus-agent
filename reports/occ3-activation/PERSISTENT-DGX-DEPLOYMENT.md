# Persistent supervised DGX development deployment

## 1. What changed and why

The supervised controller in `extensions/sglang-occ3/deploy/v3/` could restore the original
container at the end of a proof window. That was correct for early work and it is wrong for
development: every healthy experimental candidate was thrown away, and a full GLM-5.3 TP8 reload
costs tens of minutes of GPU time.

The controller now has an explicit persistence policy.

## 2. Policy values

| Value | End of a verified window |
|---|---|
| `rollback_on_success` | restore the original production container (previous behaviour) |
| `persist_candidate_on_success` | keep the healthy candidate running |

Constants: `PERSIST_CANDIDATE_ON_SUCCESS`, `ROLLBACK_ON_SUCCESS`. `validate_persistence` refuses an
unknown value, and refuses a persist plan that has no valid `preserved_original` record
(`container_id`, `image_id`, `name`).

Default remains `rollback_on_success`, so a plan that does not ask for persistence behaves exactly
as before. This is a deliberate default; flipping it is one line in
`lifecycle.validate_persistence`.

## 3. What persistence requires before it is granted

The full readiness gate is re-run and recorded as `service_persisted`: startup health, ordinary
chat inference, tool calls, native OCC-3 identity, TP8 ranks, runtime identity, and the pinned
ingress source and receiver routes. Persistence is never granted on the strength of a successful
deployment alone.

## 4. Timeouts are now separate types

| Field | Meaning |
|---|---|
| `source_capture_timeout_s` | native source capture deadline |
| `receiver_inference_timeout_s` | receiver inference deadline |
| deployment health timeout | service readiness |
| `ExperimentDeadline` | the research proof deadline |
| `MaintenanceWindowClosed` | the operator maintenance window |
| `candidate_persistence_policy` | what to do at the end |

`classify_experiment_signal` splits the fixed experiment vocabulary into `semantic_experiment` and
`service_health`. A semantic signal is never fatal. This is the specific defect the brief names: a
receiver returning UNKNOWN is a research result, not a service fault, and it must not restart a
healthy 8-GPU model.

## 5. Recovery is intact

Preserved: original container and image, `preserved_original` journal record with a
configuration digest that has the credential removed, authenticated control API, resource limits,
GPU memory monitoring, source attestation, genuine health-failure detection, and an explicit
operator path:

```
python -m deploy.v3.controller restore --attempt-dir DIR
```

`supervisor.decide` returns `persist` only for a `CANDIDATE_PERSISTED` child whose persistence
record validates; `commit` proves completion state and helper-cleanup settlement before it commits
`COMPLETE`. A verified `recover()` still runs under the rollback policy, and still runs for a
service-health failure under either policy.

## 6. Tests

`extensions/sglang-occ3/tests/test_v3_persistence.py`, 20 passing:

| Case | Contract |
|---|---|
| a | success + rollback policy restores production |
| b | success + persist policy keeps the candidate and records the preserved original |
| c | proof deadline persists under persist policy and still rolls back under rollback policy |
| d | startup health failure and a failed persistence gate restore production |
| e | ordinary-inference failure is a service health failure |
| f | a semantic experiment signal never rolls back, under either policy |
| g | operator restore works after persistence |
| h | the validator rejects persist without a valid preserved record |

Related suites: `test_controller`, `test_v3_lifecycle`, `test_v3_supervisor`,
`test_v3_supervisor_admission`, `test_v3_service_checks`, `test_v3_docker_config`,
`test_v3_persistence` together: 360 passed.

## 7. Not done

No live Docker Engine run. The persist path is verified offline against `FakeDocker` only; there was
no authorised live deployment window in this session, and production was not touched. See
`FINAL-REPORT.md`.
