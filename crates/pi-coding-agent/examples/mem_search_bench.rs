//! Deterministic, LLM-free benchmark of the native memory lexical search path.
//!
//! Measures the REAL library retrieval functions — `search_memory`,
//! `MemoryService::search`, `MemoryService::recall` — against synthesized
//! stores of N entries (Zipf-ish word distribution, ~120-char titles,
//! 400-2000-char contents) written once to disk in the native
//! `harness_state.json` format. No LLM, no network, no store writes during
//! measurement. Mirrors `memory_recall_probe.rs` public-field construction of
//! `MemoryStore`/`MemoryService` (avoids `MemoryStore::new` host-id
//! persistence), and `memory_bench.rs`'s `service.search(...)` usage.
//!
//! Usage:
//!   cargo run -p pi-coding-agent --example mem_search_bench --release -- <out_root> [N ...]
//!
//!   <out_root>  directory for synthesized stores + results.json (keep short:
//!              MAX_PATH). Stores land in <out_root>/n<N>/agent/project/.
//!   [N ...]     entry counts, default 1000 5000 20000 50000.
//!
//! Output: per-N tables on stdout plus <out_root>/results.json with raw
//! per-query timings (µs) and hit counts for every measured path.

use std::collections::HashSet;
use std::path::Path;
use std::time::Instant;

use pi_coding_agent::core::memory::jobs::MemoryJobs;
use pi_coding_agent::core::memory::project::ProjectIdentity;
use pi_coding_agent::core::memory::search::search_memory;
use pi_coding_agent::core::memory::service::MemoryService;
use pi_coding_agent::core::memory::sharing::MemorySharing;
use pi_coding_agent::core::memory::store::MemoryStore;
use serde_json::{json, Map, Value};

const PROJECT_ID: &str = "project_bench";
const HOST_ID: &str = "00000000-0000-0000-0000-000000000001";
const VOCAB_SIZE: usize = 5000;
const ZIPF_EXPONENT: f64 = 1.1;
const DEFAULT_QUERIES: usize = 200;
const WARMUP_QUERIES: usize = 2;
const READ_SAMPLES: usize = 5;

// ---------------------------------------------------------------------------
// Deterministic RNG (SplitMix64): fixed seed => identical corpus every run.
// ---------------------------------------------------------------------------

struct Rng(u64);

impl Rng {
    fn new(seed: u64) -> Rng {
        Rng(seed)
    }
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next_u64() % n as u64) as usize
    }
}

/// Zipf rank sampler: P(rank r) ~ 1/(r+1)^s, inverse-CDF with binary search.
struct Zipf {
    cdf: Vec<f64>,
    total: f64,
}

impl Zipf {
    fn new(n: usize, s: f64) -> Zipf {
        let mut cdf = Vec::with_capacity(n);
        let mut acc = 0.0;
        for r in 0..n {
            acc += 1.0 / (r as f64 + 1.0).powf(s);
            cdf.push(acc);
        }
        Zipf { cdf, total: acc }
    }
    fn sample(&self, rng: &mut Rng) -> usize {
        let u = rng.next_f64() * self.total;
        match self
            .cdf
            .binary_search_by(|x| x.partial_cmp(&u).unwrap())
        {
            Ok(i) => i,
            Err(i) => i.min(self.cdf.len() - 1),
        }
    }
}

/// Syllable-ish pseudo-words, 3-9 lowercase letters, at least one vowel.
fn make_vocab(rng: &mut Rng, size: usize) -> Vec<String> {
    const CONSONANTS: &[u8] = b"bcdfghjklmnpqrstvwxyz";
    const VOWELS: &[u8] = b"aeiou";
    let mut words = Vec::with_capacity(size);
    let mut seen = HashSet::new();
    while words.len() < size {
        let len = 3 + rng.below(7);
        let mut word = String::with_capacity(len);
        for i in 0..len {
            let source = if rng.below(2) == (i % 2) {
                VOWELS
            } else {
                CONSONANTS
            };
            word.push(source[rng.below(source.len())] as char);
        }
        if word.contains(|c: char| VOWELS.contains(&(c as u8))) && seen.insert(word.clone()) {
            words.push(word);
        }
    }
    words
}

/// Word indices until the joined length reaches a target drawn from
/// [min_chars, max_chars]; returns vocabulary indices (not strings).
fn word_seq(
    rng: &mut Rng,
    zipf: &Zipf,
    vocab: &[String],
    min_chars: usize,
    max_chars: usize,
) -> Vec<usize> {
    let target = min_chars + rng.below(max_chars - min_chars + 1);
    let mut words = Vec::new();
    let mut len = 0usize;
    while len < target {
        let index = zipf.sample(rng);
        words.push(index);
        len += vocab[index].len() + 1;
    }
    words
}

fn stats(mut v: Vec<f64>) -> Value {
    if v.is_empty() {
        return json!({"n": 0});
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    let pick = |p: f64| -> f64 { v[((p * (n - 1) as f64).round() as usize).min(n - 1)] };
    json!({
        "n": n,
        "min": v[0],
        "median": pick(0.5),
        "mean": v.iter().sum::<f64>() / n as f64,
        "p95": pick(0.95),
        "max": v[n - 1],
    })
}

fn ints_stats(v: &[usize]) -> Value {
    let floats: Vec<f64> = v.iter().map(|x| *x as f64).collect();
    stats(floats)
}

// ---------------------------------------------------------------------------
// Corpus synthesis
// ---------------------------------------------------------------------------

struct Corpus {
    document: Value,
    entry_words: Vec<Vec<usize>>,
    title_chars: usize,
    content_chars: usize,
    distinct_words: usize,
}

/// Build a native MemoryDocument with N "memory" entries. Word lengths from
/// the real vocab make title ~120 chars and content 400-2000 chars.
fn synthesize(n: usize, vocab: &[String], rng: &mut Rng, zipf: &Zipf) -> Corpus {
    let mut title_chars = 0usize;
    let mut content_chars = 0usize;
    let mut distinct: HashSet<usize> = HashSet::new();
    let mut entry_words = Vec::with_capacity(n);
    let mut memory_bucket = Map::with_capacity(n);
    for i in 0..n {
        // ~120-char title: words until joined length reaches 105-125.
        let title_indices = word_seq(rng, zipf, vocab, 105, 125);
        // 400-2000-char content.
        let content_indices = word_seq(rng, zipf, vocab, 400, 2000);
        let title: String = title_indices
            .iter()
            .map(|&w| vocab[w].as_str())
            .collect::<Vec<_>>()
            .join(" ");
        let content: String = content_indices
            .iter()
            .map(|&w| vocab[w].as_str())
            .collect::<Vec<_>>()
            .join(" ");
        title_chars += title.chars().count();
        content_chars += content.chars().count();
        distinct.extend(title_indices.iter().copied());
        distinct.extend(content_indices.iter().copied());
        entry_words.push(
            title_indices
                .iter()
                .chain(content_indices.iter())
                .copied()
                .collect(),
        );
        let created = format!(
            "2026-01-01T{:02}:{:02}:{:02}Z",
            (i / 3600) % 24,
            (i / 60) % 60,
            i % 60
        );
        let entry = json!({
            "id": format!("m{:06}", i),
            "kind": "memory",
            "title": title,
            "content": content,
            "path": "general",
            "metadata": {"projectId": PROJECT_ID},
            "reference": {},
            "arguments": {},
            "source": "refine",
            "created_at": created,
            "updated_at": created,
            "version": 1,
        });
        memory_bucket.insert(format!("m{:06}", i), entry);
    }
    let document = json!({
        "schema": 1.0,
        "entries": {
            "memory": memory_bucket,
            "prompt": {},
            "skill": {},
            "subagent": {},
        },
        "refinements": [],
        "memory": {
            "schema": 1.0,
            "projectId": PROJECT_ID,
            "revision": 0,
            "history": [],
            "events": {},
        },
    });
    Corpus {
        document,
        entry_words,
        title_chars,
        content_chars,
        distinct_words: distinct.len(),
    }
}

/// M realistic 20-80-char queries: half drawn from stored entries (guaranteed
/// lexical overlap), half Zipf-sampled vocabulary (realistic miss/low-hit mix).
fn make_queries(
    corpus: &Corpus,
    vocab: &[String],
    rng: &mut Rng,
    zipf: &Zipf,
    count: usize,
) -> Vec<String> {
    let mut queries = Vec::with_capacity(count);
    for i in 0..count {
        let mut query = String::new();
        for _attempt in 0..64 {
            let words: Vec<usize> = if i % 2 == 0 {
                // Entry-derived: distinct words from one random entry.
                let entry = &corpus.entry_words[rng.below(corpus.entry_words.len())];
                let count = 2 + rng.below(3);
                let mut picked: Vec<usize> = Vec::with_capacity(count);
                let mut seen = HashSet::new();
                while picked.len() < count.min(entry.len()) {
                    let candidate = entry[rng.below(entry.len())];
                    if seen.insert(candidate) {
                        picked.push(candidate);
                    }
                }
                picked
            } else {
                // Vocabulary: 2-6 Zipf-drawn words.
                let count = 2 + rng.below(5);
                (0..count).map(|_| zipf.sample(rng)).collect()
            };
            let candidate: String = words
                .iter()
                .map(|&w| vocab[w].as_str())
                .collect::<Vec<_>>()
                .join(" ");
            let len = candidate.chars().count();
            if (20..=80).contains(&len) {
                query = candidate;
                break;
            }
        }
        if query.is_empty() {
            // Deterministic fallback: common words, clipped to 80 chars.
            let words: Vec<usize> = (0..6).map(|_| zipf.sample(rng)).collect();
            let candidate: String = words
                .iter()
                .map(|&w| vocab[w].as_str())
                .collect::<Vec<_>>()
                .join(" ");
            let clipped: String = candidate.chars().take(80).collect();
            let trimmed = clipped.trim().to_string();
            query = if trimmed.chars().count() < 20 {
                candidate
            } else {
                trimmed
            };
        }
        queries.push(query);
    }
    queries
}

// ---------------------------------------------------------------------------
// One benchmark run for a single N
// ---------------------------------------------------------------------------

fn run(n: usize, out_root: &Path, vocab: &[String], queries: usize) -> Result<Value, String> {
    let mut rng = Rng::new(0x5EED_0000_0000_0001u64 ^ n as u64);
    let zipf = Zipf::new(VOCAB_SIZE, ZIPF_EXPONENT);
    let corpus = synthesize(n, vocab, &mut rng, &zipf);
    let queries = make_queries(&corpus, vocab, &mut rng, &zipf, queries);

    // Layout (short paths): <out_root>/n<N>/agent/project/harness_state.json
    let root = out_root.join(format!("n{}", n));
    let agent_dir = root.join("agent");
    let dir = agent_dir.join("project");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let store_path = dir.join("harness_state.json");
    let write_started = Instant::now();
    let body = format!(
        "{}\n",
        serde_json::to_string_pretty(&corpus.document).map_err(|e| e.to_string())?
    );
    std::fs::write(&store_path, body.as_bytes()).map_err(|e| e.to_string())?;
    let write_seconds = write_started.elapsed().as_secs_f64();
    let store_bytes = std::fs::metadata(&store_path)
        .map(|m| m.len())
        .map_err(|e| e.to_string())?;

    // Real library handles, public-field construction (memory_recall_probe
    // pattern): no MemoryStore::new host-id persistence, no project binding.
    let store = MemoryStore {
        agent_dir: agent_dir.to_string_lossy().into_owned(),
        project: ProjectIdentity {
            id: PROJECT_ID.to_string(),
            root: root.to_string_lossy().into_owned(),
            aliases: Vec::new(),
        },
        dir: dir.to_string_lossy().into_owned(),
        path: store_path.to_string_lossy().into_owned(),
        host_id: HOST_ID.to_string(),
    };
    let service = MemoryService {
        jobs: MemoryJobs::new(store.clone()),
        sharing: MemorySharing::new(store.clone()),
        session_artifact_dir: None,
        store: store.clone(),
    };

    // Store load from disk in isolation: full read + validate + deserialize.
    let mut read_ms: Vec<f64> = Vec::with_capacity(READ_SAMPLES);
    for _ in 0..READ_SAMPLES {
        let started = Instant::now();
        let document = store.read();
        let elapsed = started.elapsed().as_secs_f64() * 1e3;
        let entries = document.map(|d| d.entries["memory"].len()).unwrap_or(0);
        if entries != n {
            return Err(format!("store read returned {} entries, expected {}", entries, n));
        }
        read_ms.push(elapsed);
    }

    // Warmup both paths (page cache, allocator).
    for query in queries.iter().take(WARMUP_QUERIES) {
        let _ = search_memory(&store, query, &[], false);
        let _ = service.search(query, false);
        let _ = service.recall(query);
    }

    // Path 1: lexical core — search_memory re-reads the store every call.
    let q = queries.len();
    let mut search_us: Vec<f64> = Vec::with_capacity(q);
    let mut hit_counts: Vec<usize> = Vec::with_capacity(q);
    for query in &queries {
        let started = Instant::now();
        let hits = search_memory(&store, query, &[], false);
        let elapsed = started.elapsed().as_secs_f64() * 1e6;
        search_us.push(elapsed);
        hit_counts.push(hits.len());
    }

    // Path 2: MemoryService::search — adds global/shared corpus loads + dedup.
    let mut service_search_us: Vec<f64> = Vec::with_capacity(q);
    for query in &queries {
        let started = Instant::now();
        let hits = service.search(query, false);
        let elapsed = started.elapsed().as_secs_f64() * 1e6;
        service_search_us.push(elapsed);
        let _ = hits.len();
    }

    // Path 3: MemoryService::recall — search + settings + render (no LLM).
    let mut service_recall_us: Vec<f64> = Vec::with_capacity(q);
    let mut recall_chars: Vec<usize> = Vec::with_capacity(q);
    for query in &queries {
        let started = Instant::now();
        let recall = service.recall(query);
        let elapsed = started.elapsed().as_secs_f64() * 1e6;
        service_recall_us.push(elapsed);
        recall_chars.push(recall.chars);
    }

    Ok(json!({
        "n": n,
        "store_mb": store_bytes as f64 / (1024.0 * 1024.0),
        "write_s": write_seconds,
        "title_chars_avg": corpus.title_chars as f64 / n as f64,
        "content_chars_avg": corpus.content_chars as f64 / n as f64,
        "distinct_words": corpus.distinct_words,
        "read_ms": stats(read_ms),
        "search_memory_us": {
            "stats": stats(search_us.clone()),
            "per_query": search_us,
        },
        "hits": {
            "stats": ints_stats(&hit_counts),
            "per_query": hit_counts,
        },
        "service_search_us": {
            "stats": stats(service_search_us.clone()),
            "per_query": service_search_us,
        },
        "service_recall_us": {
            "stats": stats(service_recall_us.clone()),
            "per_query": service_recall_us,
            "recall_chars_avg": recall_chars.iter().sum::<usize>() as f64
                / recall_chars.len().max(1) as f64,
        },
        "queries_chars_avg": queries.iter().map(|q| q.chars().count()).sum::<usize>() as f64
            / queries.len() as f64,
    }))
}

fn print_summary(result: &Value) {
    let n = result["n"].as_u64().unwrap_or(0);
    let mb = result["store_mb"].as_f64().unwrap_or(0.0);
    let read_med = result["read_ms"]["median"].as_f64().unwrap_or(0.0);
    let search_med = result["search_memory_us"]["stats"]["median"].as_f64().unwrap_or(0.0);
    let search_p95 = result["search_memory_us"]["stats"]["p95"].as_f64().unwrap_or(0.0);
    let svc_med = result["service_search_us"]["stats"]["median"].as_f64().unwrap_or(0.0);
    let svc_p95 = result["service_search_us"]["stats"]["p95"].as_f64().unwrap_or(0.0);
    let rec_med = result["service_recall_us"]["stats"]["median"].as_f64().unwrap_or(0.0);
    let rec_p95 = result["service_recall_us"]["stats"]["p95"].as_f64().unwrap_or(0.0);
    let hits_med = result["hits"]["stats"]["median"].as_f64().unwrap_or(0.0);
    let per_entry_us = search_med / n as f64;
    println!(
        "| {:>6} | {:>8.1} | {:>9.1} | {:>9.0} | {:>9.0} | {:>9.0} | {:>9.0} | {:>9.0} | {:>9.0} | {:>8.0} | {:>8.2} |",
        n, mb, read_med, search_med, search_p95, svc_med, svc_p95, rec_med, rec_p95, hits_med, per_entry_us
    );
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!(
            "Usage: mem_search_bench <out_root> [--queries M] [N ...]   (default N: 1000 5000 20000 50000, default M: 200)"
        );
        std::process::exit(2);
    }
    let out_root = Path::new(&args[1]).to_path_buf();
    std::fs::create_dir_all(&out_root).expect("create out_root");
    let mut query_count = DEFAULT_QUERIES;
    let mut sizes: Vec<usize> = Vec::new();
    for arg in &args[2..] {
        if let Some(value) = arg.strip_prefix("--queries=") {
            query_count = value.parse().unwrap_or(DEFAULT_QUERIES);
        } else if arg == "--queries" {
            eprintln!("use --queries=M");
            std::process::exit(2);
        } else if let Ok(size) = arg.parse::<usize>() {
            sizes.push(size);
        }
    }
    let sizes = if sizes.is_empty() {
        vec![1000, 5000, 20000, 50000]
    } else {
        sizes
    };

    let mut rng = Rng::new(0x5EED_1234_ABCD_0001);
    let vocab = make_vocab(&mut rng, VOCAB_SIZE);

    println!("mem_search_bench: vocab={} Zipf s={} queries={} warmup={} read_samples={}",
        VOCAB_SIZE, ZIPF_EXPONENT, query_count, WARMUP_QUERIES, READ_SAMPLES);
    println!();
    println!("Per-N results (times are per query unless noted):");
    println!("|      N | store MB | read ms (median, isolated) |");
    println!();

    let mut results: Vec<Value> = Vec::with_capacity(sizes.len());
    for size in &sizes {
        match run(*size, &out_root, &vocab, query_count) {
            Ok(result) => {
                println!(
                    "N={:>6}: store {:>8.1} MB | read median {:>9.1} ms | title avg {:>5.1} ch | content avg {:>6.1} ch | distinct words {}",
                    result["n"].as_u64().unwrap_or(0),
                    result["store_mb"].as_f64().unwrap_or(0.0),
                    result["read_ms"]["median"].as_f64().unwrap_or(0.0),
                    result["title_chars_avg"].as_f64().unwrap_or(0.0),
                    result["content_chars_avg"].as_f64().unwrap_or(0.0),
                    result["distinct_words"].as_u64().unwrap_or(0),
                );
                results.push(result);
            }
            Err(error) => {
                eprintln!("N={} failed: {}", size, error);
                std::process::exit(1);
            }
        }
    }

    println!();
    println!("Per-query latency (µs):");
    println!("|      N |   store MB |  read ms | search med | search p95 | svc med | svc p95 | recall med | recall p95 | hits med | µs/entry |");
    println!("|-------:|-----------:|---------:|-----------:|-----------:|--------:|--------:|-----------:|-----------:|---------:|---------:|");
    for result in &results {
        print_summary(result);
    }

    let report = json!({
        "bench": "mem_search_bench/v1",
        "vocab_size": VOCAB_SIZE,
        "zipf_exponent": ZIPF_EXPONENT,
        "queries": query_count,
        "results": results,
    });
    let report_path = out_root.join("results.json");
    std::fs::write(
        &report_path,
        serde_json::to_string_pretty(&report).map_err(|e| e.to_string()).expect("serialize results"),
    )
    .expect("write results.json");
    println!();
    println!("Raw results: {}", report_path.display());
}
