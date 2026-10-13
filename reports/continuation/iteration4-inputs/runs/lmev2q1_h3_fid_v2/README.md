# LME-v2 q1 loop: 36fe48ef 'Toolbox links' fixed via write-time + read-time + answer-side stack

Runs (variant improved, env web, 3-session mini store 1ad1af4b+b1c719fb+2e66df34, cap 40000,
model dgx-glm53/glm-5.3, driver imp4-targetA shared binary at 874a67e3d):

| run | hypothesis | settings delta vs mini cap40k | 36fe48ef |
|---|---|---|---|
| lmev2q1_h1_pagestruct | H1 write-time: page-structure importInstructions | +importInstructions (page/route/title, section headings, exact link names+counts, per viewing context) | 0.0 ("two": gold note extracted but ranked 15/27, cut by 12000-char budget) |
| lmev2q1_h2_recall40k | H2 read-time: budget+rerank | skipIngest reuse of H1 store; maxRecallChars 32000, maxRecallEntries 27, recallRerank on (pool 20) | 0.0 (rerank kept books notes; Cyberpunk note ranked 15) |
| lmev2q1_h2b_v5recall | H2b read-time: full v5 recall stack | skipIngest reuse; +distill, rerank pool 200, queryExtract, expansion 2 | 0.0 but answer showed BOTH Toolbox counts; model picked "two" (recency) — answer-side failure |
| lmev2q1_h3_fid_v2 | H3: all three, fidelity v2 | fresh ingest; importInstructions v2 (dedupe + no inferred permissions) + v5 recall stack + no-narration answerInstruction (match-conditions-not-recency) | **1.0 "\boxed{six}"** |

## Root causes fixed (question 36fe48ef, gold 'six')

1. B-EXTRACTION (fixed in H1): default import instructions distilled the Cyberpunk forum page
   (creator view, Toolbox with 6 links: Appearance, Bans, Delete forum, Edit forum, Moderation log,
   Trash) to a forum-creation note only. The page-structure clause extracts a per-page structure
   note naming every sidebar section with exact link names and counts.
2. D-RETRIEVAL (fixed in H3 via v5 recall stack): the gold note ranked 15/27 lexically; only the
   top ~8 entries fit the recall char budget. Query distill + extract + expansion (2 angles) +
   rerank pool 200 surfaced it (H2b proof: answer quoted both counts).
3. G-ANSWER (fixed in H3): the books-forum note (non-moderated view, Toolbox 2 links) was answered
   because it was most recent. Two changes: (a) importInstructions v2 forbids inferring viewer
   permissions from visible controls (the H1 books note falsely claimed "viewer has moderation
   access"); (b) answerInstruction prefers the note satisfying the question's conditions (a page
   the viewer moderates) over the most recent note.

## Regression (same H3 store, question-scoped)

- f928c075 (gold 'moderator'): 0.0 — answer 'Toolbox'. Needs an OTHER user's profile page for
  the own-vs-other comparison; the 3 mini sessions contain no other-user profile, so this
  question is not answerable from the mini env regardless of extraction (evidence absent; the
  own-profile 'X is a moderator on:' section IS now extracted).
- 499488a6 (gold 'none'): 0.0 — answer 'Subscriber-only'. Needs creator/subscriber/viewer forum
  views; the mini sessions only contain the creator view (Cyberpunk) and non-subscribed views
  (books/television) plus a front page whose space-forum snapshot lacks the Hide control.
  Not answerable from the mini env (evidence absent).
- Both need the full-env cap-40000 re-capture (separate decision; ~5h ingest per env at cap
  2000-era rates; the mini env is 3 sessions).

## Reuse

- The H3 store (this run's envs/web) is reusable via skipIngest for any read-time experiment on
  the 3-session mini env. Do NOT re-ingest unless write-time settings change.
- Scoring: question-scoped pattern (single qs file in scratch dir); the full lmev2 qs_web.jsonl
  has 5 questions and scores missing records as flagged zeros — the committed scores.json uses
  the 3-question private copy at imp3-lmev2ing .../private-scoring-questions.
