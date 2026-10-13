# Write-time extraction residue probe (improve/mem4-write-residue)

Worker `writeres` · model dgx-glm53/glm-5.3 (only) · driver `imp4-targetA`
memory_bench.exe (shared binary, no rebuild). Probes the two stem-sweep
write-time residues: **conv-42_q4 (D2:23)** and **conv-47_q102 (D16:9)**.

## 1. conv-47_q102 - "What did James enjoy doing on cold winter days?" (cat 4)

**Classification: TRUE write-time residue - speaker preference/self-description
in the speaker's own words (the q149 attribute-clause class).**

- Gold D16:9: "James: I also love to read, especially while snuggled under the
  covers on a cold winter day."
- Frozen store scan (improved-s0/envs/conv-47, all 158 memory entries): 0 hits
  for `snuggl` / `under the covers` / `cold winter` / `winter day`. The mirrored
  **John** statement (D16:8, "John relaxes by reading fiction") WAS extracted -
  the extractor processed session_16 but dropped James's self-description.

**Verification (AA typeB single-session pattern):** re-ingested conv-47
session_16 (16 events) into a fresh content-addressed cache with the attribute
clause v2 (byte-identical to `attrs-q149-h4b-typeb-v2`) + the promoted read
config (recallQueryStemming=true, det recall 24/24000, G4 answer instruction).
- 7 entries extracted; gold note: **"James loves winter reading under the
  covers"** - "James loves to read, especially 'snuggled under the covers on a
  cold winter day'."
- Answer: "Reading snuggled under the covers" · **score 0.8889 PASS**
  (question-scoped local token-F1, unflagged; baseline 0.0). Gold note recalled
  at rank 1 of 6.
- Run `wrq102-attrv2`, cache key `3afe0f0a9f8cc2fa`, ledger attempt appended.

**Verdict: V's attribute clause v2 GENERALIZES** (second conversation after
conv-26: a preference with manner/context detail, extracted verbatim in the
speaker's own words). No new clause type; the fix rides the full re-ingest
campaign.

## 2. conv-42_q4 - "What pets wouldn't cause any discomfort to Joanna?" (cat 3)

**Classification: NOT a write-time residue - dataset-gold inference gap
(correction of the stem-sweep summary).**

- Gold answer: "Hairless cats or pigs, since they don't have fur..." with
  evidence D2:23 ("I'm allergic to most reptiles and animals with fur").
- The evidence FACT is extracted AND retrieved: frozen store note
  **"Joanna's animal allergies (most reptiles and animals with fur)"** was in
  the ss-q4-r1 recall window (search rank 11, direct recall).
- The gold ANSWER's species ("hairless", "pigs", "discomfort") appear **nowhere
  in the 29-session transcript** - verified both in env_conv-42.json and the
  original locomo10.json sample (Joanna/Nate). The answer requires world-knowledge
  synthesis (fur-free pets -> hairless cats/pigs) beyond any memory of the
  transcript; the model correctly reports the notes lack it ("not mentioned").
- **No extraction clause can ever produce these tokens.** Not fixable at write
  time; handle at answer time (world-knowledge prompting) or scorer gold
  normalization - recommend deprioritizing.

## 3. Write-time gap taxonomy (cumulative)

| class | example | fix |
|---|---|---|
| KU updates | mid-conversation changed facts | fullstack update clause |
| UI features (capture granularity) | lme_v2 web 36fe48ef | captureAxtreeMaxChars 2000->40000 |
| speaker attributes (own words) | conv-26_q149; **now conv-47_q102** | attribute clause v2 (verified 2x) |
| dataset-gold inference gap (NEW) | **conv-42_q4** | none at write time; answer-stage or scorer-side |

## 4. Regression

Regression-free by construction: the typeB ingest wrote only to the fresh
content-addressed cache `writeres-scratch/typeb-cache/conv-47--3afe0f0a...`;
frozen imp3-gate stores were opened read-only for the scans; baseline run data
untouched. No rebuilds (shared imp4-targetA binary).

## Files

- `write-residue-evidence.json` - full evidence bundle (scans, run record, taxonomy)
- `env-conv47-session16.json`, `base-settings-stem.json`, `ov-attrclause-v2.json` - run inputs
- `q102-wrq102-attrv2-*` - manifest, run.jsonl, env.jsonl, ingested.json, scores.json, evaluator.log
