//! Content-free, in-memory provider timing totals. Independent of disk monitoring.
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, OnceLock, Weak};

use pi_agent_core::performance_metrics::{
    PerformanceMetricEvent, PerformanceMetricMeasurement as M, PerformanceMetricOperation,
    PerformanceMetricOutcome,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub generation: String,
    pub output_tokens: f64,
    /// Sum of measured first-to-last nonempty delta intervals. Never dispatch time.
    pub streaming_ms: f64,
    pub responses: u64,
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub estimated: bool,
    #[serde(default)]
    pub sequence: u64,
}

pub struct Counter {
    snapshot: Snapshot,
    seen: VecDeque<String>,
    pending: Option<(String, f64, f64)>,
}

type Registry = Mutex<HashMap<String, Weak<Mutex<Counter>>>>;
fn registry() -> &'static Registry {
    static REGISTRY: OnceLock<Registry> = OnceLock::new();
    REGISTRY.get_or_init(|| Mutex::new(HashMap::new()))
}

pub fn register(session_id: &str) -> Arc<Mutex<Counter>> {
    let counter = Arc::new(Mutex::new(Counter {
        snapshot: Snapshot {
            generation: uuid::Uuid::new_v4().to_string(),
            ..Default::default()
        },
        seen: VecDeque::new(),
        pending: None,
    }));
    let mut registry = registry().lock().unwrap_or_else(|e| e.into_inner());
    registry.retain(|_, entry| entry.strong_count() > 0);
    registry.insert(session_id.to_string(), Arc::downgrade(&counter));
    counter
}

pub fn snapshot(session_id: &str) -> Option<Snapshot> {
    // Release registry before taking a counter lock; recording never locks registry.
    let counter = registry()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(session_id)?
        .upgrade()?;
    let snapshot = counter.lock().unwrap_or_else(|e| e.into_inner()).current();
    Some(snapshot)
}

/// Bound worker roster broadcasts to four per second while deltas arrive.
pub fn publish_due(session_id: &str) -> bool {
    let counter = registry()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(session_id)
        .and_then(Weak::upgrade);
    let Some(counter) = counter else {
        return false;
    };
    if counter
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .pending
        .is_none()
    {
        return false;
    }
    // All sessions in this worker share the budget. Never hold the counter lock
    // while acquiring this gate (or while the daemon composes its roster).
    static LAST_PUBLISHED: Mutex<Option<std::time::Instant>> = Mutex::new(None);
    let mut last = LAST_PUBLISHED.lock().unwrap_or_else(|e| e.into_inner());
    let now = std::time::Instant::now();
    if last.is_some_and(|last| {
        now.saturating_duration_since(last) < std::time::Duration::from_millis(250)
    }) {
        return false;
    }
    *last = Some(now);
    true
}

impl Counter {
    fn current(&self) -> Snapshot {
        let mut sample = self.snapshot.clone();
        if let Some((_, tokens, ms)) = &self.pending {
            sample.output_tokens += tokens;
            sample.streaming_ms += ms;
            sample.active = true;
            sample.estimated = true;
        }
        sample
    }

    fn clear_pending(&mut self) {
        if self.pending.take().is_some() {
            self.snapshot.sequence = self.snapshot.sequence.saturating_add(1);
        }
    }

    pub fn progress(&mut self, id: &str, tokens: f64, ms: f64) {
        if id.is_empty() || self.seen.iter().any(|seen| seen == id) {
            return;
        }
        if !tokens.is_finite()
            || tokens <= 0.0
            || !ms.is_finite()
            || ms <= 0.0
            || !(self.snapshot.output_tokens + tokens).is_finite()
            || !(self.snapshot.streaming_ms + ms).is_finite()
        {
            // The first delta has no interval; an invalid later clock also
            // invalidates a previous live estimate. Never invent a denominator.
            self.clear_pending();
            return;
        }
        if self
            .pending
            .as_ref()
            .is_some_and(|(pending, old_tokens, old_ms)| {
                pending == id && *old_tokens == tokens && *old_ms == ms
            })
        {
            return;
        }
        self.pending = Some((id.into(), tokens, ms));
        self.snapshot.sequence = self.snapshot.sequence.saturating_add(1);
    }

    pub fn record(&mut self, event: &PerformanceMetricEvent) {
        if event.operation != PerformanceMetricOperation::ProviderAttempt {
            return;
        }
        let Some(outcome) = event.outcome else {
            return;
        };
        let Some(id) = event
            .correlation
            .as_ref()
            .and_then(|c| c.provider_attempt_id.as_ref())
            .filter(|id| !id.is_empty())
        else {
            return;
        };
        if self.seen.contains(id) {
            return;
        }
        if outcome == PerformanceMetricOutcome::Started {
            if self
                .pending
                .as_ref()
                .is_some_and(|(pending, _, _)| pending != id)
            {
                self.clear_pending();
            }
            return;
        }
        if self
            .pending
            .as_ref()
            .is_some_and(|(pending, _, _)| pending == id)
        {
            self.clear_pending();
        }
        // Remember every terminal, including missing usage/timing, so late
        // progress cannot resurrect its discarded estimate.
        self.seen.push_back(id.clone());
        if self.seen.len() > 256 {
            self.seen.pop_front();
        }
        let Some(tokens) = event
            .usage
            .as_ref()
            .filter(|usage| usage.source == "provider")
            .and_then(|usage| usage.output_tokens)
            .filter(|n| n.is_finite() && *n > 0.0)
        else {
            return;
        };
        // No fallback: dispatch, first-token latency, and terminal drain are
        // not streaming time. Provider output totals already include reasoning
        // when the provider defines them that way; never add it again here.
        let Some(ms) = event
            .measurements
            .as_ref()
            .and_then(|values| values.get(&M::StreamingMs).copied().flatten())
            .filter(|n| n.is_finite() && *n > 0.0)
        else {
            return;
        };
        let tokens = self.snapshot.output_tokens + tokens;
        let ms = self.snapshot.streaming_ms + ms;
        if !tokens.is_finite() || !ms.is_finite() {
            return;
        }
        self.snapshot.output_tokens = tokens;
        self.snapshot.streaming_ms = ms;
        self.snapshot.responses = self.snapshot.responses.saturating_add(1);
        self.snapshot.sequence = self.snapshot.sequence.saturating_add(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pi_agent_core::performance_metrics::{
        PerformanceMetricCorrelation, PerformanceMetricUsageV1,
    };

    fn event(id: &str, tokens: f64, ms: f64) -> PerformanceMetricEvent {
        let mut event = PerformanceMetricEvent::new(PerformanceMetricOperation::ProviderAttempt);
        event.outcome = Some(PerformanceMetricOutcome::Success);
        event.correlation = Some(PerformanceMetricCorrelation {
            provider_attempt_id: Some(id.into()),
            ..Default::default()
        });
        event.usage = Some(PerformanceMetricUsageV1 {
            output_tokens: Some(tokens),
            ..PerformanceMetricUsageV1::provider_unavailable()
        });
        event.measurements = Some([(M::StreamingMs, Some(ms))].into_iter().collect());
        event
    }

    #[test]
    fn throughput_streaming_updates_replace_estimates_and_clear_on_failure() {
        let counter = register("live-throughput-unit");
        let mut counter = counter.lock().unwrap();
        counter.progress("a", 10.0, 100.0);
        let first = counter.current();
        assert!(first.active && first.estimated);
        assert_eq!(first.responses, 0);
        counter.progress("a", 30.0, 200.0);
        assert_eq!(counter.current().output_tokens, 30.0);
        counter.record(&event("a", 25.0, 150.0));
        let final_sample = counter.current();
        assert!(!final_sample.active && !final_sample.estimated);
        assert_eq!(final_sample.output_tokens, 25.0);
        assert_eq!(final_sample.streaming_ms, 150.0);
        assert!(final_sample.sequence > first.sequence);
        counter.progress("a", 40.0, 300.0); // late delta after terminal
        assert_eq!(counter.current().output_tokens, 25.0);
        counter.progress("b", 10.0, 100.0);
        let mut failed = event("b", 0.0, 100.0);
        failed.outcome = Some(PerformanceMetricOutcome::Cancelled);
        counter.record(&failed);
        assert!(!counter.current().active);
        assert_eq!(counter.current().output_tokens, 25.0);
    }

    #[test]
    fn throughput_counts_only_measured_attempts_once_and_excludes_other_work() {
        let counter = register("throughput-unit-counter");
        let mut counter = counter.lock().unwrap();
        let good = event("a", 100.0, 2000.0);
        counter.record(&good);
        counter.record(&good);
        let mut other = event("b", 500.0, 1000.0);
        other.operation = PerformanceMetricOperation::LogicalRequest;
        counter.record(&other);
        other.operation = PerformanceMetricOperation::Tool;
        counter.record(&other);
        other.operation = PerformanceMetricOperation::ProviderAttempt;
        other.outcome = Some(PerformanceMetricOutcome::Started);
        counter.record(&other);
        for bad in [
            event("zero", 1.0, 0.0),
            event("nan", f64::NAN, 10.0),
            event("negative", -1.0, 10.0),
        ] {
            counter.record(&bad);
        }
        assert_eq!(counter.snapshot.responses, 1);
        assert_eq!(counter.snapshot.output_tokens, 100.0);
        assert_eq!(counter.snapshot.streaming_ms, 2000.0);
        let mut next = event("c", 50.0, 1000.0);
        next.measurements
            .as_mut()
            .unwrap()
            .insert(M::DispatchToNetworkTerminalMs, Some(9000.0));
        counter.record(&next);
        assert_eq!(counter.snapshot.streaming_ms, 3000.0);
        assert_eq!(counter.snapshot.output_tokens, 150.0);
    }

    #[test]
    fn throughput_registry_tracks_recorder_lifetime_and_new_generation() {
        let first = register("throughput-unit-lifetime");
        let generation = snapshot("throughput-unit-lifetime").unwrap().generation;
        let second = register("throughput-unit-lifetime");
        assert_ne!(
            snapshot("throughput-unit-lifetime").unwrap().generation,
            generation
        );
        drop(first);
        assert!(snapshot("throughput-unit-lifetime").is_some());
        drop(second);
        assert!(snapshot("throughput-unit-lifetime").is_none());
    }
    #[test]
    fn throughput_records_with_disk_monitoring_off_and_reaches_roster() {
        use crate::core::performance_monitor::PerformanceMonitor;
        use crate::modes::daemon::active_session_state::{ActiveSessionState, AgentSessionRuntime};
        use pi_agent_core::performance_metrics::PerformanceMetricRecorder;
        let dir = tempfile::tempdir().unwrap();
        let monitor = PerformanceMonitor::new(dir.path(), false);
        let recorder = monitor.recorder("throughput-monitor-off".into());
        recorder.record(event("a", 80.0, 2000.0));
        assert!(!monitor.directory().exists());
        let mut runtime = AgentSessionRuntime::default();
        runtime.session.session_id = "throughput-monitor-off".into();
        let state = Arc::new(Mutex::new(ActiveSessionState::new("active", runtime)));
        let row = crate::modes::daemon::daemon_session_list::summary_for_active_session(
            &state, None, false, false, false,
        );
        assert_eq!(row.extra["streamingThroughput"]["outputTokens"], 80.0);
        assert_eq!(row.extra["streamingThroughput"]["streamingMs"], 2000.0);
        assert_eq!(
            row.roster_view().extra["streamingThroughput"]["responses"],
            1
        );
    }

    #[test]
    fn throughput_never_falls_back_to_dispatch_or_total_duration() {
        let counter = register("throughput-no-fallback");
        let mut counter = counter.lock().unwrap();
        for (index, value) in [
            None,
            Some(0.0),
            Some(-1.0),
            Some(f64::NAN),
            Some(f64::INFINITY),
        ]
        .into_iter()
        .enumerate()
        {
            let mut sample = event(&format!("invalid-{index}"), 100.0, 500.0);
            let measurements = sample.measurements.as_mut().unwrap();
            measurements.insert(M::StreamingMs, value);
            measurements.insert(M::TotalMs, Some(10000.0));
            measurements.insert(M::DispatchToNetworkTerminalMs, Some(9000.0));
            counter.record(&sample);
        }
        let mut missing = event("missing-streaming", 100.0, 500.0);
        missing.measurements = Some(
            [
                (M::TotalMs, Some(10000.0)),
                (M::DispatchToNetworkTerminalMs, Some(9000.0)),
            ]
            .into_iter()
            .collect(),
        );
        counter.record(&missing);
        assert_eq!(counter.current().responses, 0);
        assert_eq!(counter.current().output_tokens, 0.0);
        assert_eq!(counter.current().streaming_ms, 0.0);
    }

    #[test]
    fn throughput_all_terminals_clear_estimates_and_reject_late_progress_without_usage() {
        let counter = register("throughput-all-terminals");
        let mut counter = counter.lock().unwrap();
        for (index, outcome) in [
            PerformanceMetricOutcome::Success,
            PerformanceMetricOutcome::Failure,
            PerformanceMetricOutcome::Cancelled,
            PerformanceMetricOutcome::Unavailable,
            PerformanceMetricOutcome::Timeout,
        ]
        .into_iter()
        .enumerate()
        {
            let id = format!("terminal-{index}");
            counter.progress(&id, 20.0, 500.0);
            let live_sequence = counter.current().sequence;
            let mut terminal = event(&id, 0.0, 500.0);
            terminal.outcome = Some(outcome);
            terminal.usage = None;
            counter.record(&terminal);
            let sample = counter.current();
            assert!(!sample.active && !sample.estimated);
            assert!(sample.sequence > live_sequence);
            assert_eq!(sample.output_tokens, 0.0);
            assert_eq!(sample.streaming_ms, 0.0);
            counter.progress(&id, 40.0, 1000.0);
            counter.record(&event(&id, 40.0, 1000.0));
            assert_eq!(counter.current().sequence, sample.sequence);
            assert_eq!(counter.current().responses, 0, "terminal cannot be revived");
        }
    }

    #[test]
    fn throughput_invalid_usage_and_timing_never_preserve_or_restore_estimates() {
        let counter = register("throughput-invalid-terminal");
        let mut counter = counter.lock().unwrap();
        let mut no_usage = event("no-output-usage", 1.0, 500.0);
        no_usage.usage.as_mut().unwrap().output_tokens = None;
        let mut estimated = event("estimated-source", 1.0, 500.0);
        estimated.usage.as_mut().unwrap().source = "estimated".into();
        let mut no_timing = event("no-timing", 1.0, 500.0);
        no_timing.measurements = None;
        for invalid in [
            no_usage,
            estimated,
            no_timing,
            event("zero-duration", 1.0, 0.0),
        ] {
            let id = invalid
                .correlation
                .as_ref()
                .unwrap()
                .provider_attempt_id
                .as_ref()
                .unwrap();
            counter.progress(id, 5.0, 100.0);
            counter.record(&invalid);
            counter.progress(id, 10.0, 200.0);
            assert!(!counter.current().active);
            assert!(!counter.current().estimated);
            assert_eq!(counter.current().responses, 0);
        }
    }

    #[test]
    fn throughput_sums_tokens_and_streaming_intervals_without_double_counting_reasoning() {
        let counter = register("throughput-weighted-totals");
        let mut counter = counter.lock().unwrap();
        let mut first = event("first", 100.0, 1000.0);
        let usage = first.usage.as_mut().unwrap();
        usage.reasoning_tokens = Some(60.0);
        usage.reasoning_included_in_output = Some(true);
        counter.record(&first);
        let mut second = event("second", 100.0, 9000.0);
        second.outcome = Some(PerformanceMetricOutcome::Cancelled);
        counter.progress("second", 200.0, 8000.0);
        counter.record(&second);
        counter.record(&second);
        assert!(!counter.current().active);
        assert_eq!(counter.current().responses, 2);
        assert_eq!(counter.current().output_tokens, 200.0);
        assert_eq!(counter.current().streaming_ms, 10000.0);
        assert_eq!(
            1000.0 * counter.current().output_tokens / counter.current().streaming_ms,
            20.0
        );
    }

    #[test]
    fn throughput_first_chunk_invalid_clock_and_retry_cannot_keep_old_estimates() {
        let counter = register("throughput-retry-estimate");
        let mut counter = counter.lock().unwrap();
        counter.progress("a", 2.0, 0.0);
        assert!(!counter.current().active);
        counter.progress("a", 4.0, 500.0);
        let sequence = counter.current().sequence;
        counter.progress("a", 4.0, 500.0);
        assert_eq!(
            counter.current().sequence,
            sequence,
            "duplicate progress is not a revision"
        );
        counter.progress("a", 8.0, f64::NAN);
        assert!(!counter.current().active);
        assert!(counter.current().sequence > sequence);
        counter.progress("a", 8.0, 700.0);
        counter.progress("b", 1.0, 0.0);
        assert!(
            !counter.current().active,
            "new attempt's first chunk clears stale pending data"
        );
        counter.progress("b", 2.0, 250.0);
        let mut started = event("c", 0.0, 0.0);
        started.outcome = Some(PerformanceMetricOutcome::Started);
        counter.record(&started);
        assert!(!counter.current().active);
        counter.progress("c", 3.0, 500.0);
        assert_eq!(counter.current().output_tokens, 3.0);
        assert_eq!(counter.current().streaming_ms, 500.0);
    }

    #[test]
    fn throughput_snapshot_uses_streaming_wire_field_only() {
        let snapshot = Snapshot {
            streaming_ms: 500.0,
            ..Default::default()
        };
        let json = serde_json::to_value(snapshot).unwrap();
        assert_eq!(json["streamingMs"], 500.0);
        assert!(json.get("generationMs").is_none());
    }
}
