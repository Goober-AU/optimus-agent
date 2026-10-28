# Hello smoke test

## 1. Status

**NOT RUN in this session.** No live runtime window was performed, so no smoke test was executed
against GLM-5.3.

## 2. What it is for

"hello" is a transport smoke test, nothing more. It answers one question: does a native capture
travel through the connector into a receiver inference and produce a completed-use receipt. It
cannot answer whether meaning transferred, because a model can say hello without receiving anything.

Under the new event path, a hello smoke test is also the cheapest way to see the OCC3 indicator
light for a real reason: the light requires a completed-use receipt, so a lit OCC3 after a hello
means the whole delivery path worked end to end.

## 3. What a passing smoke test would establish

- normal SGLang serving unaffected;
- authenticated OCC-3 readiness;
- native source capture completed with a bound receipt;
- native receiver consumption completed with a receipt;
- the OCC3 indicator lit by that delivery;
- no text leakage of the source turn into the receiver's ordinary prompt.

## 4. What it would not establish

Semantic transfer. The report language must keep the two separate, and the indicator design enforces
the separation in the product: the light means a completed native event, not a correct answer.

## 5. Offline evidence that the path works without a GPU

`crates/pi-coding-agent/tests/occ3_latent_activity.rs` runs the real connector against a loopback
fixture that returns `x-optimus-occ3-receipt` only on a verified path. The receiver lease reaches
`Consumed` and exactly one latent delivery is projected. That is the offline equivalent of the
smoke test's transport question, and it passes 7 of 7.

It is a fixture, not a model. It proves the integration, not the science.
