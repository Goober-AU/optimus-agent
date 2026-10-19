"""Context rot iteration 2 research harness.

Methodology fixes over iteration 1 (BUG-LEDGER.md M1-M5):
- M5: item seeds derive from blake2b digests of stable identifiers, never
  Python hash(); the same scenario id regenerates byte-identical items.
- M2: contradictory distractors carry an explicit authority rule (later
  user correction supersedes); every probe has a unique gold.
- M1/M3: the live-model answer protocol requires a delimiter before
  scoring; reasoning text is never parsed as an answer.
- M4: one versioned matcher per answer kind; scoring version recorded.
"""

SCORING_VERSION = "r2.1"
