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
}

pub struct Counter {
    snapshot: Snapshot,
    seen: VecDeque<String>,
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
    let snapshot = counter
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .snapshot
        .clone();
    Some(snapshot)
}

impl Counter {
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
        let Some(id) = event
            .correlation
            .as_ref()
            .and_then(|c| c.provider_attempt_id.as_ref())
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
