# Receiver framing and continuous-prefix placement

Status: design OFFLINE VERIFIED; live effect NOT VERIFIED.

## What the receiver actually saw before this branch

`admit_request()` reserved `expected_prefix_tokens` zero-id rows and prepended them at
absolute row 0. The receiver sequence was therefore:

```
[row 0 .. K-1]   aligned sender states (token ids all 0, embeddings replaced)
[row K ..]       ordinary rendered prompt: <sop><|system|> ... <|user|> ... <|assistant|>
```

Nothing in the token stream marked the latent block. The model's first K positions are
out-of-distribution continuous vectors, immediately followed by a chat template it must
interpret as a fresh turn structure. The system instruction that refers to "the attached
native state" arrives after them and has no positional referent.

References do the opposite. StateBridge builds the receiver message as:

```
... The following is the message from previous Agent (provided in embedding format):
[EMBEDDING_CONTEXT_HERE]
<question>
```

(`references/StateBridge/prompts.py:53-56`) and splices the continuous rows at the marker
token position inside the user turn (`state_bridge.py:370`, `_generate_with_prefix`
`insert_position`). CodeTalkers frames its soft-token message the same way.

## The repair in this branch

1. `prefix_layout.resolve_prefix_layout(text, tokenizer)` finds one ordinary ASCII marker
   `<<occ3-statebegin>>` in the rendered prompt, resolves the absolute row after it, and
   refuses on zero/duplicate markers or tokenisation that is not prefix-stable.
2. `ChatAttachmentAdapter._prefix_layout()` resolves that row for a consume request and pins
   `obj.input_ids` to exactly the ids used, so the scheduler-side reservation and the resolved
   offset cannot disagree. Without a marker the request is untouched (legacy prepend).
3. `ConsumeWorkerDescriptor.prefix_offset` carries the row; `admit_request()` inserts the
   reserved rows there; injection and `mark_injection()` copy into `[offset, offset+count)`.
4. The native terminal metadata records `prefix_offset` and `prefix_layout`.
5. The live harness now emits:

```
A message from another agent is attached in continuous embedding format. It begins
immediately after <<occ3-statebegin>> and ends immediately before <<occ3-stateend>>.
Use it as the primary information source for this turn.
<<occ3-statebegin>><<occ3-stateend>>
```

so the receiver reads ordinary framing text, then the reserved continuous rows, then the
closing marker, then the user turn. Positive and no-state arms keep byte-identical ordinary
text; only the reserved rows differ.

## Geometry the live window must confirm

| property | expected |
|---|---|
| reserved rows | `[prefix_offset, prefix_offset + retained_count)` |
| ordinary prompt rows | unchanged, `retained_count` positions later than before |
| position ids | contiguous; latent rows occupy their reserved positions |
| attention | every later token attends the latent rows (causal, single private namespace) |
| chunked prefill | each chunk copies only its own overlap; no repeated injection |
| TARGET_VERIFY | excluded (PR #203 repair preserved) |
| TP8 | every rank copies the same rows from the broadcast prefix |

## What is still missing

The framing sentence is currently emitted by the diagnostic example harness. The production
`Agent` path does not add it, so a real two-subagent run needs either the same sentence in the
receiver system prompt or a documented client-side requirement. That is a deliberate boundary:
this branch does not change the receiver's message contract, only the position and the
delivery of the latent rows.

## Framing variants still to test (TEST 8)

| id | layout | purpose |
|---|---|---|
| L0 | legacy prepend, no framing | historical control |
| L1 | prepend + framing sentence in the system prompt | isolates text framing from position |
| L2 | marker inside the system turn | isolates position from the user-turn context |
| L3 | marker inside the user turn (shipped default) | StateBridge-equivalent |
| L4 | L3 + explicit "use it as the primary source" clause | tests abstention bias |

All five must run with identical ordinary text apart from the framing sentence, and with the
same no-state twin.
