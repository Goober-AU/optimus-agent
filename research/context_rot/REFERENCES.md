# Context-rot / compaction-fidelity references: notes from actual code

Code-level survey for the Optimus context-rot campaign. Every claim comes from a file opened in the pinned clone;
paths are repo-relative. Clone root `C:/Users/openclawuser/optimus-context-rot-refs/`, `git clone --depth 1` then `git
rev-parse HEAD`. All 13 targets exist; none NOT FOUND.

| repo | commit | licence | files read |
|---|---|---|---|
| chroma-core/context-rot | `af80a08018f2b7257c0336d04bcd02f936088106` | MIT (Chroma) | 17, all three experiments |
| youtal321/nolima | `cb14780b249fecf2851127b2101a062c1b2c6430` | Adobe Research License, non-commercial | evaluation/ + data/ |
| NVIDIA/RULER | `c3f5e3b4f87f97e048793bb510a3a6b19a46bf3a` (main) | Apache-2.0 | all generators + scoring |
| compactbench/compactbench | `b2bb0e2254010e7842801b9101e56fd9a6542615` | Apache-2.0 | contracts, scoring, runner, 2 compactors |
| microsoft/acon | `d63f9ae18959dc7215ff62899c94c5e8c56847ae` | MIT | `src/productive_agents/ctxopt/` |
| microsoft/LLMLingua | `5a4c78ae18ab17a98cf997e8259354e546081d64` | MIT | `prompt_compressor.py` |
| THUDM/LongBench | `2e00731f8d0bff23dc4325161044d0ed8af94c1e` | MIT | v2 `pred.py`/`result.py`, v1 `metrics.py` |
| evalplus/repoqa | `ae876deb1365dbf5a15b0533723c8ed123eee586` | Apache-2.0 | context build + scoring |
| Aider-AI/aider | `5dc9490bb35f9729ef2c95d00a19ccd30c26339c` | Apache-2.0 | `aider/repomap.py` complete |
| SWE-agent/SWE-agent | `3ea751c087f32b16e039a2233dd6eefecef325d5` | MIT | history processors, agent, models, patch formatter |
| letta-ai/letta-code | `b6d8b51564c7e045ed2f22f1bb6655b68256be32` | Apache-2.0 | `memory*.ts`, `backend/local/compaction.ts` |
| xiaowu0162/LongMemEval-V2 | `2cc8c540bdb87fe6761629b585e727e1c4704520` | Apache-2.0 | harness, metrics, RAG, LAFS |
| nelson-liu/lost-in-the-middle | `29b8a6d042ce29abccee3db1a73171a107d7e6af` | MIT | prompting, metrics, data builder |

## 1. chroma-core/context-rot (primary benchmark)

Haystacks (`experiments/niah_extension/run/create_haystacks.py`). `build_haystack_sequential` cycles corpus documents
and decodes `tokens[:remaining]` on the last, so the haystack hits the token target exactly; `insert_needle_at_depth`
takes `int(len(tokens)*depth/100)` then walks back until `tokens[i-1] == encode('.')[0]`, so the needle starts a
sentence (depth 0/100 prepend/append). `insert_distractors_randomly` splits on `'. '` and inserts at a random index in
`[1, n-1]`. Grid: `input_lengths = [500, 1k, 5k, 10k, 50k, 100k, 500k, 900k]` x `depths 0..100 step 10`; overhead is
the template tokenised with `SAMPLE_CONTEXT` removed, `available = length - overhead - needle_tokens`, cells with
`available <= 100` skipped; `tiktoken.get_encoding("o200k_base")` at every length. The prompt fences
`<document_content>...<question>...` and pre-fills `Assistant: Here is the most relevant information in the
documents:`.

Harness (`experiments/models/base_provider.py`, `llm_judge.py`, `providers/openai.py`). `create_batches` is greedy
first-fit on the stored `token_count` against `max_tokens_per_minute`; `process_batch` uses
`ThreadPoolExecutor(max_workers=len(batch))` with per-future `timeout=500`, writes CSV after each batch, and encodes
failures as `ERROR_TIMEOUT:`/`FUTURE_ERROR:`; `main` filters by `max_context_length` then resumes by reprocessing NaN
or `ERROR` rows, 60 s between batches. Judging is a binary LLM call capped at 100 output tokens, and
`LLMJudge.evaluate` forces `token_count=100` per row so the rate packer treats it as tiny work; rules are typo or
partial => false, answer plus preamble => true. `analyze_distractors.py` re-judges only `llm_judge_output == False`
rows with a "which distractor, else -1, hesitation or multi-mention => -1" question; `visualize.py` pivots mean
accuracy over (`approximate_input_length`, `needle_depth`).

Repeated words (`experiments/repeated_words/*`): `create_variations` places `modified` at position `i` among
`num_words` copies of `common`, positions sampled with `step = num_words // 100`, lengths `[25 .. 10000]`; prompt
`"Simply replicate the following text, output the exact same text: ..."`; `max_output_tokens = 2*input_tokens` clamped
by the model cap. Scoring: `normalized_levenshtein_score = 1 - distance/max(len)`; `modified_word_present` guards the
substring with a space so `apples` cannot match inside `applesauce`; `check_correct_position` compares
`gold.index(unique)` with `output.index(unique)`.

LongMemEval (`experiments/longmemeval/*`): `focused_prompt` (relevant turns only) versus `full_prompt` (~113k tokens),
separating retrieval cost from reasoning cost; the judge adds abstention rules.

Limitations: one LLM judge at `temperature=0`, no rubric versioning or agreement measure, accuracy-only so superfluous
context is free; corpus and needles are external downloads; `<document_content>` opens twice and never closes with a
distinct tag. Fitness: high for protocol, low for scoring.

## 2. youtal321/nolima (semantic needles)

Lexical distance is constructed (`data/remove_distractors.ipynb`): vocabulary is the haystack's whitespace tokens
after `re.sub(r'[^\w\s-]','',text)`; Contriever word embeddings with mean pooling (CLS/SEP masked) pick the top-25
neighbours per needle word to *choose* distractors; each distractor becomes a regex by edge shape — both edges spaced
-> literal, leading space -> `rf'{d}\w*\b'`, trailing space -> `rf'\b\w*{d}'`, else `rf'\b\w*{d}\w*\b'`; a match is
skipped when any token in `[start-6, end+6]` is in a short bypass list (`cooper` for `oper`); matches expand by `W =
12` chars to the nearest space or newline, overlapping spans merge, and merged spans become `"... "`.
`data/filtering_conflicts.70b.py` then asks Llama-3.3-70B over 1000-char chunks (stride 800) each needle question;
"yes" flags a possible conflicting fact.

Placement (`data/book_haystack.py::BookHaystack._generate_w_needle_placement`): `valid_positions_token_counts` are
token indices whose single-token decode contains `"\n"`; depth is matched by `argmin` over `(pos -
shift)/(context_length+1) - depth` with negatives set to `inf`; `pre = decode[start:pos+1]` is cut back to the last
newline; result `pre + " " + needle + "\n" + post`. `generate_w_needle_placement` adds an optional distractor with
`distractor_free_zone = 0.2` (uniform over the remaining span, forced to 0.5/0.6/0.7 below 500 tokens) and refuses
insertion unless the 50-char pre-context span is unique.

Protocol (`evaluation/async_evaluate.py::NoLiMa_Tester`, `run_tests.py`): depths from `np.linspace(min, max,
intervals)`, 26 by default and 11 for 64K/128K; `{CHAR}` filled from `character_set` with `np.random.choice` seeded by
`base_seed + int(exp_id[:4])`, that character being the gold; metrics `EM`, `contains`, `lastline_EM`,
`lastline_contains`, configs using `contains`; a SHA-256 `test_hash` over the config minus `results`/`eval_name`
deduplicates runs; the template ends "Return only the final answer with no additional explanation or reasoning."

Limitations: needles and haystacks are Hugging Face downloads, so only the protocol is in-repo; redaction is
destructive and corpus-specific; licence is non-commercial. Fitness: high for harness design, unusable as data.

## 3. NVIDIA/RULER

Tasks (`scripts/config_tasks.sh`, `scripts/synthetic.yaml`): 13 keys over 5 generators — `niah_single_1..3`,
`niah_multikey_1..3`, `niah_multivalue`, `niah_multiquery` (`niah.py`), `vt` (`variable_tracking.py`, `num_chains=1,
num_hops=4`), `cwe` (`common_words_extraction.py`, `freq_cw=30, freq_ucw=3, num_cw=10`), `fwe`
(`freq_words_extraction.py`, `alpha=2.0`), `qa_1`/`qa_2` (`qa.py`, `squad`/`hotpotqa`).

Mechanisms. `niah.py`: needle `"One of the special magic {type} for {key} is: {value}."` with 7-digit numbers,
`adjective-noun` wonderwords pairs or `uuid4`; `DEPTHS = np.round(np.linspace(0,100,40))`; insertion at
`int(len(sentences)*depth/100)` over `nltk.sent_tokenize`; haystack size found by **binary search** on `num_haystack`
until `len(tokens(input)) + tokens_to_generate <= max_seq_length`. `variable_tracking.py::shuffle_sublists_heap`
interleaves chains with a priority queue holding one element per sublist, preserving intra-chain order.
`freq_words_extraction.py` samples Zipf counts `num_words * k**-alpha / zeta(alpha)` over a synthetic vocabulary, sets
`vocab[0] = '...'` as noise, answers `vocab[1:4]`; `qa.py` samples distractor documents, numbers them via
`DOCUMENT_PROMPT`, and sizes by binary search.

Scoring (`scripts/eval/synthetic/constants.py`, `evaluate.py`): two metrics — `string_match_part` = 1.0 if any
reference is a case-insensitive substring, `string_match_all` = fraction of references present, both x100 rounded to 2
dp; `qa` uses `part`, the other four tasks use `all`. `postprocess_pred` maps non-printables to newlines; `nulls` is
`sum(len(p)==0)/len(p)`; outputs `summary.csv` and `submission.csv`.

Limitations: substring scoring rewards copying; sizing costs tokeniser calls per probe. Fitness: medium — its tasks
stress attention, not compaction; its budget search is the reusable part.

## 4. compactbench/compactbench (closest prior art on compaction fidelity)

Contracts (`src/compactbench/contracts/artifact.py`, `case.py`): `ARTIFACT_SCHEMA_VERSION = "1.0.0"`;
`StructuredState` has six required sections (`immutable_facts, locked_decisions, deferred_items, forbidden_behaviors,
unresolved_items, entity_map`), lists capped at 200 items and strings at 500 chars; `summary_text` capped at 8000
chars; `selected_source_turn_ids` carries provenance; `Transcript.without_tags()` runs immediately before compaction,
because tags mark distractor turns and the constraint turn, and a five-line tag-dropping method would otherwise top
the board.

Scoring. `compression.py`: one canonical tokeniser (`cl100k_base`) for every ratio; `artifact_tokens` sums summary
text, the five lists and both sides of `entity_map`; `compression_ratio = src / max(dst, 1)`. `checks.py`: four
primitives — `contains_normalized`, `forbidden_absent`, `exact`, `set_match`, normalising by lowercase plus whitespace
collapse. `scorer.py`: locked weights (locked-decision 3.0, forbidden-behavior 3.0, immutable-fact 2.0,
unresolved-task 2.0, entity 1.0, planning 1.0) and `score * (1 - contradiction_rate)`. `contradictions.py` is a
lexical forbidden-phrase detector whose denominator excludes recall items, because a correct recall answer must name
the forbidden thing. `drift.py`: `drift_resistance = clamp(mean(scores[1:]) / scores[0])`, `MIN_CYCLES_FOR_DRIFT = 2`,
unmeasurable on a zero baseline, and `compaction_attributable_drift = method_drift / oracle_drift` so the method is
not charged for model decay.

Cycle protocol (`runner/cycle.py`, `runner/evaluation.py`): for `cycle >= 1` the transcript gains one continuation
pair, then `compact(transcript.without_tags(), previous_artifact)`, then probes are answered **from the artifact
only**, then `score_cycle` with `source_transcript` so the ratio reflects the current cycle;
`CountingProvider.reset()/snapshot()` isolates per-cycle token use. Evaluation fences the artifact in
`<untrusted_artifact>` tags, redacts any inner closing tag, and instructs the reader to treat content as data.

Compactors: `structured_state.py` makes one JSON-forced call over the six keys with empty `summaryText`;
`hybrid_ledger.py` makes a delta call plus a 2-3 sentence header call, and `_merge_ledger` accumulates
`locked_decisions, deferred_items, forbidden_behaviors, unresolved_items` with `uniq_preserve_order` while
re-extracting `immutable_facts` and merging `entity_map` by key; `_utils.fit_summary_text` truncates to the 8000-char
cap and records a warning instead of raising.

Limitations: probes are answered by a model, so a score mixes compaction loss with reader capability; no code or
repository tasks. Fitness: highest in this set for compaction fidelity; the drift and artifact design is directly
adoptable.

## 5. microsoft/acon

`src/productive_agents/ctxopt/base.py::BaseContextOptimizer` counts tokens with
`tiktoken.encoding_for_model("gpt-4o")` and a documented `len//4` fallback; `build_history_text` renders ` - Step {i}:
{json.dumps(action)} -> [{observation}]` with newlines escaped; `convert_llm_history_to_text` drops the first user
message and prefixes `USER:`/`ASSISTANT:`; `llmlingua_compress_context` POSTs to `localhost:9999/compress`, so
LLMLingua is out-of-process here.

`history_optimizer.py`: `check_summarization_needed` compares `count_tokens` with `history_summarization_threshold`
(`-1` = always); `_build_history_prompt` puts the prior summary in `<PREVIOUS_SUMMARY>...</PREVIOUS_SUMMARY>`;
`parse_output(response, "# History Summary")` discards everything before the marker, a cheap reasoning strip;
`HistoryOptimizerV2` appends `"\n[COMPRESSION START]\n" + prompt` to the last raw message to reuse the agent's
KV-cache prefix; `HistoryRetriever` cosine-ranks cached embeddings of user windows and returns top-`n` in order;
`obs_optimizer.py` uses the `# Refined Observation` marker.

Limitations: every compressor is an LLM call, with no deterministic fallback, budget guarantee or fidelity metric.
Fitness: low for mechanism, medium for prompt shape.

## 6. microsoft/LLMLingua (deterministic versus model-based split)

Deterministic. Budget arithmetic: `target_token = (instr + question + sum(context))*rate - instr - (question if
concate_question else 0)`, or `rate = target_token/sum(context)`. `control_context_budget` does `target_token =
eval("target_token" + context_budget)` (default `"+100"`, so the flag becomes `target+100`), then greedy fill
subtracting `context_tokens_length[idx]` until negative with `force_context_ids` always kept; `reorder_context`
supports `original` (sorted) and `two_stage` (even forward, odd reversed); `dynamic_context_compression_ratio` builds
a linear ramp `i*(ratio/(N-1))` over kept order. `control_sentence_budget`: `nltk.sent_tokenize` then `sync_sentence`,
which re-derives spans from the original via `text.find(next[:5])` and asserts `"".join(new) == text` — a hard
guarantee that kept bytes are never rewritten; `keep_first_sentence`, `keep_last_sentence`, `keep_sentence_number` add
`high_priority_bonus = 100`. `iterative_compress_prompt` keeps tokens above a percentile within each `iterative_size =
200` window and `keep_split` protects double split tokens. `compress_prompt_llmlingua2` keeps words above
`np.percentile(word_probs, 100*reduce_rate + 1)` with `force_tokens`, `force_reserve_digit`, `drop_consecutive`, and
`token_to_word in {mean, first, max}` aggregation.

Model-based: `get_ppl`/`get_condition_ppl` (causal-LM cross-entropy; `condition_in_question in {none, before, after}`
conditions sentence loss on the question); rank methods `bm25, gzip, sentbert, openai, bge, longllmlingua, jinza,
voyageai, cohere`; LLMLingua-2 logits; KV-cache window shifting past `max_position_embeddings`. `get_distance_gzip` is
model-free: `(len(gzip(x+" "+y)) - min(len(gzip(x)), len(gzip(y)))) / max(...)`.

Fitness: high for the split — the deterministic half is arithmetic plus one string-equality invariant.

## 7. THUDM/LongBench

v1 (`LongBench/`): `metrics.py` provides eight scorers (`normalize_answer`, `count_score`, `retrieval_score`,
`code_sim_score`, `classification_score`, `rouge_score`, `f1_score`, `qa_f1_score`) plus `_zh` variants; `task.md`
maps 21 datasets to four families with per-task metric, average length and sample count.

v2 has no task modules; the contract is `README.md` plus `pred.py`/`result.py`. Schema: `_id, domain, sub_domain,
difficulty, length, question, choice_A..D, answer, context`. `query_llm` middle-truncates as `input_ids[:max_len//2] +
input_ids[-max_len//2:]` (head+tail, no marker). `extract_answer` matches `r'The correct answer is \(([A-D])\)'` with
a parenthesis-free fallback after `replace('*','')`. `result.py` reports overall plus easy/hard and short/medium/long
slices, with a `compensated` switch giving 0.25 credit when `pred is None` (the 4-way guessing floor).

Limitations: accuracy only, no calibration or per-difficulty weighting, and records keep only `context[:1000]`.
Fitness: medium — useful as a format reference, not as a fidelity measure.

## 8. evalplus/repoqa (needle-in-repository)

`repoqa/search_needle_function.py::make_code_context(needle, file_content_list, position_ratio, code_context_size,
language)`: `prefix_size = int(code_context_size*ratio - needle_tokens/2)`, `suffix_size = code_context_size -
needle_tokens - prefix_size`; the needle file splits at `needle["start_byte"]`/`["end_byte"]`, the prefix grows
**backwards** through preceding files in list order and the suffix forwards, each file opening with a synthetic `//
Path: <path>` line; files follow `utility.topological_sort` (DFS post-order over the dependency graph).
`_backward_tokenizable_lines`/`_forward_tokenizable_lines` add whole lines until the next would overflow, appending
`"...\n"` when truncated. The return value includes `needle_token_start`, `needle_token_end`, `code_context_ntokens`,
so measured needle position is exact. `sanitize_output` extracts fenced blocks and, via tree-sitter `FUNCTION_QUERY`,
trims to the first function node; `remove_comments` deletes comment captures bottom-up by byte offset.

Scoring: `metric.py::compute_function_similarity` is `nltk.sentence_bleu([ref_tokens], cand_tokens,
smoothing_function=SmoothingFunction().method4)` over whitespace tokens. `needle_evaluator` scores against every
candidate, takes `argmax` BLEU, and passes only if that candidate's name equals the gold name;
`compute_language_results` sweeps thresholds `0.0..1.0` and computes `estimate_pass_at_k` for `k in {1,10,100}`.

Limitations: BLEU-4 is surface-based, so a correct function with renamed locals can fail; the pass criterion couples
ranking with generation fidelity. Fitness: high for context-window construction, medium for scoring.

## 9. Aider-AI/aider: `aider/repomap.py` (most relevant to repo-map work)

Extraction (`get_tags_raw`): `filename_to_lang` -> tree-sitter `get_language`/`get_parser` -> per-language
`queries/<pack>/<lang>-tags.scm` via `get_scm_fname`; captures starting `name.definition.` become `kind="def"`,
`name.reference.` become `kind="ref"`, all else dropped; each capture yields `Tag(rel_fname, fname,
line=node.start_point[0], name=node.text, kind)`. Fallback: when a file produced defs but no refs (the C++ query is
definition-only), the code is lexed with `pygments.guess_lexer_for_filename` and every `Token.Name` becomes a `ref`
with `line=-1`.

Ranking (`get_ranked_tags`): `defines[ident] -> {files}`, `references[ident] -> [files]` with multiplicity; a
`networkx.MultiDiGraph` over file paths with edges `referencer -> definer`, so mass flows to definitions of widely
used symbols. Per-ident multiplier `mul`: x10 if mentioned by the user, x10 if snake/kebab/camel with `len>=8`, x0.1
if it starts with `_`, x0.1 if `len(defines[ident]) > 5` (ambiguous names lose weight); `weight = use_mul *
sqrt(num_refs)`, and `use_mul *= 50` when the referencer is a chat file. Definitions with zero references get a
`weight=0.1` self-edge. `nx.pagerank(G, weight="weight", personalization=..., dangling=...)` with `personalize =
100/len(fnames)` for chat files and mentioned paths, plus one increment when a path component matches a mentioned
ident. Redistribution: per source, `data["rank"] = src_rank * weight / sum(out_weights)`, accumulated into
`ranked_definitions[(dst, ident)]` — rank attaches to (file, symbol) pairs, so two symbols in one file can rank
differently. Output skips chat files, prepends `filter_important_files` specials and appends tag-less files last.

Budget (`get_ranked_tags_map_uncached`): start `middle = min(max_map_tokens // 25, num_tags)` (25 tokens/tag), then
binary search on the **number of ranked tags**: render `to_tree(tags[:middle])`, `token_count`, accept if `num_tokens
<= max_map_tokens and num_tokens > best`, or stop when `abs(num_tokens - max_map_tokens)/ max_map_tokens < 0.15` — a
15% band that ends the search early. `RepoMap.token_count` uses the model tokeniser below 200 chars, otherwise samples
every `num_lines // 100` line and scales by `sample_tokens/len(sample_text)*len_text`. `get_repo_map` multiplies
`max_map_tokens` by `map_mul_no_files = 8` when no files are in the chat, capped at `max_context_window - 4096`.

Rendering and refresh: `render_tree` uses `grep_ast.TreeContext` to render only lines of interest plus enclosing scope
(`add_context()`), no line numbers, no colour, cached by `(rel_fname, sorted(lois), mtime)`; `to_tree` groups tags by
file in sorted order, emits `\n<rel_fname>:\n` plus the rendered tree, and truncates every line to 100 chars.
`get_tags` caches `{"mtime","data"}` per file in a `diskcache` SQLite store (`.aider.tags.cache.v3`) with a dict
fallback; `get_ranked_tags_map` caches the whole map on `(chat_files, other_files, max_map_tokens, mentioned_fnames,
mentioned_idents)` and reuses it only when `refresh == "files"`, or `refresh == "auto"` with `map_processing_time >
1.0`.

Limitations: needs tree-sitter grammars, `grep_ast`, `diskcache`, `pygments`; symbol identity is the bare name, so
same-named symbols in different namespaces merge; `token_count` is an estimate, absorbed by the 15% band. Fitness:
highest in this set for repo-map work — the ranking math and budget search port as-is; the parsers and `TreeContext`
scope renderer do not.

## 10. SWE-agent/SWE-agent

Assembly (`sweagent/agent/agents.py::DefaultAgent.messages`): history is filtered by `entry["agent"] == self.name`
(sub-agents share one history), then `history_processors` are **chained** in config order, so compression is a pure
function of stored history applied at request time, never a mutation of it.

Processors (`agent/history_processors.py`). `LastNObservations(n, polling)`: `_get_omit_indices` keeps the last `n`
observation indices, never removes the first (instance template), and with `polling > 1` recomputes the cutoff only
every `polling` steps (`(len//polling)*polling - n`) to keep prompt caches valid; elided observations become `"Old
environment output: (n lines omitted)"`, so elision is marked, never silent. `ClosedWindowHistoryProcessor` keeps the
newest window per file and replaces older ones with `"Outdated window with {n} lines omitted..."`; `RemoveRegex`
deletes matches by `re.DOTALL`.

Truncation: `TemplateConfig.max_observation_length = 100_000` characters, and the notice is instructional —
`...<response clipped><NOTE>... {{elided_chars}} characters were elided. Please try a different command ... </NOTE>`.
`utils/patch_formatter.py::PatchFormatter` merges hunk intervals (`_merge_intervals`) and `format_file` emits explicit
gaps: `"[N lines above omitted]"`, `"\n[N lines omitted]\n"`, `"[N lines below omitted]"`.

Cost (`agent/models.py`): `InstanceStats(instance_cost, tokens_sent, tokens_received, api_calls)` with
`__add__`/`__sub__`, a module-level `GLOBAL_STATS` behind `GLOBAL_STATS_LOCK`; `_update_stats` raises
`TotalCostLimitExceededError`, `InstanceCostLimitExceededError`, `InstanceCallLimitExceededError` as limits trip, and
`_sleep` enforces a global delay; `_single_query` counts input tokens **before** the call and raises
`ContextWindowExceededError` instead of truncating.

Limitations: elision plus regex only, no semantic compression; cache-hostile processors are documented as such.
Fitness: high for the processor-chain architecture and the marked-elision rule.

## 11. letta-ai/letta-code (memory blocks and production compaction policy)

A TypeScript CLI, not the Python Letta server, but it carries the real compaction policy. `src/agent/memory.ts`:
memory blocks are `.mdx` files with frontmatter (`label`, `description`); `MEMORY_BLOCK_LABELS = ["persona","human"]`;
`src/agent/memory-filesystem.ts::renderMemoryFilesystemTree` renders a label tree under three hard caps (line, char
and per-directory limits), with `canAppendLine` checking line and char budgets *before* appending and `"… (N more
entries)"` for elided children.

`src/backend/local/compaction.ts`: modes `all` and `sliding_window` (default),
`LOCAL_DEFAULT_SLIDING_WINDOW_PERCENTAGE = 0.3`, word caps 500/300. Two prompts share a schema: high-level goals; what
happened; important details including "Preserve identifiers verbatim" for paths, URLs and PR/ticket IDs; errors and
fixes; current state; optional next step; lookup hints ("note the topic and key terms that could be used to find it in
message history later"). `planLocalSlidingWindowCompaction` refuses under 4 messages, requires the cutoff on an
`assistant` message strictly before the end and never at a pending tool call (`hasPendingLocalToolCall` =>
`maximumCutoffIndex = len-2`), and when `contextWindow` is known escalates `evictionPercentage` in `+0.1` steps until
`estimateLocalMessageTokens(messagesToKeep) < (1-percentage)*contextWindow`. `assertLocalCompactionProgress` throws
`LocalIrreducibleContextError` when a compaction would not reduce message count **or** estimated tokens.
`middleTruncateText(text, budget, headFrac=0.3, tailFrac=0.3)` inserts `"[TRUNCATED: dropped N middle chars due to
context budget]"`; `truncateToolReturn` cuts at `LOCAL_SUMMARY_TOOL_RETURN_TRUNCATION_CHARS = 2_000`;
`TRANSCRIPT_FALLBACK_MAX_CHAR_STEPS` is a descending 120k..2k retry ladder for summary input.

`src/backend/local/local-context-estimate.ts`: estimate anchored on the last non-zero `usage` (`totalTokens`, or
input+output+cacheRead+cacheWrite) plus `ceil(chars/4)` for trailing messages, `IMAGE_TOKEN_ESTIMATE = 1200`; anchors
at or before the last compaction timestamp are ignored as stale. Fitness: high for policy, since the progress
assertion and boundary rule are model-independent.

## 12. xiaowu0162/LongMemEval-V2

Layout: `data/`, `evaluation/` (`harness.py`, `qa_eval_metrics.py`, `run_eval.py`, `memory_configs/*.json`),
`leaderboard/`, `memory_modules/` (`no_retrieval`, `rag`, `agentrunbook_r/c`, `codex`); 451 questions, up to 500
trajectories and 115M tokens per haystack.

Scoring (`evaluation/qa_eval_metrics.py`): `normalize_phrase` lowercases, maps hyphen/underscore to space, strips
punctuation and collapses whitespace; `norm_phrase_set_match` requires every comma/semicolon reference phrase to
appear with word boundaries — stronger than substring; `norm_phrase_set_match_ordered` also requires increasing
positions; abstention and gotchas items use an LLM judge with explicit rules ("follows the flawed premise => 0", "bare
UNKNOWN => 0", "partial coverage without contradiction => 1"); `eval_from_spec` resolves `"func|k=v"` strings to
callables.

`harness.py::truncate_memory_context` fits memory to `memory_context_max_tokens` by **binary search on prefix length**
with a memoised prefix-token map and reports original versus truncated counts. `agentrunbook_r.py::_truncate_middle`
keeps head+tail with `"\n...[truncated N chars]...\n"`. Fitness: medium — the phrase-set scorer and prefix search are
reusable, the memory modules need models.

## 13. nelson-liu/lost-in-the-middle

Placement: `scripts/make_qa_data_from_retrieval_results.py` writes one dataset per gold index — take the top `N-1`
retrieved passages with `hasanswer is False`, insert the gold chunk at `gold_index`, record
`original_retrieval_index`/`isgold` per distractor. Shipped grids: 10/20/30 documents with gold at `0, 4|9, 14|19|24,
29`. `src/lost_in_the_middle/prompting.py` renders `Document [{i+1}](Title: {title}) {text}` per passage.

Scoring: `metrics.py::best_subspan_em` after SQuAD-style `normalize_answer`. `scripts/evaluate_qa_responses.py` keeps
only `model_answer.split("\n")[0]`, with the comment that otherwise "models could hack the metric by simply copying
the input context", since the gold answer is guaranteed to be in the context — the anti-cheat rule our substring
matching needs.

Reported effect (`EXPERIMENTS.md`): mpt-30b-instruct, 20 documents, `best_subspan_em` 0.5367 (gold at 0), 0.5175 (4),
0.5217 (9), 0.5266 (14), 0.5623 (19), against 0.8166 oracle and 0.3168 closed-book — a shallow U-shape for this model,
so re-measure rather than assume. Fitness: high for placement design; its 268 MB of gzipped data and GPU scripts are
not practical here.

## IMPLEMENTATION CONSTRAINTS FOR OPTIMUS

Rust core: no GPU, no fine-tuning, no network, no embedding model. Each mechanism below depends only on strings,
integers, regex, or local file metadata.

- Budget fitting: binary search on item count or prefix length over a monotone token estimate with an explicit
  tolerance band (aider 0.15; RULER `niah.py`; LongMemEval-V2 `truncate_memory_context`).
- Token estimation: sample every `n//100` lines and scale (aider `token_count`); `ceil(chars/4 + digits/12 +
  newlines/2)` (our `corpus.TOKEN_ESTIMATOR`); usage-anchored plus `ceil(chars/4)` (letta).
- Needle-centred window: repoqa `make_code_context` — `prefix = budget*ratio - needle/2`, walk dependency-ordered
  files outward, stop on overflow, record the exact needle token span.
- Symbol graph: aider `get_ranked_tags` — directed file graph, `weight = mul*sqrt(num_refs)` with the
  x10/x10/x0.1/x0.1/x50 table, personalized PageRank, rank redistributed to `(dst, ident)`; power iteration is
  fixed-length and seedless.
- Dependency order: repoqa `topological_sort`, DFS post-order over the import graph.
- Reserve-aware rendering: letta `renderMemoryFilesystemTree`, aider `to_tree` — check budgets before appending, `"…
  (N more entries)"`, truncate lines to 100 chars.
- Marked elision: SWE-agent `LastNObservations`, `PatchFormatter.format_file` — replace removed spans with a `"N lines
  omitted"` marker, never delete silently.
- Head/tail truncation: LongBench `query_llm`, letta `middleTruncateText` — keep 0.3/0.3 or 0.5/0.5 and insert
  `"[dropped N chars]"`.
- Span-preservation proof: LLMLingua `sync_sentence` — re-derive spans from the original and assert `join(kept) ==
  input`.
- Percentile keep/drop: LLMLingua-2 `__compress` — score spans, drop below `percentile(100*reduce+1)`, force-protect
  listed tokens.
- Model-free relevance: `get_distance_gzip` — `(len(gzip(x+" "+y)) - min(len(gzip(x)),len(gzip(y)))) /
  max(len(gzip(x)),len(gzip(y)))`.
- Lexical-distance audit: NoLiMa `remove_distractors.ipynb` — infix/prefix/suffix regex, ±12-char span, merged
  overlaps, bypass list.
- Gold-position sweep: lost-in-the-middle data builder — one dataset per gold index over one distractor pool.
- Compaction progress assertion: letta `assertLocalCompactionProgress` — error when message count and estimated tokens
  both fail to fall.
- Fidelity artifact and drift: compactbench `contracts/artifact.py` + `scoring/drift.py` — six sections, weighted item
  score, `clamp(mean(later)/baseline)`, oracle-normalised ratio.
- Anti-echo scoring: first-line-only answers (lost-in-the-middle) plus word-boundary phrase-set matching
  (LongMemEval-V2).
- Output fencing: compactbench `runner/evaluation.py` — wrap compactor output in `<untrusted_artifact>`, redact inner
  closing tags.

Out of scope for the deterministic core, always behind a model adapter and never trusted: LLM judges (context-rot,
NoLiMa, LongMemEval-V2 abstention/gotchas), perplexity selection (`get_ppl`), embedding retrieval (acon
`HistoryRetriever`, LongMemEval-V2 `rag`), LLM-written summaries (acon, compactbench compactors, letta).

Gaps this survey closes in `research/context_rot/`:

1. Needle lexical distance is asserted, not constructed. Add a NoLiMa-style overlap audit (shared content tokens
  between question and needle against the haystack) and store it per item.
2. `multicycle.py` has one deterministic compactor. Add an artifact-model compactor (six sections plus provenance turn
  ids) and a drift metric with an oracle denominator, both scorable without a model.
3. Repo-map budgeting is absent. Port aider's multiplier table and tolerance-band search plus repoqa's needle-centred
  window onto our own symbol extractor.
4. Truncation records `truncate_strategy` but leaves no in-text marker. Add dropped-count markers (SWE-agent / letta
  style) so loss is visible and refusals become measurable.
