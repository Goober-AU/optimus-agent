# Calibration artifact integrity

## 1. Format

`OCC3CAL1`: a magic prefix, a 4-byte header length, an ASCII JSON header, then plain float32
tensors. The JSON header is fully validated before a single tensor byte is read.

## 2. Deserialization order

1. magic and length bounds
2. JSON parse, rejecting duplicate keys, NaN and Infinity
3. exact key set
4. every binding field
5. shape table
6. method id (defaulting to the module's own id)
7. hidden dimension
8. provenance equality
9. artifact digest recompute
10. payload length and sha256
11. `torch.load(weights_only=True)`
12. dtype, shape and finiteness per tensor
13. cross-tensor consistency and a tensor-set digest

`torch.load` is only ever called with `weights_only=True`. A test pins that; `weights_only=False`
appears nowhere.

## 3. What the artifact binds to

Method id, hidden dimension, selected layer, row count, `ridge_rel`, absolute ridge, held-out R2,
model fingerprint, checkpoint revision, tokenizer fingerprint, embedding identity, capture method
identity, predictor position offset, numerical precision, corpus digest, artifact digest.

Refused: wrong method id, wrong hidden dimension, mismatched model / checkpoint / tokenizer
fingerprint, tampered or missing digest, shape mismatch between the Gram matrix or hidden residual
and the hidden dimension.

## 4. Digest caveat, stated rather than hidden

`torch.save` bytes are not reproducible across processes, so `payload_sha256` is verified against the
same file's header. Cross-process content identity is `corpus_digest` plus `tensor_digest`, both
hashed from `tensor.numpy().tobytes()` in canonical tensor order. `bytes(tensor.untyped_storage())`
was measured at 4.7 s per MiB; the numpy path is 0.5 ms per MiB.

## 5. Telemetry

`describe()` is scalar-only: method id, rows, hidden dimension, ridge, `ridge_rel`, held-out R2 and
resident bytes. The artifact may contain derived model-state information, so it is never rendered in
normal telemetry and never enters a receipt.

## 6. Attestation

`native_install._REQUIRED_PLUGIN` now attests `statebridge_calibrated.py`, `calibration_corpus.py`,
`calibration_probe.py` and `align_diagnostics.py` alongside the rest of the extension, so the
calibrated surface is digest-attested at install. `deploy/v3/native_package.py` derives its shipped
method list from the registry instead of a literal, and a test asserts that the attested module set
and the packaged method list both contain the calibrated id.

## 7. Tests

`tests/test_calibration_artifact.py` (19) covers header validation, method / provenance / digest
refusal, safe load and the scalar-only projection. `tests/test_native_method_registry.py` covers the
registry agreement and the packaging path. See `REGRESSION-GATES.md` for the counts.
