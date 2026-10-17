# W4 — Memory storage / retrieval / extraction audit (WAVE 1, read-only)

Scope: static code reading only. No builds, no tests executed, no network.
Base: worktree `C:/Users/openclawuser/optimus-fh-20261017`, branch `improve/full-harness-validation`, base commit c227b0ae3 (v0.1.34).
All paths below are relative to the repo root unless absolute. Line numbers are from the current worktree files.

File map note: `crates/pi-coding-agent/src/core/jev_memory_retrieval.rs` does not exist. The Jev memory-retrieval adapter is `crates/pi-coding-agent/src/core/jev_retrieval.rs` (imports `core::memory::evidence::MemoryOrigin` and `core::memory::search::{MemoryHit, MemoryScope}`); that file was audited instead.

---

## 1. CAPABILITY INVENTORY

### 1.1 Rust store (`crates/pi-coding-agent/src/core/memory/store.rs`, 2002 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests (file+name) | Missing tests |
|---|---|---|---|---|---|
| Atomic JSON write | `write_json` store.rs:359-375 | temp file + rename, mode 0o600, fsync + fsync_dir | `serde_json::to_string_pretty(value).unwrap_or_default()` (store.rs:362) would write an empty file if serialization failed — unreachable for `serde_json::Value`, but the pattern silently discards the error | exercised transitively by every store test | direct unit test of `write_json` failure path (skip: unreachable; see G-09) |
| Document validation | `validate_document` store.rs:316-352 | schema 1.0, projectId match, revision >= 0, per-entry id/kind/title/content/version checks, foreign-project entry rejection | fails closed with `"Invalid project memory snapshot"` | `fails_closed_on_corrupt_authoritative_state` store.rs:1438-1449 | no test for a valid-JSON entry with wrong `version` type only (e.g. `"version": "1"` string) — `safe_integer` returns None → rejected; untested |
| Store construction / host identity | `MemoryStore::new` store.rs:550-598 | project id regex `^project_[A-Za-z0-9_-]{1,80}$`; creates `memory/host-id.json` under a root lock when absent; validates host id is a UUID | corrupt `host-id.json` → `read_json` error → construction fails permanently (no quarantine/self-heal); cold-start lock race (`retries: 0`) → one of two racing processes fails | none for corrupt host-id; host isolation covered by `keeps_host_facts_private_and_remaps_sources` store.rs:1511-1582 | corrupt host-id.json recovery test; concurrent first-construction test |
| Read | `read` store.rs:600-606 | validate when file exists, else empty document | corrupt file → Err (fail closed) | `fails_closed_on_corrupt_authoritative_state` store.rs:1438-1449 | none missing |
| Settings resolution | `settings` store.rs:608-637 | defaults ← global `settings.json` ← project-local `settings.json` | invalid global/local settings silently ignored via `validate_settings(...).unwrap_or_default()` (store.rs:626,632) — no log/diagnostic | `reads_settings_from_the_global_and_local_files` store.rs:1953-1976 (valid files only) | test asserting the silent-fallback behaviour and (recommended) surfacing it |
| Exclusive sections | `exclusive` / `exclusive_async` store.rs:639-681 | dir lock, stale 30 s, 30 retries, 20-200 ms backoff | lock contention → Err after retries | `long_import_remains_exclusive_past_the_old_thirty_second_lease` jobs.rs:735-770 (jobs lock, same primitive) | none for the store lock itself |
| Configure | `configure` store.rs:683-701 | validate patch, merge into local `settings.json` under lock | invalid patch → Err before write | `reads_settings_from_the_global_and_local_files` store.rs:1965-1974; `validates_settings_limits_and_shared_endpoints` store.rs:1585-1703; `legacy_invalid_settings_do_not_replace_a_valid_partial_file` memory_compatibility.rs:282 | none missing |
| Backup | `backup` store.rs:703-722 | writes `backups/backup_{revision}_{hash16}.json` with sha256 of the serialized doc | write failure → Err | `backs_up_history_validates_restore_and_preserves_delivery_receipts` store.rs:1452-1480 | tampered-backup checksum test exists for the mismatch branch? No — only invalid-ID is tested; checksum mismatch untested |
| Restore | `restore` store.rs:724-759 | backup-id regex, sha256 verification, document revalidation, current revision+1, preserve delivery receipts (`events`) | checksum mismatch → Err; restore of foreign project → Err | `backs_up_history_validates_restore_and_preserves_delivery_receipts` store.rs:1452-1480; `restored_legacy_receipts_cannot_replay_even_without_saved_history` memory_source_integrity.rs:377 | restore-then-retry-of-old-event sequencing is covered; tamper test missing |
| Apply (proposal commit) | `apply` store.rs:761-999 | fingerprint (sha256 over proposal+sources+host+replaceMetadata, plus legacy alias); idempotent retry via receipt+history replay; learning gate; revision CAS; id/project/host validation; duplicate-create detection; metadata merge with sources; backup-before-write; atomic write | receipt mismatch → "Event ID reused with different content"; stale revision → "Memory changed..."; foreign host/project → Err; duplicate content under different id → "Exact duplicate: {id}" | `serializes_competing_revisions_and_deduplicates_retries` store.rs:1371-1435; `rolls_back_a_committed_refinement_and_restores_host_isolation` store.rs:1483-1508; `keeps_host_facts_private_and_remaps_sources` store.rs:1511-1582; `refuses_paused_automatic_learning` store.rs:1979-2000; memory_source_integrity.rs:185-517 (nine tests) | interleaved duplicate-create vs accepted_proposal (see SB-02); host-ownership bypass via `replace_metadata` (replace_metadata only replaces metadata, host check still runs — untested combination) |
| Rollback | `rollback` store.rs:1001-1078 | verify each applied edit's current state matches `after`, build inverse edits, apply as `rollback_{id}_{revision}` | changed entries → "Entry {} has changed since this edit"; derived event id can exceed the 160-char event-id regex (store.rs:1069 vs 810-814) | `rolls_back_a_committed_refinement_and_restores_host_isolation` store.rs:1483-1508; `t13_correction_and_rollback_controls_preserve_state` memory_scope_parity.rs:935 | long-event-id rollback test (see SB-05) |

### 1.2 Rust service (`crates/pi-coding-agent/src/core/memory/service.rs`, 2528 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Search dispatch | `MemoryService::search` service.rs:357-368 | assemble global+shared+session corpora, lexical search, drop shared-scope twins of project hits | store read failure → empty store corpus for that query (documented legacy behaviour) | search.rs tests via `service.search` (e.g. `service_cache_tracks_stemming_changes_across_project_global_and_session_corpora` search.rs:2409) | corrupt-project-store search behaviour untested (currently silently empty) |
| BM25 leg | `search_bm25` service.rs:376-381 | same corpora in tf-carrying index variant, Okapi BM25 k1=1.2 b=0.75 | inert without `recallFusion: "rrf"` (store.rs:57-59) | `bm25_leg_matches_the_lexical_entry_set_over_the_same_tokens` etc. search.rs:2621-2742 | none in this file (fusion orchestration itself is out of file map, see 5) |
| Recall render | `recall` / `render_recall` service.rs:439-446 | search + `recall_memory` | none specific | `bounds_recalled_content_and_respects_recall_controls` search.rs:1828-1870 | persona floor attach through service level (persona.rs has its own tests) |
| Prospective indexing | `index_prospective_cues` service.rs:473-622; generator `complete_prospective_cues` service.rs:240-296 | one bounded LLM call per new entry, cues stored as `metadata.prospectiveCues`; failures counted and skipped; idempotent event id `pidx_{hash24}` | max_tokens 2048 (service.rs:256): reasoning-heavy models can exhaust the budget mid-reasoning → empty content → entry stays permanently uncued (no later re-cue path) | `import_apply_with_prospective_index_stores_searchable_cues_per_new_entry` service.rs:1634-1829 (incl. retry-skip) | re-cue/recovery path for entries whose first cue call failed |
| Source spans | `attach_source_spans` service.rs:630-772; `source_span_for` service.rs:221-236 | verbatim first cited turn (<= 400 chars) into `metadata.sourceSpan`; deterministic; idempotent `span_{hash24}` | unmatched citations counted as `unmatched` | `import_apply_with_source_spans_retains_verbatim_turn_text` service.rs:1832-2034 | none missing |
| Persona supersession verifier | `verify_persona_supersession` service.rs:787-934; ordering `claim_order_key` service.rs:190-215 | per `personaKey`, newest claim by evidence timestamp (fallback created_at) wins; losers marked `supersededBy`; retained and queryable | cross-job claims only carry `created_at` (no evidence anchor in the current job) | `import_apply_persona_supersession_*` service.rs:2155-2526 (four tests incl. A→B→C chain) | ties between equal evidence timestamps (falls to created_at string compare — deterministic but untested) |
| `request` action router | service.rs:936-1294 | status/search/read/configure/bind/apply/handoff/source/backup/restore/history/rollback/import_*/share/sync/discard_pending | unknown action → Err | covered across jobs/store/service tests and memory_scope_parity.rs | `read` uses `search("", true)` (service.rs:1027) — full-corpus scan per read; no test for a large store (perf only) |
| Host handlers | `create_memory_host_handlers` service.rs:1433-1573 | per-request `MemoryService`; model registry lookup for import_run/import_apply(cues) | session-only model → instructive error | memory_scope_parity.rs exercises the host API end-to-end | none missing |
| Path resolution | `resolve_source_path` service.rs:1365-1370; `path_relative` service.rs:1379-1396 | Node win32 `path.relative` parity (case-insensitive prefix, verbatim-prefix strip) | wrong-platform behaviour | `h12_source_path_variants_resolve_to_one_project_scope` memory_scope_parity.rs:351-476 and four more | none missing |

### 1.3 Rust jobs (`crates/pi-coding-agent/src/core/memory/jobs.rs`, 1224 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Prepare | `prepare` jobs.rs:156-341 | size gate (`maxImportBytes`), deterministic job id `import_{sha24(project:source)}`, byte-for-byte `.source.jsonl` preserved atomically before parsing, JSONL parse errors with preserved-input pointer, evidence chunking under UTF-16 budget with lossless split ids `{id}:{offset}` | oversized reference → Err; invalid JSON line → Err with preserved input | `preserves_raw_code_and_reports_coverage` jobs.rs:773-805; `import_chunks_respect_utf16_budget_and_preserve_all_text_and_offsets` jobs.rs:689-716; `rejects_invalid_session_json_and_oversized_files` jobs.rs:808-825; `import_rejects_reference_that_leaves_no_unicode_character_room` jobs.rs:719-732 | concurrent `prepare` of the same source from two processes (idempotent return of existing job) |
| Run (extraction) | `run` jobs.rs:343-385; `run_inner` jobs.rs:387-430 | jobs-dir lock (stale 30 s, 0 retries), resume from `next_chunk`, `maxImportChunksPerRun` per invocation, abort signal, job state written after every chunk | extractor error → Failed status written; **Failed-status write errors are discarded** (`let _ = write_json(self.path(id).unwrap_or_default(), ...)` jobs.rs:378-381) → on-disk job stays Running while run() returns Err (restart re-enters run_inner safely) | `resumes_failed_chunks_and_requires_a_completed_preview` jobs.rs:828-862; `limits_extraction_calls_per_invocation` jobs.rs:865-914; `reports_aborted_extraction` jobs.rs:1034-1044; `long_import_remains_exclusive_past_the_old_thirty_second_lease` jobs.rs:735-770 | none critical |
| Apply | `apply` jobs.rs:432-594 | requires Preview; builds accepted proposal once: creates only, content-hash dedupe, repeated id across chunks keeps the LAST extraction, model `projectId` metadata stripped, invalid ids re-derived, existing-id and existing-content skip filters; persists accepted proposal BEFORE `store.apply`; store.apply with event id = job id | poisoned accepted_proposal after a failed first apply (see SB-02); "Imports only create memories" on non-create edits | `applies_only_creates_and_skips_existing_content` jobs.rs:917-937; `repeated_create_ids_across_chunks_keep_the_last_extraction` jobs.rs:940-1005; `rejects_imports_that_are_not_memory_creates` jobs.rs:1008-1031 | interleaved two-job same-content apply (SB-02 repro test) |

### 1.4 Rust extraction (`crates/pi-coding-agent/src/core/memory/extraction.rs`, 660 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Stop-reason mapping | `to_refinement_assistant` extraction.rs:16-53 | length/toolUse/error/aborted → local StopReason | unknown strings map to Stop (lenient) | none in this file (upstream refinement tests out of file map) | unit test of the mapping table |
| Completion plumbing | `completion_fn_for` extraction.rs:59-127 | provider retry policy wrapper, least-thinking request, TODO at extraction.rs:98-107: not routed through gateway recovery (documented integration gap) | local-gateway death surfaces as a plain completion error | none | gateway-recovery routing test (after the integration lands) |
| Extract | `extract` extraction.rs:161-228 | evidence = records, `max_output_tokens = maxExtractionTokens` (extraction.rs:190), usage accounting, `select_import_edits` normalization with audited rationale | plan_refinement error → Err (job Failed) | exercised via jobs/service tests with stub extractors | none in this file |
| Import edit selection | `select_import_edits` extraction.rs:254-301 | cited update→create conversion with derived id + `updateTargetId`; uncited/delete/foreign-kind dropped and counted; persona typing sanitized | none known | nine tests extraction.rs:404-658 | none missing |
| Persona typing sanitizer | `sanitize_persona_typing` extraction.rs:321-377 | trim/bound personaKey, whitelist claimType, dedupe+bound evidenceIds; fail-open (drop field, keep edit) | none known | `valid_persona_typing_is_preserved_through_normalization`, `malformed_persona_typing_fields_are_dropped_not_edits` extraction.rs:535-626 | none missing |

### 1.5 Rust evidence (`crates/pi-coding-agent/src/core/memory/evidence.rs`, 613 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Message → evidence | `message_evidence` evidence.rs:210-300 | user/assistant/toolResult/bashExecution → evidence with sha256, URI, entry id or derived `msg_{hash24}`; custom/derived/unknown excluded (evidence.rs:255, jobs.rs:237-247); tool results containing "[memory data; not new evidence]" demoted to Derived (evidence.rs:258-260) | none known | `excludes_custom_messages_and_preserves_real_origins` evidence.rs:381-401; `bash_execution_respects_exclusion_and_prints_undefined_exit_code` evidence.rs:404-422; `tool_call_blocks_keep_arguments_and_derived_ids_are_stable` evidence.rs:425-440 | none missing |
| Timestamp anchoring | `timestamp_datetime` evidence.rs:198-208 | finite, non-zero, in-i64-range ms → anchor; absent/zero/nonfinite/out-of-range → None | none known | five `temporal_*` tests evidence.rs:443-597 | none missing |
| Evidence window | `evidence_window` evidence.rs:334-365 | newest-first budget, explicit `[truncated; inspect source]` marker | truncation is marked, not silent | `temporal_evidence_labels_still_respect_utf16_window_budget` evidence.rs:600-611 | none missing |

### 1.6 Rust search (`crates/pi-coding-agent/src/core/memory/search.rs`, 2744 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Tokenization | `words` search.rs:78-104 (dedup, >=2 chars); `words_all` search.rs:110-130 (frequency); `stem` search.rs:138-189 (suffix-stripping) | deterministic lowercase tokens; stem floor 3 chars, doubled-consonant collapse | none known | `tokenizes_words_like_the_typescript_regex` search.rs:1730-1734; `stem_reduces_inflections_to_a_shared_form` search.rs:1650-1669 | none missing |
| Corpus index build | `CorpusIndex::build` search.rs:464-645 | drops foreign project/host entries (search.rs:476-487); host entries → Host scope; superseded/detached inactive but counted in df_all; title(3)/body(1, incl. cues)/span(0.5) term sets; BM25 tf variant | foreign entries with absent projectId are visible by design (global harness) | `foreign_entries_cannot_change_allowed_scores_or_order` search.rs:1227-1296; `eligible_metadata_preserves_legacy_scope_rules` search.rs:1534-1567 | none missing |
| Process-wide cache | `cached_file_corpus_index` search.rs:711-782; key search.rs:655-668 | `(path, mtime_ns, len, scope, project, host, stemming, bm25)` LRU (max 16, search.rs:684); load failure → uncached empty index; missing/untrustworthy mtime → always reload | same-(mtime,len) rewrite collision would serve a stale index — practically excluded by fsync + temp/rename (100 ns NTFS granularity) | `cached_search_matches_reference_scores_bits_order_and_cache_reuse` search.rs:2315-2406; `cache_invalidates_when_the_store_is_written_through_the_real_api` search.rs:2506-2564; `concurrent_searches_on_the_shared_cache_stay_correct` search.rs:2567-2598 | none missing (mtime-collision is theoretical) |
| Lexical search | `search_corpora` search.rs:817-926 | idf = ln((N+1)/(df+1))+1 over eligible entries; field weights 3/1/0.5; score = weighted-idf sum / idf denominator; sort: score desc → created_at desc (newest first) → scope order (session>project>host>shared>global) → id asc | empty query matches everything at score 0.0 (search.rs:894) | `eligible_corpus_preserves_idf_and_field_weights` search.rs:1499-1531; `unicode_matches_and_tie_breaks_are_stable` search.rs:1570-1647; `equal_scores_rank_the_newest_entry_first` search.rs:1705-1727 | mixed Rust("…Z")/Python("…+00:00") created_at tie ordering is deterministic but untested (same-instant entries only) |
| BM25 leg | `bm25_search_corpora` search.rs:962-1080 | same corpus statistics, tf saturation, length normalization, avgdl guard | none known | five `bm25_*` tests search.rs:2621-2742 | none missing |
| Recall render | `recall_memory` search.rs:1088-1169 | header + newest-first entries under `maxRecallChars`/`maxRecallEntries`; Missing/Stale freshness skipped; content truncated with `[read entry for full text]` marker; source span rendered capped at 200 chars **without a marker** (search.rs:1136) | budget arithmetic reserves 32 chars slack | `bounds_recalled_content_and_respects_recall_controls` search.rs:1828-1870; `recall_renders_the_source_span_after_content_capped_at_200_chars` search.rs:1454-1496 | span-truncation marker (see SB-04) |
| Freshness | `freshness` search.rs:224-277 | file sources: hash compare → Current/Stale/Missing/Unknown; project-path escape → Unknown | none known | `detects_changed_source_files_and_skips_missing_ones` search.rs:1914-1957 | none missing |

### 1.7 Persona layer (`crates/pi-coding-agent/src/core/memory/persona.rs`, 995 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Claim parsing | `parse_claim` persona.rs:99-154 | personaKey marker, claimType whitelist, content collapse, observed_at fallback created_at; malformed → skip (fail-safe) | none known | `regeneration_skips_malformed_entries_and_removes_stale_sidecars` persona.rs:927-993 | none missing |
| Composition | `compose` persona.rs:244-374 | per-key newest current claim; stated → inferred → facts → evolution priority under clamped budget (512-4000); `(+N more claims not shown)` marker; deterministic bytes | evolution lines hard-capped at 12 with no marker (persona.rs:58, 317-321) | `compose_is_deterministic_and_within_budget` persona.rs:658-683; `compose_respects_budget_priority_and_marks_overflow` persona.rs:686-703; three more | >12 evolution lines marker test |
| Sidecar lifecycle | `regenerate` persona.rs:432-482; `persona_floor` persona.rs:528-557 | advance-on-success; no-eligible-claims removes stale sidecar; corrupt sidecar → recovery regeneration; lock contention fails soft | sync lock with 0 retries (persona.rs:436-444) — regeneration skipped under contention (floor retries next turn) | `cold_start_and_recovery_regenerate_the_floor` persona.rs:903-924; `regeneration_writes_sidecar_and_composes_deterministically` persona.rs:810-834; `trigger_regenerates_only_on_persona_claim_events` persona.rs:837-900 | none missing |

### 1.8 Jev memory relevance (`crates/pi-coding-agent/src/core/jev_retrieval.rs`, 743 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Eligibility | `memory_eligible` jev_retrieval.rs:184-194 | non-Session scope, kind memory, non-user/manual source, not pinned, has sources, no User-origin source | none known | `pinned_session_user_instruction_unknown_and_duplicate_memories_stay` jev_retrieval.rs:618-643 | none missing |
| Prepare/apply | `prepare_memory` / `apply_memory` jev_retrieval.rs:196-226 | duplicate hit id → ALL candidates dropped (conservative keep-everything, jev_retrieval.rs:199-202); MAX_FILTER_CANDIDATES cap; excerpts redacted/bounded | invalid filter options → 0 candidates (fail-open, jev_retrieval.rs:66-72) | `memory_filter_removes_only_retrieved_optional_entries_and_is_reversible` jev_retrieval.rs:581-616; `candidate_caps_keep_unscored_entries_and_reject_invalid_config` jev_retrieval.rs:673-691; `native_recall_renderer_filters_injection_without_mutating_store` jev_retrieval.rs:644-672 | none missing |
| E2E Jev wiring | tests/jev_memory_retrieval.rs (2 tests) | real session + real bridge; Jev modes × feature × transport × mid-flight change | malformed control transport → recall still injected (fail-open) | `real_context_combines_recall_without_displacing_baseline_and_fails_open` jev_memory_retrieval.rs:145-257 | none missing |

### 1.9 Python harness state (`prime-agent-runtime/src/rlm/harness.py`, 1173 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Load | `load` harness.py:388-483 | classify missing/corrupt/unreadable; **malformed entries silently dropped** (harness.py:436-439) and the drop is persisted by the next save | silent data loss on partially-malformed state (SB-01) | none in this file (no test module found in the audited set) | everything — the file has no adjacent tests; see TEST PLAN |
| Save | `save`/`_save_locked` harness.py:525-598 | cross-language lock file, fail-closed on unreadable, content-hash CAS vs loaded generation, corrupt-state quarantine (content-addressed, harness.py:600-633), atomic temp+fsync+rename with Windows retry, mode preservation | lock timeout 1 s (harness.py:41) vs Rust up to ~6 s wait → Python mutations can raise under a long Rust write | none | lock-contention behaviour test |
| CRUD + wrappers | `upsert`/`create`/`update`/`delete`/`create_memory` etc. harness.py:635-1017 | sync-from-disk before mutation; global delegation; create-or-fail vs update-or-fail | `upsert` always overwrites `source` with the caller's default "agent" (harness.py:719) | none | unit tests for the CRUD contract (ids, scope prefix, skill reference validation) |
| Cache | `get_harness_state` harness.py:1137-1161 | per-(path,scope) process cache; reload-on-use via mtime+generation | none known (reload happens per operation) | none | none critical |

### 1.10 Bench / controller infrastructure

| Asset | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| `crates/pi-coding-agent/examples/memory_bench.rs` (7214 lines; header + settings-contract scan only per file map) | `cargo run --example memory_bench <manifest>` | drives the REAL memory system; per-env scratch dirs; settings checked against effective values (`memory_feature_realization`, mb:1402-1515); supports recallFusion/recallBm25/personaLayer (mb:1432-1438) | requested-vs-effective mismatch fails closed (mb:4461) | large in-file `#[cfg(test)]` module (mb:~4399-5943): settings application, realization checks, out-of-range rejection | (not fully read per file map — see 5) |
| `crates/pi-coding-agent/examples/mem_search_bench.rs` (551 lines) | `mem_search_bench <out_root> [N...]` | LLM-free lexical search latency benchmark; public-field store construction (msb:347-363) | compile-time coupling to `MemoryStore` field layout | none (bench) | none |
| `scripts/memory_question_loop.py` (1153 lines) | one ledger question end-to-end | type-A (copy frozen store) / type-B (content-addressed ingest cache) attempts; question-scoped scoring; atomic ledger appends under a sidecar lock | **cannot drive the newest memory settings**: INGEST_AFFECTING_KEYS/READ_AFFECTING_KEYS (mql:161-173) omit recallFusion, recallBm25, personaSupersession, personaLayer, personaSnapshotChars; `merge_settings` raises "unknown manifest settings keys" (mql:590-597); type-B cache key (mql:526-540) also excludes them | none (controller script) | see SB-08 |
| `bench/` (Python) | `bench/eval/evaluate.py`, converters, judges | official evaluators (locomo/longmemeval/pmem), Porter stemmer local scoring | out of file-map scope (listing only) | `bench/tests/test_local_scoring.py`, `bench/tests/test_pmem_converter.py` | not audited (listing only) |

---

## 2. RISK-RANKED GAPS

No P0 found in the audited files. The Rust write path is disciplined: atomic temp+rename+fsync writes (store.rs:359-375), backup before every apply (store.rs:991), fail-closed corrupt handling (store.rs:316-352), and receipt-based idempotency (store.rs:787-803).

### P1

- **G-01 (test infrastructure): the question-loop controller cannot exercise the newest memory features.** `scripts/memory_question_loop.py:161-173` defines the settings whitelist; `recallFusion`, `recallBm25`, `personaSupersession`, `personaLayer`, `personaSnapshotChars` are absent from both INGEST_AFFECTING_KEYS and READ_AFFECTING_KEYS, so `merge_settings` (mql:586-598) rejects any manifest that sets them, and the type-B cache key (mql:526-540) cannot distinguish runs that differ only in those settings. The driver itself supports them (`memory_bench.rs:1432-1438`). Consequence: wave-2 e2e validation of RRF fusion, the BM25 leg, and the persona layer cannot be driven through the official controller; this also re-creates the known settings-parity hazard (global memory `benchmark-runner-settings-parity-with-smoke`: omitted keys silently hit driver defaults).
- **G-02 (untested primary writer): `prime-agent-runtime/src/rlm/harness.py` has no test coverage in the audited tree.** It is the kernel-side writer of the session/global harness states that the Rust recall corpora read (service.rs:393-435). Its lock, CAS, quarantine, and load-classification logic (harness.py:46-130, 323-598) is the most safety-critical Python in the memory path and is pinned by nothing.

### P2

- **G-03: Python harness load silently drops malformed entries and the next save persists the drop** (SB-01, harness.py:436-439 + 557-565). Asymmetric with the Rust store, which fails closed (store.rs:316-352) and with harness.py's own corrupt-file quarantine (harness.py:600-633).
- **G-04: import apply can be permanently poisoned by a stale accepted proposal** (SB-02, jobs.rs:452-573 + store.rs:885-910).
- **G-05: recall diagnostics are invisible when the project store is corrupt.** `cached_store_corpus_index` (search.rs:802-812) maps a read failure to an empty corpus per query, matching legacy behaviour, but nothing surfaces the corruption to the recall path — the agent silently recalls nothing. Corrupt handling exists and is tested for `read()` (store.rs:1438-1449) but not for the search path.
- **G-06: prospective-cue generation has no recovery path.** A truncated/failed cue call (service.rs:240-296, max_tokens 2048 at service.rs:256) leaves the entry permanently uncued: retries of the same import skip already-applied jobs (jobs.rs:444-446), and the skip filter only ignores entries that already HAVE cues (service.rs:521-528). The 512→2048 raise is documented (service.rs:249-255) but the residual failure mode has no repair.

### P3

- **G-07: corrupt `host-id.json` bricks `MemoryStore::new`** with no quarantine/self-heal (store.rs:562-590). Fail-closed but requires manual file surgery; every `create_memory_host_handlers` request constructs a new service (service.rs:1451-1453), so all memory requests fail until the file is fixed.
- **G-08: `settings()` silently ignores invalid settings files** (store.rs:624-634, `unwrap_or_default()` on both global and local validation). No log or diagnostic; a hand-edited typo silently reverts that key to default.
- **G-09: failure-path writes discard errors.** jobs.rs:378-381 (`let _ = write_json(self.path(id).unwrap_or_default(), ...)`) hides IO errors writing the Failed status (job stays Running on disk), and the `unwrap_or_default()` can target an empty path. store.rs:362 (`to_string_pretty(...).unwrap_or_default()`) would silently write an empty file on a serialization failure (unreachable for `Value`).
- **G-10: recall renders only the first 200 chars of a stored 400-char source span with no truncation marker** (search.rs:1136 vs service.rs:39/SOURCE_SPAN_MAX_CHARS=400).
- **G-11: rollback event-id overflow for long refinement ids** (SB-05, store.rs:1069 vs 810-814).
- **G-12: persona evolution lines hard-capped at 12 with no marker** (persona.rs:58, 317-321).
- **G-13: cold-start lock race on `host-id.json` creation** (`retries: 0` at store.rs:564-572): two processes constructing the first service on a fresh agent dir — one fails its construction.

### P4

- **G-14: Python/Rust lock-wait asymmetry.** Python `_STATE_LOCK_TIMEOUT_SECONDS = 1.0` (harness.py:41) vs Rust lock acquisition 30 retries at 20-200 ms (store.rs:647-654). A long Rust write can make concurrent Python mutations raise.
- **G-15: corpus cache mtime-collision window is theoretical but unpinned** (search.rs:647-668). Requires two same-length writes inside one 100 ns window with fsync — practically excluded; no test pins the assumption.
- **G-16: `search`/`read` scan the full corpus per call.** `read` uses `search("", true)` (service.rs:1027) and `settings()` re-reads both settings files per query (service.rs:364, 392); perf only, measured by mem_search_bench.
- **G-17: `upsert` clobbers `source` with the default** (harness.py:719) — provenance metadata loss for callers that omit `source`.

---

## 3. SUSPECTED BUGS

**SB-01 — Python harness load drops malformed entries and persists the drop.**
- Where: `prime-agent-runtime/src/rlm/harness.py:431-439` (drop), `harness.py:557-565` (`_save_locked` writes the filtered view), `harness.py:550-554` (generation CAS only blocks external changes, not our own load-time drop).
- What looks wrong: an entry whose `title` or `content` is not a string (e.g. a partially-written or hand-edited state, or output of a buggy writer) is `continue`d without any counter, log, or quarantine. The subsequent `save()` serializes the reduced entries map, permanently deleting the entry. This contradicts the file's own corrupt-state discipline (quarantine at harness.py:600-633) and the Rust store's fail-closed `validate_document` (store.rs:316-352).
- Reproduce as a test (Python, unit): write a `harness_state.json` with two entries, one valid and one with `"content": 123`; construct `HarnessState(file_path=...)`; call `state.create_memory("t", "c")`; assert the malformed entry is either still on disk or the operation raised — current behaviour: it is gone.

**SB-02 — import apply poisoned by a persisted accepted proposal.**
- Where: `crates/pi-coding-agent/src/core/memory/jobs.rs:452` (`if job.accepted_proposal.is_none()` — build-once), `jobs.rs:568-572` (persist before apply), `jobs.rs:538-558` (existing-id/content skip filters run only inside the build branch), `crates/pi-coding-agent/src/core/memory/store.rs:885-910` (duplicate-create rejection).
- What looks wrong: the dedupe filters that make imports idempotent (skip creates whose id exists or whose content matches a current entry) run only when the accepted proposal is first built. If the first `store.apply` fails after the proposal was persisted (e.g. revision mismatch because a write-time pass or another import bumped the revision) and a competing import then creates the same content under a different id, every retry reuses the stale accepted proposal and `store.apply` fails forever with `Exact duplicate: <other id>`. The job is stuck in Preview with no automatic recovery.
- Reproduce as a test (Rust, integration, jobs.rs tests): prepare+run import A and import B whose extractors emit the same content under different ids; call `jobs.apply(A, rev)`; attempt `jobs.apply(B, rev)` with a deliberately stale revision so it fails after persisting `accepted_proposal`; apply A; retry `jobs.apply(B, rev+1)`. Current: permanent `Exact duplicate` error. Expected: B applies as a no-op skip (matching the build-time filter semantics) or rebuilds the accepted proposal.

**SB-03 — corrupt `host-id.json` permanently breaks store construction.**
- Where: `crates/pi-coding-agent/src/core/memory/store.rs:562-590`. The create path runs only when the file is absent (store.rs:563); a present-but-corrupt file makes `read_json` (store.rs:580) fail and `MemoryStore::new` returns Err for every subsequent call.
- What looks wrong: no quarantine or re-mint, unlike `harness.py`'s corrupt-state handling. Every memory request builds a fresh `MemoryService` (service.rs:1451-1456), so the failure is total and permanent until manual intervention.
- Reproduce as a test (Rust, unit): create an agent dir, write garbage into `memory/host-id.json`, call `MemoryService::new(...)`; current: Err (serde error). Decide desired semantics (quarantine + re-mint, or a distinct actionable error) and pin it.

**SB-04 — recall source-span rendering truncates silently at 200 chars.**
- Where: `crates/pi-coding-agent/src/core/memory/search.rs:1136` (`.chars().take(200)`), stored span cap 400 at `service.rs:39` and `service.rs:232`.
- What looks wrong: the rendered `Source:` line shows at most 200 chars of a span that was deliberately retained up to 400 chars, with no `…`/marker (contrast `truncate` in persona.rs:392-399 and `[read entry for full text]` at search.rs:1150).
- Reproduce as a test (Rust, unit): entry with a 400-char sourceSpan; `recall_memory`; assert the marker exists after the fix, or assert the current 200-char cut deliberately if intended.

**SB-05 — rollback impossible for near-max-length event ids.**
- Where: `crates/pi-coding-agent/src/core/memory/store.rs:1069` (`event_id: format!("rollback_{id}_{expected_revision}")`) vs the event-id regex `^[A-Za-z0-9_-]{1,160}$` at store.rs:810-814.
- What looks wrong: for a committed refinement id of length >= ~149 the derived rollback event id exceeds 160 chars, so `apply` rejects it with `Invalid event ID` and the refinement can never be rolled back. Generated ids (refine_24/import_31/handoff_32/pidx_29) are safe; only caller-supplied long ids are affected.
- Reproduce as a test (Rust, unit): apply with a 150-char event id; call `rollback(&result.id, rev)`; current: `Invalid event ID`.

**SB-06 — jobs.rs failure-status write discards errors and can target an empty path.**
- Where: `crates/pi-coding-agent/src/core/memory/jobs.rs:378-381`.
- What looks wrong: `self.path(id).unwrap_or_default()` yields `""` when the id is invalid (unreachable after the preceding `get`, but the pattern is unsound), and `let _ = write_json(...)` discards real IO errors — the job file stays `Running` after a failed run, though restart recovery re-enters `run_inner` safely (jobs.rs:365-370 only short-circuits Applied/Preview).
- Reproduce as a test (Rust, unit): make the jobs dir read-only, run an extractor that fails; assert the returned error and the on-disk status; then remove the read-only bit and assert a subsequent `run` recovers.

**SB-07 — controller rejects the newest memory settings (blocks wave-2 RRF/persona e2e).**
- Where: `scripts/memory_question_loop.py:161-173` (whitelists), `mql:586-598` (`merge_settings` raises `unknown manifest settings keys`), `mql:526-540` (type-B cache key).
- What looks wrong: `recallFusion`, `recallBm25`, `personaSupersession`, `personaLayer`, `personaSnapshotChars` are supported by the driver (memory_bench.rs:1432-1438) and by the store (store.rs:148-177) but cannot pass the controller. Additionally `personaSupersession`/`personaLayer` are write-time passes executed at `import_apply` (service.rs:1242-1262), which warm-cache reuse (skipIngest) skips entirely — so even accepting the keys would need cache-key or mode semantics work.
- Reproduce as a test (Python, unit): `merge_settings(base, {"recallFusion": "rrf"})` currently raises; after the fix, assert the key reaches the manifest and `settings_changed_keys` classifies it read-time, and `personaSupersession` is classified write-time (type B).

**SB-08 — silent settings fallback on read.**
- Where: `crates/pi-coding-agent/src/core/memory/store.rs:624-634`.
- What looks wrong: `validate_settings(&global_memory).unwrap_or_default()` and the local equivalent make any invalid settings file (bad types, out-of-range values) silently revert affected keys to defaults, with no diagnostic. The write path validates (store.rs:684), so this only bites hand-edited files — exactly the situation where silent behaviour is worst.
- Reproduce as a test (Rust, unit): write `{"memory": {"maxRecallEntries": 9999}}` to the global settings file; `store.settings().max_recall_entries` is 6 (default) with no error or log; pin the behaviour or surface it.

---

## 4. TEST PLAN (wave 2)

Tier: u = unit (in-file `#[cfg(test)]` or Python unit), i = integration (`crates/pi-coding-agent/tests/`), e = e2e (bench/controller).

Rust store / jobs:
1. `harness_state_writer_drops_no_entries_or_fails_closed` — u — `prime-agent-runtime` (new test module beside harness.py or a runtime test dir) — asserts SB-01: malformed entry survives a load+save cycle or the save raises; also pins quarantine-idempotence for a corrupt whole file. Tier u/i.
2. `import_apply_poisoned_proposal_recovers` — i — target `crates/pi-coding-agent/src/core/memory/jobs.rs` (test in-file) — SB-02 repro sequence; asserts the second import terminates applied-with-skip or rebuilds, never a permanent `Exact duplicate`.
3. `corrupt_host_id_quarantined_and_reminted` — u — store.rs — SB-03: garbage host-id.json → new UUID minted (or distinct actionable error); asserts subsequent `MemoryService::new` succeeds.
4. `rollback_of_max_length_event_id_succeeds` — u — store.rs — SB-05: 150-char event id commits and rolls back.
5. `backup_checksum_tamper_is_rejected` — u — store.rs — modify one byte of a backup's `state` after creation; `restore` must return `Backup checksum mismatch` (branch currently untested).
6. `failed_run_status_write_error_is_visible` — u — jobs.rs — SB-06: read-only jobs dir; failed run returns the extractor error AND the job file state is recoverable on the next `run`.
7. `settings_invalid_files_surface_or_default_doc` — u — store.rs — SB-08: pin the silent-default behaviour (and, if changed, the surfaced diagnostic).

Rust search / recall:
8. `corrupt_store_search_reports_empty_with_diagnostic` — i — search.rs/service.rs — G-05: corrupt `harness_state.json`; `MemoryService::search` returns empty (pin current behaviour) and a diagnostic is emitted or returned (new).
9. `recall_source_span_render_marks_truncation` — u — search.rs — SB-04: 400-char span → rendered line carries a truncation marker.
10. `empty_query_recall_renders_only_newest_entries` — i — service.rs — `recall("")` returns exactly `maxRecallEntries` newest entries (documents the score-0 semantics of search.rs:894 for the recall path).
11. `mixed_format_created_at_tie_break_is_deterministic` — u — search.rs — entries with Rust `…Z` and Python `…+00:00` created_at; assert a stable, documented order.
12. `persona_evolution_cap_is_marked` — u — persona.rs — G-12: >12 evolution lines → marker line present.

Extraction / cues:
13. `prospective_cue_failure_has_a_retry_path` — i — service.rs — G-06: first cue generation fails; assert a documented re-cue path (e.g. re-import trigger, explicit re-cue action, or a bounded later pass) or pin the current permanent-skip explicitly with the failure count asserted in the report.
14. `to_refinement_assistant_maps_all_stop_reasons` — u — extraction.rs — table test for length/toolUse/error/aborted/unknown.

Python harness:
15. CRUD/lock/CAS/quarantine suite — u/i — `prime-agent-runtime` — pin `create`/`update`/`delete` semantics, `global:` id routing, skill-reference validation, corrupt-state quarantine idempotence, unreadable-state fail-closed save, and the 1 s lock-timeout behaviour (G-14). This is the highest-value new test module (G-02).

Controller / bench:
16. `controller_accepts_new_memory_settings` — u (Python) — SB-07: `merge_settings` accepts recallFusion/recallBm25/persona*; `settings_changed_keys` classifies personaSupersession/personaLayer/personaSnapshotChars as write-time and fusion/bm25 as read-time; type-B cache key includes the persona keys.
17. `rrf_fusion_and_rerank_unit_tests` — u — target `crates/pi-coding-agent/src/core/extensions/builtin/memory.rs` (recall orchestration; outside this audit's file map — verify location in wave 2) — RRF k=60 0-based fusion math, max-merge off-mode byte-identity, rerank pool width and content-excerpt bounds, BM25-leg gate `recall_bm25_leg_active` end-to-end through the extension.
18. `cold_index_latency_budget` — e — mem_search_bench with a latency assertion for the first query after a store write (cold cache) at N=1000/20000 — currently the bench reports but asserts nothing.

---

## 5. COVERAGE NOTES (NOT INSPECTED / NOT EXECUTED)

- NOT EXECUTED (campaign constraint): no `cargo build/check/test`, no benchmarks, no model calls. All findings are static reading; runtime behaviour claims cite tests that exist, not runs I performed.
- `crates/pi-coding-agent/src/core/jev_memory_retrieval.rs` (file-map name) does not exist; `jev_retrieval.rs` was audited as the closest match (it is the only Jev adapter importing `core::memory::search::{MemoryHit, MemoryScope}`). UNCERTAIN whether the parent intended a different file.
- Out of file map, referenced but NOT read (bounds respected):
  - `crates/pi-coding-agent/src/core/memory/mod.rs` (302 lines) — `acquire_lock`/`acquire_lock_sync`/`MemoryLockRetries` implementation; lock semantics (lease, staleness, the "remains exclusive past 30 s" behaviour asserted by jobs.rs:735-770) are taken from call sites and tests, not the implementation. UNCERTAIN.
  - `crates/pi-coding-agent/src/core/memory/{project.rs, sharing.rs, server.rs}` — project identity/aliasing, the sharing client and pending queue, and any memory server. `dedupe_shared_copies` (service.rs:452-465) was analysed; the shared corpus contents and sync protocol were not.
  - `crates/pi-coding-agent/src/utils/atomic_file.rs` (752 lines) — the atomic writer itself (Windows rename retry list mirrored by harness.py:102-130). Taken on trust from its callers.
  - `crates/pi-coding-agent/src/core/refinement/refinement.rs` — `apply_refinement_proposal`, `plan_refinement`, `normalize_refinement_proposal`, harness-state load/save, and the extraction retry / token-budget escalation logic. The FOCUS items "stop-reason detection, bounded retry, token-budget escalation" live there for the extraction path; only the extraction.rs mapping and plumbing were audited.
  - `crates/pi-coding-agent/src/core/extensions/builtin/memory.rs` (3974 lines) — the recall ORCHESTRATION: query distillation, angle-query expansion, rerank, RRF fusion, persona floor injection, recall diagnostics. `RecallFusion`/`recall_bm25_leg_active` (store.rs:26-59) and the two search legs (search.rs:817-1080) are audited; the fusion call site is not. This is the single largest unaudited surface for the retrieval FOCUS.
  - `crates/pi-coding-agent/examples/memory_bench.rs` — header (mb:1-120) plus a bounded settings-contract scan (lines matching the settings keys) per the "headers" instruction; the driver's question loop, ingest loop, and in-file test module were not fully read.
  - `bench/` — directory listing only (evaluators, judges, converters).
  - Python-side tests for harness.py — none were found inside the audited set; `prime-agent-runtime` test layout was not explored beyond the file map.
- `memory_bench.rs` in-file tests were enumerated from the settings scan only; their full coverage was not assessed.
- Windows-specific behaviours (NTFS 100 ns mtime granularity, `LockFileEx` interop with `msvcrt.locking`, `os.replace` sharing violations) are reasoned from code comments and the mirrored retry lists, not measured.

Report generated by W4 (memory audit), full-harness validation campaign, wave 1.
