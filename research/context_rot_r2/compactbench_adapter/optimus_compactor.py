"""CompactBench adapter: the real Optimus Rust compaction behind the Compactor contract.

Drives the committed one-shot harness binary (crates/pi-coding-agent/src/bin/
compaction_harness.rs; set ``HARNESS``/``ROT2_HARNESS_BIN`` to the build you
want) from the stock CompactBench runner in DELTA-APPEND mode: one persistent
per-case session directory, each ``compact()`` call appends only the turns the
session has not seen yet, and the harness re-compacts the live session
(49f473d8d ``SessionManager::continue_recent`` semantics).

    compactbench run \
      --method research/context_rot_r2/compactbench_adapter/optimus_compactor.py:OptimusRustCompactorTaskState \
      --suite starter --provider mock --model mock-deterministic \
      --case-count 5 --drift-cycles 3 \
      --benchmarks-dir <compactbench-checkout>/benchmarks/public \
      -o results/task-state.jsonl

Arms (same driver, different settings.taskState):

- ``OptimusRustCompactorTaskState`` — task-state ledger enabled ("prompt").
- ``OptimusRustCompactorPlain``      — ledger disabled ("off").

Controls (deterministic, zero model calls): ``EchoFirstNCompactor`` (head
truncation floor), ``FullTranscriptOracleCompactor`` (verbatim ceiling).

Empirical harness behavior this adapter is built around (evidence in
README.md; reproduced by probe_harness_behavior.py):

1. Sessions persist across invocations: ``continue_recent`` reopens the most
   recent session file in the directory (falling back to a new one). The
   session entry's ``cwd`` must match the directory, so a copied session dir
   is NOT continuable (a clone silently forks a fresh session); the adapter
   therefore uses ONE stable live dir per case and never clones.
2. The compaction cut must immediately precede a USER turn; cuts that land
   before an assistant turn make the pipeline reject the summarizer handoff
   ("unusable handoff" error). The adapter aims candidates at user turns.
3. Tail sizing: walking backwards, message ``j`` joins the kept tail iff the
   estimate of the tail without it (``cum[j+1] = chars/4 + 0.6`` per message
   of ``[j+1, end)``) is ``<= keepRecentTokens``; the cut sits before the
   first message that does not fit. In delta mode the walk clamps at the
   previous compaction's firstKeptEntryId: an oversized keepRecentTokens
   keeps the whole live window and summarizes nothing (harmless).
4. Assistant turns carry a conservative ``ceil(chars/4)`` usage anchor, so
   ``tokensBefore``/``compactedContextTokens`` are non-zero (anchored on the
   last assistant turn; they undercount prior user turns).
5. Appends are flushed (and the turn-map sidecar stored) BEFORE compaction
   runs, so after an error-path exit the session deterministically holds the
   new turns: the adapter re-reads the sidecar before every attempt and sends
   only the turns the session is still missing (possibly none).

All quality numbers produced with the mock provider are CUSTOM plumbing-runs
only; see README.md for the labeling rules.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from compactbench.compactors._utils import fit_summary_text
from compactbench.compactors.base import Compactor
from compactbench.contracts import CompactionArtifact, StructuredState, Transcript

#: Harness binary. Override with the ``HARNESS`` (or ``ROT2_HARNESS_BIN``)
#: environment variable.
DEFAULT_HARNESS_BIN = Path("C:/Users/openclawuser/rot2-shared-target/debug/compaction-harness.exe")

#: Per-message tail estimate that reproduces the harness's keep-recent sizing
#: (see README.md "cut-point ladder"). Validated on the starter templates and
#: on delta sessions: message j joins the tail iff cum[j+1] <= keepRecentTokens.
_TAIL_TOKENS_PER_CHAR = 0.25
_TAIL_TOKENS_PER_MESSAGE = 0.6

#: Bounds mirrored from compactbench.contracts.artifact so a hostile ledger
#: can never produce an invalid artifact.
_STATE_ITEM_MAX_CHARS = 500
_STATE_ITEM_MAX_COUNT = 200
_WARNING_MAX_CHARS = 500
_WARNING_MAX_COUNT = 50

#: Upper bound on harness spawns per compact() call (cut-point ladder width).
_MAX_ATTEMPTS = 10

#: Harness warnings that mark a degenerate (non-advancing) compaction: the
#: ladder treats them as "not landed" and tries the next candidate.
_DEGENERATE_WARNING_MARKERS = (
    "no compaction prepared",
    "empty summarize window",
)

#: Harness error prefixes that leave the session dir in an UNKNOWN state:
#: the remaining attempts (and later calls) must not reuse it.
_POISON_ERROR_PREFIXES = ("session flush after append",)

_SUMMARIZER_MODE = "fixture"
_CONTEXT_WINDOW = 1_000_000.0
_MAX_TOKENS = 64_000.0
_TURN_MAP_NAME = ".rot2-turn-map.json"


def _harness_bin() -> Path:
    override = os.environ.get("HARNESS") or os.environ.get("ROT2_HARNESS_BIN")
    return Path(override) if override else DEFAULT_HARNESS_BIN


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _render_turns(turns: list[Any]) -> str:
    return "\n\n".join(f"{turn.role.value.upper()}: {turn.content}" for turn in turns)


def _clip_state_items(items: list[str]) -> list[str]:
    """Fit ledger texts into the StructuredState section bounds, deduplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()[:_STATE_ITEM_MAX_CHARS].strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= _STATE_ITEM_MAX_COUNT:
            break
    return out


def _clip_warnings(warnings: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        text = str(warning).strip()[:_WARNING_MAX_CHARS].strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= _WARNING_MAX_COUNT:
            break
    return out


def _ledger_texts(entries: Any) -> list[str]:
    """Extract display texts from a ledger entry list.

    Observed shapes: constraints/decisions/superseded/evidence/openWork hold
    ``{"id": ..., "text": ..., "entry": ...}`` dicts; anchors hold bare
    strings. Anything else is tolerated defensively.
    """
    if not isinstance(entries, list):
        return []
    texts: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            text = entry.get("text")
            if isinstance(text, str):
                texts.append(text)
        elif isinstance(entry, str):
            texts.append(entry)
    return texts


def structured_state_from_ledger(ledger: Any) -> StructuredState:
    """Map the harness task-state ledger onto the six contract sections.

    Mapping (COMPACTBENCH-ADAPTER-DESIGN.md 2.4):

    - forbidden_behaviors <- ledger constraints
    - locked_decisions   <- ledger decisions
    - deferred_items     <- ledger superseded
    - immutable_facts    <- ledger evidence + anchors
    - unresolved_items   <- ledger openWork
    - entity_map         <- empty (not produced by the pipeline today)
    - retractions        <- dropped (no contract section; see README.md)
    """
    if not isinstance(ledger, dict):
        return StructuredState()
    anchors_raw = ledger.get("anchors")
    anchors = [str(a) for a in anchors_raw if isinstance(a, (str, int, float)) and str(a).strip()]
    evidence = _ledger_texts(ledger.get("evidence"))
    return StructuredState(
        forbidden_behaviors=_clip_state_items(_ledger_texts(ledger.get("constraints"))),
        locked_decisions=_clip_state_items(_ledger_texts(ledger.get("decisions"))),
        deferred_items=_clip_state_items(_ledger_texts(ledger.get("superseded"))),
        immutable_facts=_clip_state_items(evidence + anchors),
        unresolved_items=_clip_state_items(_ledger_texts(ledger.get("openWork"))),
        entity_map={},
    )


def tail_candidates(turns: list[Any]) -> list[float]:
    """keepRecentTokens values that land the cut immediately before a user turn.

    The harness walks the kept tail backwards: message ``j`` joins the tail iff
    the estimate of the tail without it (``cum[j+1]``, ``chars/4 + 0.6`` per
    message) is ``<= keepRecentTokens``, and the pipeline rejects cuts that do
    not precede a user turn. For each user turn ``j`` (with a non-empty
    summarize window ``[0, j)``), the midpoint of ``cum[j]`` and ``cum[j+1]``
    includes exactly ``[j, end)`` in the tail. Candidates are ordered most
    aggressive first (shortest valid tail).
    """
    count = len(turns)
    cumulative = [0.0] * (count + 1)
    for index in range(count - 1, -1, -1):
        cumulative[index] = (
            cumulative[index + 1]
            + len(turns[index].content) * _TAIL_TOKENS_PER_CHAR
            + _TAIL_TOKENS_PER_MESSAGE
        )
    candidates: list[float] = []
    for boundary in range(count - 1, 0, -1):
        if turns[boundary].role.value == "user":
            candidates.append((cumulative[boundary] + cumulative[boundary + 1]) / 2.0)
    return candidates


def _error_response(message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "summary": "",
        "firstKeptEntryId": "",
        "tokensBefore": 0.0,
        "compactedContextTokens": 0.0,
        "ledger": None,
        "summarizedTurnIds": [],
        "keptTurnIds": [],
        "warnings": [],
        "error": message,
    }


def _parse_response(stdout: bytes, stderr: bytes) -> dict[str, Any]:
    lines = [line for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        detail = stderr.decode("utf-8", errors="replace").strip()[:200]
        return _error_response(f"harness wrote no response on stdout (stderr: {detail or 'empty'})")
    try:
        response = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return _error_response(f"harness response is not JSON: {exc}")
    if not isinstance(response, dict):
        return _error_response("harness response is not a JSON object")
    return response


def _degenerate(response: dict[str, Any]) -> str | None:
    """Return the marker of the degenerate warning the response carries, if any."""
    for warning in response.get("warnings") or []:
        text = str(warning)
        for marker in _DEGENERATE_WARNING_MARKERS:
            if marker in text:
                return marker
    return None


def _poisoned(response: dict[str, Any]) -> bool:
    """True when the harness left its session dir in an unknown state.

    Kill/crash paths (timeout, no stdout) and a failed flush-after-append can
    tear the "appends flushed, sidecar stored" invariant; the safe reaction is
    to abandon the directory rather than risk duplicated appends.
    """
    error = response.get("error")
    if not isinstance(error, str):
        return False
    if error.startswith("harness timed out"):
        return True
    if error.startswith("harness wrote no response"):
        return True
    return any(error.startswith(prefix) for prefix in _POISON_ERROR_PREFIXES)


class OptimusRustCompactor(Compactor):
    """Compactor arm that drives the real Optimus Rust compaction pipeline.

    One instance per benchmark case (the runner constructs a fresh compactor
    per case). The instance owns one persistent session directory (the "live"
    dir). Each ``compact()`` call:

    1. reads the live dir's ``.rot2-turn-map.json`` sidecar (the ids of the
       conversation turns the session already holds),
    2. if the sidecar is a prefix of the incoming transcript, appends only
       the missing turns (delta-append); otherwise opens a fresh session dir
       and re-sends the full transcript (resync),
    3. walks the cut-point ladder; before every attempt it re-reads the
       sidecar so an attempt whose appends landed but whose compaction
       failed is retried with zero new turns instead of duplicating them,
    4. on a landed compaction translates the response into a
       CompactionArtifact; the live dir becomes the attempt dir.

    ``previous_artifact`` is ignored by design: the harness session carries
    its own compaction state (summary + kept tail + ledger) across calls, and
    that state — not a re-rendered artifact — is what the next compaction
    sees, which is the repeated-compaction semantics the benchmark measures.
    """

    name: ClassVar[str] = "optimus-rust"
    version: ClassVar[str] = "0.2.0"

    #: Which compaction settings the harness should use: "prompt" keeps the
    #: task-state ledger, "off" disables it. Fixed per subclass.
    task_state: ClassVar[str] = "off"

    def __init__(self, provider: Any, model: str) -> None:
        super().__init__(provider, model)
        self._harness = _harness_bin()
        self._reserve_tokens = _env_float("ROT2_RESERVE_TOKENS", 4000.0)
        self._timeout_s = _env_float("ROT2_HARNESS_TIMEOUT_S", 120.0)
        pinned = os.environ.get("ROT2_KEEP_RECENT_TOKENS")
        try:
            self._pinned_keep_recent: float | None = (
                float(pinned) if pinned not in (None, "") else None
            )
        except ValueError:
            self._pinned_keep_recent = None
        self._tmp = tempfile.TemporaryDirectory(prefix="optimus-rust-compactor-")
        self._root = Path(self._tmp.name)
        self._live_dir: Path | None = None
        #: (id, content) of every turn appended to the live session dir, in
        #: order — the content-aware mirror of the harness turn-map sidecar.
        self._sent: list[tuple[Any, str]] = []
        self._session_seq = 0
        self._compact_calls = 0
        self._harness_calls = 0

    # ------------------------------------------------------------------ #
    # Session bookkeeping
    # ------------------------------------------------------------------ #

    def _turn_map(self, session_dir: Path | None) -> list[Any]:
        if session_dir is None:
            return []
        try:
            raw = (session_dir / _TURN_MAP_NAME).read_text(encoding="utf-8")
        except OSError:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _new_session_dir(self, tag: str) -> Path:
        directory = self._root / f"session-{self._session_seq:03d}-{tag}"
        self._session_seq += 1
        return directory

    @staticmethod
    def _is_prefix(candidate: list[Any], ids: list[Any]) -> bool:
        return ids[: len(candidate)] == candidate

    # ------------------------------------------------------------------ #
    # Compactor contract
    # ------------------------------------------------------------------ #

    async def compact(
        self,
        transcript: Transcript,
        config: dict[str, Any] | None = None,
        previous_artifact: CompactionArtifact | None = None,
    ) -> CompactionArtifact:
        """Append the unseen turns to the live session and re-compact it."""
        self._compact_calls += 1
        turns = list(transcript.turns)
        harness_turns = [t for t in turns if t.role.value in ("user", "assistant")]
        dropped_system = len(turns) - len(harness_turns)

        warnings: list[str] = []
        if dropped_system:
            warnings.append(
                f"dropped {dropped_system} system turn(s): the harness accepts user/assistant turns only"
            )
        if not harness_turns:
            warnings.append("no user/assistant turns to compact; returning an empty artifact")
            return CompactionArtifact(
                summaryText="",
                structured_state=StructuredState(),
                selectedSourceTurnIds=[],
                warnings=_clip_warnings(warnings),
                methodMetadata=self._metadata(
                    harness_status="skipped",
                    keep_recent=None,
                    attempts=[],
                    fallback="empty-transcript",
                    total_turns=len(turns),
                    dropped_system=dropped_system,
                    response=None,
                    session_mode="skipped",
                    map_length=0,
                    delta_turns=0,
                ),
            )

        turn_ids = [turn.id for turn in harness_turns]
        live_map = self._turn_map(self._live_dir)
        sent_prefix = [(t.id, t.content) for t in harness_turns[: len(self._sent)]]
        in_sync = (
            self._live_dir is not None
            and self._sent == sent_prefix
            and len(live_map) == len(self._sent)
        )
        if in_sync:
            session_dir = self._live_dir
            session_mode = "delta"
        else:
            session_dir = self._new_session_dir("resync" if self._live_dir else "initial")
            session_mode = "resync" if self._live_dir else "initial"
            self._sent = []
        # Track the dir from the moment it is chosen, landing or not: a failed
        # call may still have flushed its appends there, and the map-prefix
        # check on the next call needs the dir to find them.
        self._live_dir = session_dir
        map_at_entry = len(live_map) if in_sync else 0

        candidates = (
            [self._pinned_keep_recent]
            if self._pinned_keep_recent is not None
            else tail_candidates(harness_turns)
        )
        attempts: list[dict[str, Any]] = []
        response: dict[str, Any] | None = None
        keep_recent: float | None = None
        for index, candidate in enumerate(candidates[:_MAX_ATTEMPTS]):
            current_map = self._turn_map(session_dir)
            if not self._is_prefix(current_map, turn_ids) or len(current_map) != len(self._sent):
                # The dir diverged from the conversation (only possible after
                # external interference): restart from a fresh session dir.
                session_dir = self._new_session_dir("resync")
                self._live_dir = session_dir
                self._sent = []
                session_mode = "resync"
                current_map = []
            new_turns = harness_turns[len(current_map):]
            response = await self._invoke(session_dir, new_turns, candidate, index)
            growth = max(0, len(self._turn_map(session_dir)) - len(current_map))
            self._sent.extend((t.id, t.content) for t in new_turns[:growth])
            keep_recent = candidate
            attempts.append(self._attempt_record(candidate, response, len(new_turns)))
            if self._landed(response):
                self._live_dir = session_dir
                return self._artifact_from_response(
                    response,
                    keep_recent,
                    attempts,
                    warnings,
                    total_turns=len(turns),
                    dropped_system=dropped_system,
                    session_mode=session_mode,
                    map_length=map_at_entry,
                    delta_turns=len(new_turns),
                )
            if _poisoned(response):
                # Unknown on-disk state: never append to this dir again.
                session_dir = self._new_session_dir("resync")
                self._live_dir = session_dir
                self._sent = []
                session_mode = "resync"

        return self._fallback_artifact(
            harness_turns, attempts, warnings, total_turns=len(turns), dropped_system=dropped_system,
            session_mode=session_mode, map_length=map_at_entry,
        )

    @staticmethod
    def _landed(response: dict[str, Any]) -> bool:
        """A landed attempt: a real compaction that advanced the session."""
        if response.get("status") != "ok":
            return False
        if not str(response.get("summary") or "").strip():
            return False
        return _degenerate(response) is None

    # ------------------------------------------------------------------ #
    # Harness invocation
    # ------------------------------------------------------------------ #

    async def _invoke(
        self, session_dir: Path, new_turns: list[Any], keep_recent: float, attempt: int
    ) -> dict[str, Any]:
        request = {
            "sessionDir": str(session_dir).replace("\\", "/"),
            "newTurns": [
                {"role": turn.role.value, "content": turn.content, "turnId": turn.id}
                for turn in new_turns
            ],
            "settings": {
                "reserveTokens": self._reserve_tokens,
                "keepRecentTokens": keep_recent,
                "taskState": self.task_state,
                "contextWindow": _CONTEXT_WINDOW,
                "maxTokens": _MAX_TOKENS,
            },
            "summarizer": _SUMMARIZER_MODE,
        }
        self._harness_calls += 1
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self._harness),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return _error_response(f"harness spawn failed ({self._harness}): {exc}")
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(request).encode("utf-8")), timeout=self._timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return _error_response(f"harness timed out after {self._timeout_s:.0f}s")
        return _parse_response(stdout, stderr)

    @staticmethod
    def _attempt_record(keep_recent: float, response: dict[str, Any], new_turns: int) -> dict[str, Any]:
        error = response.get("error")
        return {
            "keepRecentTokens": round(keep_recent, 3),
            "status": response.get("status"),
            "newTurnsSent": new_turns,
            "summarizedTurns": len(response.get("summarizedTurnIds") or []),
            "keptTurns": len(response.get("keptTurnIds") or []),
            "error": str(error)[:_WARNING_MAX_CHARS] if error else None,
            "warnings": [str(w)[:_WARNING_MAX_CHARS] for w in (response.get("warnings") or [])],
        }

    # ------------------------------------------------------------------ #
    # Artifact construction
    # ------------------------------------------------------------------ #

    def _artifact_from_response(
        self,
        response: dict[str, Any],
        keep_recent: float,
        attempts: list[dict[str, Any]],
        warnings: list[str],
        *,
        total_turns: int,
        dropped_system: int,
        session_mode: str,
        map_length: int,
        delta_turns: int,
    ) -> CompactionArtifact:
        summary, fit_warnings = fit_summary_text(str(response.get("summary") or ""))
        merged = warnings + list(response.get("warnings") or []) + fit_warnings
        if len(attempts) > 1:
            merged.append(
                "cut-point ladder: "
                f"{len(attempts)} harness attempts, landed at keepRecentTokens={keep_recent:.1f}"
            )
        selected = list(response.get("summarizedTurnIds") or []) + list(
            response.get("keptTurnIds") or []
        )
        return CompactionArtifact(
            summaryText=summary,
            structured_state=structured_state_from_ledger(response.get("ledger")),
            selectedSourceTurnIds=selected,
            warnings=_clip_warnings(merged),
            methodMetadata=self._metadata(
                harness_status=str(response.get("status")),
                keep_recent=keep_recent,
                attempts=attempts,
                fallback=None,
                total_turns=total_turns,
                dropped_system=dropped_system,
                response=response,
                session_mode=session_mode,
                map_length=map_length,
                delta_turns=delta_turns,
            ),
        )

    def _fallback_artifact(
        self,
        turns: list[Any],
        attempts: list[dict[str, Any]],
        warnings: list[str],
        *,
        total_turns: int,
        dropped_system: int,
        session_mode: str,
        map_length: int,
    ) -> CompactionArtifact:
        """No harness attempt compacted: the live context is the un-compacted transcript."""
        text, fit_warnings = fit_summary_text(_render_turns(turns))
        if attempts:
            last = attempts[-1]
            reason = last.get("error") or "; ".join(last.get("warnings") or []) or last.get("status")
        else:
            reason = "no cut-point candidate exists for this transcript"
        merged = warnings + fit_warnings + [
            f"harness produced no compaction ({reason}); artifact is the un-compacted transcript"
        ]
        return CompactionArtifact(
            summaryText=text,
            structured_state=StructuredState(),
            selectedSourceTurnIds=[turn.id for turn in turns],
            warnings=_clip_warnings(merged),
            methodMetadata=self._metadata(
                harness_status=str(attempts[-1]["status"]) if attempts else "skipped",
                keep_recent=attempts[-1]["keepRecentTokens"] if attempts else None,
                attempts=attempts,
                fallback="uncompacted-transcript",
                total_turns=total_turns,
                dropped_system=dropped_system,
                response=None,
                session_mode=session_mode,
                map_length=map_length,
                delta_turns=0,
            ),
        )

    def _metadata(
        self,
        *,
        harness_status: str,
        keep_recent: float | None,
        attempts: list[dict[str, Any]],
        fallback: str | None,
        total_turns: int,
        dropped_system: int,
        response: dict[str, Any] | None,
        session_mode: str,
        map_length: int,
        delta_turns: int,
    ) -> dict[str, Any]:
        return {
            "method": self.name,
            "taskState": self.task_state,
            "harnessBinary": str(self._harness),
            "harnessStatus": harness_status,
            "sessionMode": session_mode,
            "sessionMapTurns": map_length,
            "deltaTurnsSent": delta_turns,
            "keepRecentTokens": keep_recent,
            "reserveTokens": self._reserve_tokens,
            "summarizer": _SUMMARIZER_MODE,
            "tokensBefore": response.get("tokensBefore") if response else None,
            "compactedContextTokens": response.get("compactedContextTokens") if response else None,
            "summarizedTurns": len(response.get("summarizedTurnIds") or []) if response else 0,
            "keptTurns": len(response.get("keptTurnIds") or []) if response else 0,
            "attempts": attempts,
            "fallback": fallback,
            "transcriptTurns": total_turns,
            "systemTurnsDropped": dropped_system,
            "compactCalls": self._compact_calls,
            "harnessCalls": self._harness_calls,
        }

    def close(self) -> None:
        """Remove the per-instance session root (best effort; also runs on GC)."""
        self._tmp.cleanup()


class OptimusRustCompactorTaskState(OptimusRustCompactor):
    """Arm with the task-state ledger enabled (settings.taskState = "prompt")."""

    name: ClassVar[str] = "optimus-rust-task-state"
    task_state: ClassVar[str] = "prompt"


class OptimusRustCompactorPlain(OptimusRustCompactor):
    """Arm without the task-state ledger (settings.taskState = "off")."""

    name: ClassVar[str] = "optimus-rust-plain"
    task_state: ClassVar[str] = "off"


class EchoFirstNCompactor(Compactor):
    """Control: keep the first N turns verbatim, drop everything later.

    Head-truncation floor. Dual of the built-in truncate-last-n: strong on
    early context (buried constraints), blind to anything stated recently.
    Zero model calls. N defaults to 10; override with ROT2_ECHO_FIRST_N.
    """

    name: ClassVar[str] = "echo-first-n"
    version: ClassVar[str] = "0.1.0"
    n_default: ClassVar[int] = 10

    def __init__(self, provider: Any, model: str) -> None:
        super().__init__(provider, model)
        raw = os.environ.get("ROT2_ECHO_FIRST_N")
        try:
            self.n = int(raw) if raw not in (None, "") else self.n_default
        except ValueError:
            self.n = self.n_default

    async def compact(
        self,
        transcript: Transcript,
        config: dict[str, Any] | None = None,
        previous_artifact: CompactionArtifact | None = None,
    ) -> CompactionArtifact:
        kept = transcript.turns[: self.n]
        text, warnings = fit_summary_text(_render_turns(kept))
        return CompactionArtifact(
            summaryText=text,
            structured_state=StructuredState(),
            selectedSourceTurnIds=[turn.id for turn in kept],
            warnings=warnings,
            methodMetadata={
                "control": "echo-first-n",
                "n": self.n,
                "transcriptTurns": len(transcript.turns),
            },
        )


class FullTranscriptOracleCompactor(Compactor):
    """Control: the full transcript verbatim (compaction-loses-nothing ceiling).

    Zero model calls. Mirrors the built-in oracle so the custom arms can be
    bracketed from the same adapter file.
    """

    name: ClassVar[str] = "full-transcript-oracle"
    version: ClassVar[str] = "0.1.0"

    async def compact(
        self,
        transcript: Transcript,
        config: dict[str, Any] | None = None,
        previous_artifact: CompactionArtifact | None = None,
    ) -> CompactionArtifact:
        text, warnings = fit_summary_text(_render_turns(transcript.turns))
        return CompactionArtifact(
            summaryText=text,
            structured_state=StructuredState(),
            selectedSourceTurnIds=[turn.id for turn in transcript.turns],
            warnings=warnings,
            methodMetadata={
                "control": "oracle",
                "transcriptTurns": len(transcript.turns),
            },
        )
