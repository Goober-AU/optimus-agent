"""ContextBench live model-backed practice run (rot2 W27, CUSTOM-OPTIMUS).

Runs the dev-subset slice of the ContextBench verified dataset through the
REAL Optimus compaction pipeline (the `compaction-harness` binary) with a
live GLM 5.3 summarizer, then evaluates the retained context against the
gold annotations with the offline-validated W10 metric mirror
(`research.context_rot_r2/contextbench/`).

Labels: every artifact produced here is a CUSTOM-OPTIMUS LIVE-MODEL
practice run. It is not a ContextBench leaderboard number, not comparable
to upstream-scored runs, and not an official scorecard: the trajectories
are compaction-retention views over synthetic explorations built from the
gold annotations (no repository checkouts exist offline), so coverage
measures retention of the gold context through compaction, not free-form
agent exploration. See live/README.md for the full method and limits.
"""

LIVE_LABEL = "CUSTOM-OPTIMUS LIVE-MODEL practice run (not an official scorecard)"

# Deterministic slice identifiers (blake2b-seeded; see scenario.py).
SLICE_SEED_PARTS = ("contextbench", "w27-live-slice", "w27.1")
EXPLORE_SEED_PARTS = ("contextbench", "w27-explore-order", "w27.1")
DISTRACTOR_SEED_PARTS = ("contextbench", "w27-distractor", "w27.1")

# Slice bounds: dev instances whose gold context has 4..12 annotation items
# and 1200..9000 content characters (supports 5 compaction cycles with a
# meaningful summarize window under the scaled practice settings).
SLICE_MIN_ITEMS = 4
SLICE_MAX_ITEMS = 12
SLICE_MIN_CHARS = 1200
SLICE_MAX_CHARS = 9000

# Slice sized from the measured live smoke: 1.75-2.25 summarizer HTTP
# requests per fired cycle (the pipeline splits windows into parallel
# chunks and the max_tokens ladder can add rungs), fire rate ~0.85; so
# ~8.5 wire calls per (instance, arm) pair, ~510 expected for 30
# instances, worst case at the 600 hard cap.
# Slice/cycle sizing from the measured live runs: ~1.64 summarizer HTTP
# requests per cycle attempt (parallel window chunks + ladder rungs).
# The first live attempt (30 instances x 5 cycles) was aborted after 15
# pairs: the W10 row loader dropped the problem_statement column, so the
# transcripts ran blind (123 calls, invalidated and documented). The
# corrected run is sized into the remaining budget: 26 instances x 2 arms
# x 4 cycles = 208 attempts, ~341 expected, <=460 remaining under the
# 600 total practice cap (17 smoke + 123 blind + 460).
SLICE_SIZE = 26           # instances
CYCLES = 4                # append+compact cycles per instance and arm
PLANNED_CYCLE_ATTEMPTS = SLICE_SIZE * 2 * CYCLES   # 208
WIRE_CALL_HARD_CAP = 600  # absolute cap on HTTP requests, never exceeded

# Practice-run compaction scaling (transcripts are ContextBench-sized, far
# below production context sizes; the reserve/keep budget scales to the
# transcript so compaction actually fires). Fractions of the estimated
# transcript tokens.
RESERVE_FRACTION = 0.30
KEEP_FRACTION = 0.10
RESERVE_MIN = 200.0
KEEP_MIN = 60.0

# Arms (W10 scaffold ids A/B). The compaction-harness binary cannot express
# W10's offline arm B (jev-relevance is not a compaction surface), so the
# live practice arms are the two production compaction policies the binary
# does express: A = baseline (taskState off), B = protected task-state
# ledger (taskState prompt). Documented deviation, see README.md.
ARM_SETTINGS = {
    "A": {"taskState": "off"},
    "B": {"taskState": "prompt"},
}
