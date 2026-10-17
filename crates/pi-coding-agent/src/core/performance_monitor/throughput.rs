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
    pub generation_ms: f64,
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
            sample.generation_ms += ms;
            sample.active = true;
            sample.estimated = true;
        }
        sample
    }

    pub fn progress(&mut self, id: &str, tokens: f64, ms: f64) {
        if id.is_empty()
            || !tokens.is_finite()
            || tokens <= 0.0
            || !ms.is_finite()
            || ms <= 0.0
            || self.seen.iter().any(|seen| seen == id)
        {
            return;
        }
        self.pending = Some((id.into(), tokens, ms));
        self.snapshot.sequence = self.snapshot.sequence.saturating_add(1);
    }

    pub fn record(&mut self, event: &PerformanceMetricEvent) {
        if event.operation != PerformanceMetricOperation::ProviderAttempt
            || !matches!(
                event.outcome,
                Some(
                    PerformanceMetricOutcome::Success
                        | PerformanceMetricOutcome::Failure
                        | PerformanceMetricOutcome::Cancelled
                )
            )
        {
            return;
        }
        let Some(id) = event
            .correlation
            .as_ref()
            .and_then(|c| c.provider_attempt_id.as_ref())
        else {
            return;
        };
        if self
            .pending
            .as_ref()
            .is_some_and(|(pending, _, _)| pending == id)
        {
            self.pending = None;
            self.snapshot.sequence = self.snapshot.sequence.saturating_add(1);
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
        // Provider dispatch-to-terminal duration includes response latency, but
        // excludes tool execution, idle time and queue/pre-dispatch wait.
        let Some(ms) = event
            .measurements
            .as_ref()
            .and_then(|values| {
                values
                    .get(&M::DispatchToNetworkTerminalMs)
                    .copied()
                    .flatten()
                    .or_else(|| values.get(&M::TotalMs).copied().flatten())
            })
            .filter(|n| n.is_finite() && *n > 0.0)
        else {
            return;
        };
        if self.seen.contains(id) {
            return;
        }
        let tokens = self.snapshot.output_tokens + tokens;
        let ms = self.snapshot.generation_ms + ms;
        if !tokens.is_finite() || !ms.is_finite() {
            return;
        }
        self.seen.push_back(id.clone());
        if self.seen.len() > 256 {
            self.seen.pop_front();
        }
        self.snapshot.output_tokens = tokens;
        self.snapshot.generation_ms = ms;
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
        event.measurements = Some([(M::TotalMs, Some(ms))].into_iter().collect());
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
        assert_eq!(final_sample.generation_ms, 150.0);
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
        assert_eq!(counter.snapshot.generation_ms, 2000.0);
        let mut next = event("c", 50.0, 2000.0);
        next.measurements
            .as_mut()
            .unwrap()
            .insert(M::DispatchToNetworkTerminalMs, Some(1000.0));
        counter.record(&next);
        assert_eq!(counter.snapshot.generation_ms, 3000.0);
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
        assert_eq!(row.extra["generationThroughput"]["outputTokens"], 80.0);
        assert_eq!(row.extra["generationThroughput"]["generationMs"], 2000.0);
        assert_eq!(
            row.roster_view().extra["generationThroughput"]["responses"],
            1
        );
    }
}
