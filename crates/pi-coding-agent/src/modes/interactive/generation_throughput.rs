//! Sum of each observed family session's duration-weighted streaming output TPS.
//!
//! Each absolute snapshot already weights that session's measured responses by
//! first-to-last output time. Keep completed observed descendants when they leave
//! the roster; reset only the restarted worker's sample or the selected chat.
//! Live overlays require a currently available worker row. Losing one falls back
//! to its last observed finalized baseline, never to the abandoned estimate.
use crate::core::performance_monitor::throughput::Snapshot;
use crate::modes::daemon::daemon_session_list::SessionSummary;
use crate::modes::interactive::components::subagent_summary_line::{
    linkage_row_for_roster_summary, RosterParent,
};
use std::collections::{BTreeMap, BTreeSet, VecDeque};

const MAX_SESSIONS: usize = 4096;
#[derive(Default)]
pub(super) struct Throughput {
    root: String,
    family: BTreeSet<String>,
    family_keys: BTreeSet<String>,
    // Latest accepted snapshots retain ordering even when their live overlay expires.
    samples: BTreeMap<String, Snapshot>,
    finalized: BTreeMap<String, Snapshot>,
    live_overlays: BTreeSet<String>,
    retired_generations: BTreeMap<String, BTreeSet<String>>,
    overflowed: bool,
    supported: bool,
}
impl Throughput {
    pub(super) fn observe(&mut self, rows: &[SessionSummary], parent: &RosterParent) {
        let root = parent.session_id.clone().unwrap_or_default();
        if self.root != root {
            *self = Self {
                root: root.clone(),
                ..Default::default()
            };
        }
        // Roster projection strips this metadata unless the daemon negotiated
        // StreamingThroughput. Never fall back to request-inclusive legacy data.
        self.supported = rows
            .iter()
            .any(|row| row.session_id == root && row.extra.contains_key("streamingThroughput"));
        if !self.supported || root.is_empty() {
            self.samples.clear();
            self.finalized.clear();
            self.live_overlays.clear();
            self.retired_generations.clear();
            self.family.clear();
            self.family_keys.clear();
            self.overflowed = false;
            return;
        }
        self.family.insert(root.clone());
        // Index shared linkage keys once; each key is visited at most once,
        // including nested descendants that precede their parents in the roster.
        let mut children: BTreeMap<String, Vec<&SessionSummary>> = BTreeMap::new();
        for row in rows
            .iter()
            .filter(|row| row.runtime_kind.as_deref() == Some("subagent"))
        {
            for key in crate::modes::agents_view::agents_view_state::get_parent_keys(
                &linkage_row_for_roster_summary(row),
            ) {
                children.entry(key).or_default().push(row);
            }
        }
        let mut pending: VecDeque<String> = self.family_keys.iter().cloned().collect();
        pending.extend(parent_keys(parent));
        for row in rows
            .iter()
            .filter(|row| self.family.contains(&row.session_id))
        {
            pending.extend(summary_keys(row));
        }
        let mut visited = BTreeSet::new();
        while let Some(key) = pending.pop_front() {
            if !visited.insert(key.clone()) {
                continue;
            }
            // Retain aliases so a later descendant can link through a completed
            // observed parent even after that parent's roster row disappears.
            self.family_keys.insert(key.clone());
            for row in children.remove(&key).unwrap_or_default() {
                if row.session_id.is_empty() {
                    continue;
                }
                if self.family.len() >= MAX_SESSIONS && !self.family.contains(&row.session_id) {
                    self.overflowed = true;
                    continue;
                }
                self.family.insert(row.session_id.clone());
                pending.extend(summary_keys(row));
            }
        }
        let updates: Vec<_> = rows
            .iter()
            .filter(|row| self.family.contains(&row.session_id))
            .filter_map(|row| parse_sample(row).map(|sample| (row, sample)))
            .collect();
        let available_live_rows: BTreeSet<_> = updates
            .iter()
            .filter(|(row, sample)| {
                row_allows_live_overlay(row) && (!sample.active || sample_rate(sample).is_some())
            })
            .map(|(row, _)| row.session_id.as_str())
            .collect();
        // Lifecycle and metadata loss can occur without a new counter sequence.
        // Expire overlays first; retaining the ordering watermark prevents replay
        // of that same snapshot from making a vanished estimate live again.
        self.live_overlays
            .retain(|id| available_live_rows.contains(id.as_str()));
        for (row, sample) in updates {
            if self
                .retired_generations
                .get(&row.session_id)
                .is_some_and(|retired| retired.contains(&sample.generation))
            {
                continue;
            }
            // Absolute snapshots replace totals; repeated or reordered pushes
            // never add twice. Sequence also orders final usage corrections.
            if let Some(previous) = self.samples.get(&row.session_id) {
                if previous.generation == sample.generation {
                    let stale = if previous.sequence > 0 || sample.sequence > 0 {
                        previous.sequence >= sample.sequence
                    } else {
                        previous.responses > sample.responses
                            || previous.streaming_ms > sample.streaming_ms
                            || (previous.responses == sample.responses
                                && previous.streaming_ms == sample.streaming_ms
                                && previous.output_tokens == sample.output_tokens
                                && previous.active == sample.active
                                && previous.estimated == sample.estimated)
                    };
                    if stale {
                        continue;
                    }
                } else {
                    self.retired_generations
                        .entry(row.session_id.clone())
                        .or_default()
                        .insert(previous.generation.clone());
                    self.finalized.remove(&row.session_id);
                    self.live_overlays.remove(&row.session_id);
                }
            }
            if sample.active && row_allows_live_overlay(row) && sample_rate(&sample).is_some() {
                self.live_overlays.insert(row.session_id.clone());
            } else {
                self.live_overlays.remove(&row.session_id);
            }
            if !sample.active && !sample.estimated && sample_rate(&sample).is_some() {
                self.finalized
                    .insert(row.session_id.clone(), sample.clone());
            }
            self.samples.insert(row.session_id.clone(), sample);
        }
    }
    fn contributions(&self) -> impl Iterator<Item = &Snapshot> {
        self.samples.iter().filter_map(|(id, latest)| {
            if self.live_overlays.contains(id) {
                Some(latest)
            } else {
                self.finalized.get(id)
            }
        })
    }
    fn total_rate(&self) -> Option<f64> {
        if !self.supported || self.overflowed {
            return None;
        }
        let mut rates = self.contributions().filter_map(sample_rate);
        let first = rates.next()?;
        let rate = rates.fold(first, |total, rate| total + rate);
        rate.is_finite().then_some(rate)
    }
    pub(super) fn session_rate(&self, id: &str) -> Option<(f64, bool)> {
        if !self.supported { return None; }
        let sample = if self.live_overlays.contains(id) {
            self.samples.get(id)
        } else {
            self.finalized.get(id)
        }?;
        Some((sample_rate(sample)?, sample.active && sample.estimated))
    }
    pub(super) fn indicator(&self) -> String {
        let value = self
            .total_rate()
            .map(|rate| {
                format!(
                    "{}{rate:.1}",
                    if self
                        .contributions()
                        .any(|s| s.active && s.estimated && sample_rate(s).is_some())
                    {
                        "~"
                    } else {
                        ""
                    }
                )
            })
            .unwrap_or_else(|| "—".into());
        format!("\x1b[1;38;5;196mSTREAM TPS {value}\x1b[22;39m")
    }
}

fn parse_sample(row: &SessionSummary) -> Option<Snapshot> {
    let value = row.extra.get("streamingThroughput")?;
    value.get("streamingMs")?.as_f64()?;
    let sample: Snapshot = serde_json::from_value(value.clone()).ok()?;
    (!sample.generation.is_empty()
        && sample.output_tokens.is_finite()
        && sample.output_tokens >= 0.0
        && sample.streaming_ms.is_finite()
        && sample.streaming_ms >= 0.0)
        .then_some(sample)
}

fn row_allows_live_overlay(row: &SessionSummary) -> bool {
    use crate::modes::daemon::agent_roster::AgentRosterStatus;
    // isStreaming, activity and taskState describe foreground work, not worker
    // liveness: a background helper can be idle in the roster while generating.
    row.active_session_id
        .as_deref()
        .is_some_and(|id| !id.is_empty())
        && row.lifecycle != "archived"
        && row.roster_status != Some(AgentRosterStatus::Inactive)
        && matches!(row.worker_state.as_deref(), None | Some("ready"))
        && !matches!(
            row.status_label.as_deref(),
            Some("failed" | "recovering" | "queued")
        )
}

fn sample_rate(sample: &Snapshot) -> Option<f64> {
    if !(sample.responses > 0 || sample.active) || sample.streaming_ms <= 0.0 {
        return None;
    }
    let rate = sample.output_tokens / sample.streaming_ms * 1000.0;
    rate.is_finite().then_some(rate)
}

pub(super) fn summary_keys(row: &SessionSummary) -> Vec<String> {
    parent_keys(&RosterParent {
        active_session_id: row.active_session_id.clone(),
        session_id: Some(row.session_id.clone()),
        session_file: row.session_file.clone(),
    })
}

pub(super) fn parent_keys(parent: &RosterParent) -> Vec<String> {
    let row = SessionSummary {
        parent_active_session_id: parent.active_session_id.clone(),
        parent_session_id: parent.session_id.clone(),
        parent_session_path: parent.session_file.clone(),
        ..Default::default()
    };
    crate::modes::agents_view::agents_view_state::get_parent_keys(&linkage_row_for_roster_summary(
        &row,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn row(id: &str, parent: Option<&str>, tokens: f64, ms: f64) -> SessionSummary {
        SessionSummary {
            lifecycle: "live".into(),
            activity: "idle".into(),
            session_id: id.into(),
            active_session_id: Some(id.into()),
            parent_session_id: parent.map(str::to_string),
            runtime_kind: Some(if parent.is_some() { "subagent" } else { "top-level" }.into()),
            extra: [(
                "streamingThroughput".into(),
                json!({"generation":"g", "outputTokens":tokens, "streamingMs":ms, "responses":1, "sequence":1}),
            )]
            .into_iter()
            .collect(),
            ..Default::default()
        }
    }
    fn sample(row: &mut SessionSummary) -> &mut Value {
        row.extra.get_mut("streamingThroughput").unwrap()
    }
    fn revision(mut row: SessionSummary, sequence: u64) -> SessionSummary {
        sample(&mut row)["sequence"] = json!(sequence);
        row
    }
    fn live(mut row: SessionSummary, sequence: u64) -> SessionSummary {
        row.is_session_active = true;
        row.is_streaming = true;
        row.activity = "working".into();
        sample(&mut row)["active"] = json!(true);
        sample(&mut row)["estimated"] = json!(true);
        revision(row, sequence)
    }
    fn parent(id: &str) -> RosterParent {
        RosterParent {
            session_id: Some(id.into()),
            ..Default::default()
        }
    }

    #[test]
    fn throughput_survives_actual_ui_roster_projection_and_streams_before_completion() {
        use crate::modes::agents_view::agents_view_state::SessionSummary as UiSummary;
        let mut row = row("a", None, 10.0, 1000.0);
        sample(&mut row)["responses"] = json!(0);
        sample(&mut row)["active"] = json!(true);
        sample(&mut row)["estimated"] = json!(true);
        let project = |row: &SessionSummary| {
            let wire = serde_json::to_value(row).unwrap();
            let ui: UiSummary = serde_json::from_value(
                crate::modes::agents_view::native_wire::normalize_browser_numbers(wire),
            )
            .unwrap();
            serde_json::from_value::<SessionSummary>(serde_json::to_value(ui).unwrap()).unwrap()
        };
        let mut state = Throughput::default();
        state.observe(&[project(&row)], &parent("a"));
        assert_eq!(state.total_rate(), Some(10.0));
        assert!(state.indicator().contains("STREAM TPS ~10.0"));
        sample(&mut row)["outputTokens"] = json!(40.0);
        sample(&mut row)["streamingMs"] = json!(2000.0);
        sample(&mut row)["sequence"] = json!(2);
        state.observe(&[project(&row)], &parent("a"));
        assert_eq!(state.total_rate(), Some(20.0));
        let pending = project(&row);
        sample(&mut row)["active"] = json!(false);
        sample(&mut row)["estimated"] = json!(false);
        sample(&mut row)["responses"] = json!(1);
        sample(&mut row)["outputTokens"] = json!(30.0);
        sample(&mut row)["streamingMs"] = json!(1500.0);
        sample(&mut row)["sequence"] = json!(3);
        state.observe(&[project(&row)], &parent("a"));
        assert!(state.indicator().contains("STREAM TPS 20.0"));
        // A late live estimate must not restore the tilde after final usage.
        state.observe(&[pending], &parent("a"));
        assert!(state.indicator().contains("STREAM TPS 20.0"));
    }

    #[test]
    fn throughput_ten_agents_at_300_tps_sum_to_3000_not_300() {
        let mut state = Throughput::default();
        let mut rows = vec![row("root", None, 0.0, 0.0)];
        for i in 1..=10 {
            rows.push(row(
                &format!("child-{i}"),
                Some("root"),
                300.0 * i as f64,
                1000.0 * i as f64,
            ));
        }
        state.observe(&rows, &parent("root"));
        assert_eq!(state.total_rate(), Some(3000.0));
        assert!(state.indicator().contains("STREAM TPS 3000.0"));
        // The selected chat is also one measured agent, never a multiplier.
        rows.pop();
        rows[0] = revision(row("root", None, 600.0, 2000.0), 2);
        state = Throughput::default();
        state.observe(&rows, &parent("root"));
        assert_eq!(state.total_rate(), Some(3000.0));
    }

    #[test]
    fn throughput_family_cap_is_inclusive_and_overflow_stays_unavailable_until_reset() {
        let mut state = Throughput::default();
        let mut rows = vec![row("root", None, 100.0, 1000.0)];
        for i in 1..MAX_SESSIONS {
            rows.push(row(&format!("child-{i}"), Some("root"), 100.0, 1000.0));
        }
        // Exactly the cap is complete, including duplicate roster rows.
        rows.push(rows[1].clone());
        state.observe(&rows, &parent("root"));
        assert_eq!(state.total_rate(), Some(MAX_SESSIONS as f64 * 100.0));
        assert!(!state.overflowed);
        rows.push(row("one-too-many", Some("root"), 100.0, 1000.0));
        state.observe(&rows, &parent("root"));
        assert!(state.overflowed);
        assert_eq!(state.family.len(), MAX_SESSIONS);
        assert_eq!(state.total_rate(), None);
        assert!(state.indicator().contains("STREAM TPS —"));
        state.observe(&[rows[0].clone()], &parent("root"));
        assert_eq!(
            state.total_rate(),
            None,
            "missing rows must not restore a partial sum"
        );
        state.observe(&[row("other", None, 200.0, 1000.0)], &parent("other"));
        assert!(!state.overflowed);
        assert_eq!(state.total_rate(), Some(200.0));
    }

    #[test]
    fn throughput_sums_own_duration_weighted_rates_not_pooled_tokens_or_roster_count() {
        let mut state = Throughput::default();
        state.observe(
            &[
                row("a", None, 100.0, 1000.0),
                row("b", Some("a"), 30.0, 3000.0),
                row("c", Some("a"), 0.0, 0.0),
            ],
            &parent("a"),
        );
        assert_eq!(state.total_rate(), Some(110.0));
        // Two responses in a now total 160 tokens over 4s: a=40, b=10.
        let mut updated = revision(row("a", None, 160.0, 4000.0), 2);
        sample(&mut updated)["responses"] = json!(2);
        state.observe(&[updated], &parent("a"));
        assert_eq!(state.total_rate(), Some(50.0));
    }

    #[test]
    fn throughput_nested_duplicate_unrelated_and_fork_rows_are_order_independent() {
        let mut fork = row("fork", Some("root"), 9000.0, 1.0);
        fork.runtime_kind = Some("top-level".into());
        let mut rows = vec![
            row("grandchild", Some("child"), 90.0, 1000.0),
            row("root", None, 100.0, 2000.0),
            row("child", Some("root"), 150.0, 3000.0),
            row("child", Some("root"), 150.0, 3000.0),
            row("unrelated", None, 9000.0, 1.0),
            row("other-child", Some("unrelated"), 9000.0, 1.0),
            fork,
            row("fork-child", Some("fork"), 9000.0, 1.0),
        ];
        let mut state = Throughput::default();
        for _ in 0..rows.len() {
            rows.rotate_left(1);
            let mut reordered = Throughput::default();
            reordered.observe(&rows, &parent("root"));
            assert_eq!(reordered.total_rate(), Some(190.0));
            state.observe(&rows, &parent("root"));
            assert_eq!(state.total_rate(), Some(190.0));
            assert_eq!(state.samples.len(), 3);
        }
        // Completed observed descendants retain their own rates, not a pooled denominator.
        state.observe(
            &[revision(row("root", None, 200.0, 3000.0), 2)],
            &parent("root"),
        );
        assert!((state.total_rate().unwrap() - (200.0 / 3.0 + 50.0 + 90.0)).abs() < 1e-9);
        // Selecting a child must not include its ancestor or former siblings.
        state.observe(&rows, &parent("child"));
        assert_eq!(state.total_rate(), Some(140.0));
    }

    #[test]
    fn throughput_retains_aliases_for_late_descendants_of_completed_observed_parents() {
        let mut state = Throughput::default();
        let root = row("root", None, 100.0, 1000.0);
        let mut child = row("child", Some("root"), 200.0, 1000.0);
        child.active_session_id = Some("child-runtime".into());
        child.session_file = Some("C:/sessions/child.jsonl".into());
        let mut grandchild = row("grandchild", Some("not-a-session-id"), 300.0, 1000.0);
        grandchild.parent_active_session_id = Some("child-runtime".into());
        let mut other_grandchild = row("other-grandchild", Some("not-a-session-id"), 400.0, 1000.0);
        other_grandchild.parent_session_path = child.session_file.clone();
        state.observe(&[root.clone(), child], &parent("root"));
        state.observe(&[other_grandchild, grandchild, root], &parent("root"));
        assert_eq!(state.total_rate(), Some(1000.0));
    }

    #[test]
    fn throughput_restart_resets_only_its_session_and_rejects_retired_generations() {
        let mut state = Throughput::default();
        let old = revision(row("a", None, 100.0, 1000.0), 10);
        state.observe(
            &[old.clone(), row("b", Some("a"), 300.0, 1000.0)],
            &parent("a"),
        );
        assert_eq!(state.total_rate(), Some(400.0));
        let mut restarted = revision(row("a", None, 0.0, 0.0), 0);
        sample(&mut restarted)["generation"] = json!("new");
        state.observe(&[restarted.clone()], &parent("a"));
        assert_eq!(state.total_rate(), Some(300.0));
        state.observe(&[revision(old, 99)], &parent("a"));
        assert_eq!(state.total_rate(), Some(300.0));
        sample(&mut restarted)["outputTokens"] = json!(50.0);
        sample(&mut restarted)["streamingMs"] = json!(1000.0);
        sample(&mut restarted)["sequence"] = json!(1);
        state.observe(&[restarted], &parent("a"));
        assert_eq!(state.total_rate(), Some(350.0));
        state.observe(&[row("c", None, 30.0, 1000.0)], &parent("c"));
        assert_eq!(state.total_rate(), Some(30.0));
        state.observe(&[], &parent("c"));
        assert_eq!(state.total_rate(), None);
    }

    #[test]
    fn throughput_unsupported_or_legacy_daemon_is_honestly_unavailable() {
        let mut state = Throughput::default();
        assert!(state.indicator().contains("STREAM TPS —"));
        let mut legacy = row("a", None, 100.0, 1000.0);
        legacy.extra.remove("streamingThroughput");
        legacy.extra.insert(
            "generationThroughput".into(),
            json!({
                "generation":"old", "outputTokens":100, "generationMs":1000, "responses":1,
            }),
        );
        state.observe(&[legacy.clone()], &parent("a"));
        assert_eq!(state.total_rate(), None);
        state.observe(&[row("a", None, 100.0, 1000.0)], &parent("a"));
        assert_eq!(state.total_rate(), Some(100.0));
        // Losing the capability must not leave the last good value on screen.
        state.observe(&[legacy], &parent("a"));
        assert!(state.indicator().contains("STREAM TPS —"));
        assert!(state.samples.is_empty());
    }

    #[test]
    fn throughput_unmeasurable_sessions_do_not_contribute_or_mark_total_estimated() {
        let mut state = Throughput::default();
        let root = row("a", None, 100.0, 1000.0);
        let mut unmeasurable = row("b", Some("a"), 50.0, 0.0);
        sample(&mut unmeasurable)["active"] = json!(true);
        sample(&mut unmeasurable)["estimated"] = json!(true);
        state.observe(&[root.clone(), unmeasurable.clone()], &parent("a"));
        assert_eq!(state.total_rate(), Some(100.0));
        assert!(!state.indicator().contains('~'));
        sample(&mut unmeasurable)["streamingMs"] = json!(1000.0);
        unmeasurable = revision(unmeasurable, 2);
        state.observe(&[root.clone(), unmeasurable.clone()], &parent("a"));
        assert_eq!(state.total_rate(), Some(150.0));
        assert!(state.indicator().contains("STREAM TPS ~150.0"));
        sample(&mut unmeasurable)["active"] = json!(false);
        unmeasurable = revision(unmeasurable, 3);
        state.observe(&[root, unmeasurable], &parent("a"));
        assert!(!state.indicator().contains('~'));
        let mut zero = Throughput::default();
        zero.observe(&[row("a", None, 100.0, 0.0)], &parent("a"));
        assert_eq!(zero.total_rate(), None);
        assert!(zero.indicator().contains("STREAM TPS —"));
    }

    #[test]
    fn throughput_rejects_stale_duplicate_and_invalid_samples_without_nan_or_infinity() {
        let mut state = Throughput::default();
        let current = revision(row("a", None, 100.0, 2000.0), 3);
        state.observe(&[current], &parent("a"));
        for bad in [
            revision(row("a", None, 80.0, 1000.0), 2),
            revision(row("a", None, 80.0, 1000.0), 3),
            revision(row("a", None, -20.0, 2000.0), 4),
            revision(row("a", None, 20.0, -2000.0), 4),
        ] {
            state.observe(&[bad], &parent("a"));
            assert_eq!(state.total_rate(), Some(50.0));
        }
        let mut invalid = revision(row("a", None, 20.0, 2000.0), 4);
        for value in [Value::Null, json!("NaN"), json!(-1)] {
            sample(&mut invalid)["streamingMs"] = value;
            state.observe(&[invalid.clone()], &parent("a"));
            assert_eq!(state.total_rate(), Some(50.0));
        }
        sample(&mut invalid)
            .as_object_mut()
            .unwrap()
            .remove("streamingMs");
        sample(&mut invalid)["generationMs"] = json!(2000);
        state.observe(&[invalid], &parent("a"));
        assert_eq!(state.total_rate(), Some(50.0));
        // A finite but overflowing individual rate also contributes nothing.
        state.observe(
            &[
                revision(row("a", None, 100.0, 2000.0), 3),
                row("overflow", Some("a"), f64::MAX, f64::MIN_POSITIVE),
            ],
            &parent("a"),
        );
        assert_eq!(state.total_rate(), Some(50.0));
        assert!(!state.indicator().contains("NaN"));
        assert!(!state.indicator().contains("inf"));
    }

    #[test]
    fn throughput_live_only_child_disappears_without_contributing_or_reviving_on_replay() {
        for sequence in [0, 2] {
            let mut state = Throughput::default();
            let root = row("root", None, 0.0, 0.0);
            let child = live(row("child", Some("root"), 300.0, 1000.0), sequence);
            state.observe(&[root.clone(), child.clone()], &parent("root"));
            assert_eq!(state.total_rate(), Some(300.0));
            state.observe(&[root.clone()], &parent("root"));
            assert_eq!(state.total_rate(), None);
            assert!(state.indicator().contains("STREAM TPS —"));
            // Preserve the high-water mark even though no baseline exists.
            state.observe(&[root.clone(), child.clone()], &parent("root"));
            assert_eq!(state.total_rate(), None);
            let newer = revision(child.clone(), sequence + 1);
            state.observe(&[root.clone(), newer], &parent("root"));
            assert_eq!(state.total_rate(), Some(300.0));
            let final_row = revision(row("child", Some("root"), 250.0, 1000.0), sequence + 2);
            state.observe(&[root.clone(), final_row], &parent("root"));
            assert_eq!(state.total_rate(), Some(250.0));
            assert!(!state.indicator().contains('~'));
            state.observe(&[root.clone()], &parent("root"));
            assert_eq!(state.total_rate(), Some(250.0));
            state.observe(&[root, child], &parent("root"));
            assert_eq!(state.total_rate(), Some(250.0));
            assert!(!state.indicator().contains('~'));
        }
    }

    #[test]
    fn throughput_disappearing_live_child_rolls_back_to_its_finalized_baseline() {
        let mut state = Throughput::default();
        let root = row("root", None, 50.0, 1000.0);
        let baseline = row("child", Some("root"), 100.0, 1000.0);
        state.observe(&[root.clone(), baseline], &parent("root"));
        let child = live(row("child", Some("root"), 400.0, 2000.0), 2);
        state.observe(&[root.clone(), child.clone()], &parent("root"));
        assert_eq!(state.total_rate(), Some(250.0));
        assert!(state.indicator().contains('~'));
        state.observe(&[root.clone()], &parent("root"));
        assert_eq!(state.total_rate(), Some(150.0));
        assert!(!state.indicator().contains('~'));
        // Replayed live sequence cannot overwrite the fallback's ordering.
        state.observe(&[root.clone(), child], &parent("root"));
        assert_eq!(state.total_rate(), Some(150.0));
        let final_row = revision(row("child", Some("root"), 500.0, 2000.0), 3);
        state.observe(&[root.clone(), final_row], &parent("root"));
        assert_eq!(state.total_rate(), Some(300.0));
        state.observe(&[root], &parent("root"));
        assert_eq!(state.total_rate(), Some(300.0));
        assert!(!state.indicator().contains('~'));
    }

    #[test]
    fn throughput_missing_legacy_or_malformed_child_metadata_expires_only_live_overlay() {
        for with_baseline in [false, true] {
            for missing in ["absent", "legacy", "malformed", "unmeasurable"] {
                let mut state = Throughput::default();
                let root = row("root", None, 0.0, 0.0);
                if with_baseline {
                    state.observe(
                        &[root.clone(), row("child", Some("root"), 200.0, 1000.0)],
                        &parent("root"),
                    );
                }
                let child = live(row("child", Some("root"), 600.0, 2000.0), 2);
                state.observe(&[root.clone(), child.clone()], &parent("root"));
                assert_eq!(state.total_rate(), Some(300.0));
                let mut unavailable = child.clone();
                unavailable.extra.remove("streamingThroughput");
                if missing == "legacy" {
                    unavailable.extra.insert("generationThroughput".into(), json!({
                        "generation":"g", "generationMs":2000, "outputTokens":600, "active":true,
                    }));
                } else if missing == "malformed" {
                    unavailable.extra.insert("streamingThroughput".into(), json!({
                        "generation":"g", "streamingMs":"invalid", "outputTokens":600, "active":true,
                    }));
                } else if missing == "unmeasurable" {
                    unavailable = child.clone();
                    sample(&mut unavailable)["streamingMs"] = json!(0);
                }
                state.observe(&[root.clone(), unavailable], &parent("root"));
                let expected = with_baseline.then_some(200.0);
                assert_eq!(state.total_rate(), expected);
                assert!(!state.indicator().contains('~'));
                state.observe(&[root, child], &parent("root"));
                assert_eq!(
                    state.total_rate(),
                    expected,
                    "same-sequence replay must not restore the overlay"
                );
            }
        }
    }

    #[test]
    fn throughput_inactive_or_failed_row_expires_overlay_before_same_sequence_rejection() {
        use crate::modes::daemon::agent_roster::AgentRosterStatus;
        let root = row("root", None, 0.0, 0.0);
        let child = live(row("child", Some("root"), 600.0, 2000.0), 2);
        let mut inactive_rows = Vec::new();
        // passivated_worker_roster_entry keeps the old throughput metadata while
        // clearing residency/streaming flags; its counter sequence does not move.
        let mut passivated = child.clone();
        passivated.active_session_id = None;
        passivated.is_streaming = false;
        passivated.is_session_active = false;
        passivated.roster_status = Some(AgentRosterStatus::Inactive);
        inactive_rows.push(passivated);
        let mut nonresident = child.clone();
        nonresident.active_session_id = None;
        inactive_rows.push(nonresident);
        let mut archived = child.clone();
        archived.lifecycle = "archived".into();
        inactive_rows.push(archived);
        let mut inactive = child.clone();
        inactive.roster_status = Some(AgentRosterStatus::Inactive);
        inactive_rows.push(inactive);
        for worker_state in ["starting", "stopping", "recovering", "failed"] {
            let mut stopped = child.clone();
            stopped.worker_state = Some(worker_state.into());
            inactive_rows.push(stopped);
        }
        for status_label in ["recovering", "failed", "queued"] {
            let mut stopped = child.clone();
            stopped.status_label = Some(status_label.into());
            inactive_rows.push(stopped);
        }
        for stopped in inactive_rows {
            for with_baseline in [false, true] {
                let mut state = Throughput::default();
                if with_baseline {
                    state.observe(
                        &[root.clone(), row("child", Some("root"), 200.0, 1000.0)],
                        &parent("root"),
                    );
                }
                state.observe(&[root.clone(), child.clone()], &parent("root"));
                assert_eq!(state.total_rate(), Some(300.0));
                state.observe(&[root.clone(), stopped.clone()], &parent("root"));
                let expected = with_baseline.then_some(200.0);
                assert_eq!(state.total_rate(), expected);
                assert!(!state.indicator().contains('~'));
                state.observe(&[root.clone(), child.clone()], &parent("root"));
                assert_eq!(
                    state.total_rate(),
                    expected,
                    "stale live row must not undo lifecycle invalidation"
                );
                // A newer counter on an explicitly dead worker is a watermark,
                // never a live estimate or a finalized baseline.
                state.observe(
                    &[root.clone(), revision(stopped.clone(), 3)],
                    &parent("root"),
                );
                state.observe(&[root.clone(), revision(child.clone(), 3)], &parent("root"));
                assert_eq!(state.total_rate(), expected);
                state.observe(&[root.clone(), revision(child.clone(), 4)], &parent("root"));
                assert_eq!(state.total_rate(), Some(300.0));
                let mut final_row = revision(stopped.clone(), 5);
                sample(&mut final_row)["active"] = json!(false);
                sample(&mut final_row)["estimated"] = json!(false);
                state.observe(&[root.clone(), final_row], &parent("root"));
                state.observe(&[root.clone()], &parent("root"));
                assert_eq!(
                    state.total_rate(),
                    Some(300.0),
                    "finalized history survives inactive lifecycle"
                );
                assert!(!state.indicator().contains('~'));
            }
        }
    }

    #[test]
    fn throughput_idle_background_helper_is_not_a_stopped_worker() {
        use crate::modes::daemon::agent_roster::AgentRosterStatus;
        let mut state = Throughput::default();
        let root = row("root", None, 0.0, 0.0);
        let mut helper = live(row("helper", Some("root"), 300.0, 1000.0), 2);
        helper.is_streaming = false;
        helper.activity = "idle".into();
        helper.task_state = Some("completed".into());
        helper.status_label = Some("background helper".into());
        helper.roster_status = Some(AgentRosterStatus::Idle);
        helper.worker_state = Some("ready".into());
        state.observe(&[root.clone(), helper.clone()], &parent("root"));
        assert_eq!(state.total_rate(), Some(300.0));
        state.observe(&[root, helper], &parent("root"));
        assert!(state.indicator().contains("STREAM TPS ~300.0"));
    }

    #[test]
    fn throughput_worker_generation_reset_clears_old_finalized_baseline_and_overlay() {
        let mut state = Throughput::default();
        let root = row("root", None, 0.0, 0.0);
        state.observe(
            &[root.clone(), row("child", Some("root"), 200.0, 1000.0)],
            &parent("root"),
        );
        let old_live = live(row("child", Some("root"), 600.0, 2000.0), 2);
        state.observe(&[root.clone(), old_live.clone()], &parent("root"));
        let mut restarted = live(row("child", Some("root"), 400.0, 1000.0), 1);
        sample(&mut restarted)["generation"] = json!("new");
        state.observe(&[root.clone(), restarted.clone()], &parent("root"));
        assert_eq!(state.total_rate(), Some(400.0));
        state.observe(&[root.clone()], &parent("root"));
        assert_eq!(
            state.total_rate(),
            None,
            "old-generation baseline must not survive restart"
        );
        state.observe(&[root.clone(), revision(old_live, 99)], &parent("root"));
        assert_eq!(state.total_rate(), None);
        sample(&mut restarted)["active"] = json!(false);
        sample(&mut restarted)["estimated"] = json!(false);
        restarted = revision(restarted, 2);
        state.observe(&[root.clone(), restarted], &parent("root"));
        assert_eq!(state.total_rate(), Some(400.0));
        state.observe(&[root], &parent("root"));
        assert_eq!(state.total_rate(), Some(400.0));
        state.observe(&[row("other", None, 50.0, 1000.0)], &parent("other"));
        assert_eq!(state.total_rate(), Some(50.0));
        assert_eq!(state.finalized.len(), 1);
        assert!(state.live_overlays.is_empty());
    }

    #[test]
    fn throughput_red_indicator_follows_occ2_and_fits_narrow_terminals() {
        use crate::modes::interactive::components::subagent_summary_line::{
            RightStatus, SubagentSummaryLine,
        };
        use pi_tui::{
            tui::Component,
            utils::{strip_ansi, visible_width},
        };
        crate::modes::interactive::theme::theme::init_theme(Some("dark"), false);
        let mut state = Throughput::default();
        state.observe(&[row("a", None, 100.0, 2000.0)], &parent("a"));
        let mut line = SubagentSummaryLine::default();
        line.set_always_visible(true);
        line.set_communication_status(Some(format!(
            "TXT ○  OCC1 ○  OCC2 ○  {}",
            state.indicator()
        )));
        line.set_right_status(Some(RightStatus {
            full: "Jev active".into(),
            compact: "Jev on".into(),
            minimal: "Jev".into(),
        }));
        for width in [2, 5, 20, 40, 80, 120] {
            let lines = line.render(width as f64);
            assert!(lines.iter().all(|line| visible_width(line) <= width));
            // PR #174 renamed "AVG TPS " to "STREAM TPS " (+3 visible columns), so the
            // full below-line needs inner >= visible+1, i.e. width >= 41. The documented
            // rule lives in subagent_summary_line.rs.
            if width >= 41 {
                assert!(strip_ansi(&lines.join("\n")).contains("OCC2 ○  STREAM TPS 50.0"));
                assert!(lines.join("\n").contains("\x1b[1;38;5;196mSTREAM TPS 50.0"));
            }
        }
    }
}
