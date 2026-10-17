//! Duration-weighted output TPS for one chat and its observed descendants.
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
    samples: BTreeMap<String, Snapshot>,
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
        self.supported = rows
            .iter()
            .any(|row| row.session_id == root && row.extra.contains_key("generationThroughput"));
        if !self.supported || root.is_empty() {
            self.samples.clear();
            self.family.clear();
            return;
        }
        self.family.insert(root.clone());
        // Index shared linkage keys once; each edge is visited at most once,
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
        let mut pending: VecDeque<String> = parent_keys(parent).into();
        for row in rows
            .iter()
            .filter(|row| self.family.contains(&row.session_id))
        {
            pending.extend(parent_keys(&RosterParent {
                active_session_id: row.active_session_id.clone(),
                session_id: Some(row.session_id.clone()),
                session_file: row.session_file.clone(),
            }));
        }
        while let Some(key) = pending.pop_front() {
            for row in children.remove(&key).unwrap_or_default() {
                if self.family.len() >= MAX_SESSIONS {
                    break;
                }
                if !row.session_id.is_empty() && self.family.insert(row.session_id.clone()) {
                    pending.extend(parent_keys(&RosterParent {
                        active_session_id: row.active_session_id.clone(),
                        session_id: Some(row.session_id.clone()),
                        session_file: row.session_file.clone(),
                    }));
                }
            }
        }
        for row in rows
            .iter()
            .filter(|row| self.family.contains(&row.session_id))
        {
            let Some(sample) = row
                .extra
                .get("generationThroughput")
                .and_then(|v| serde_json::from_value::<Snapshot>(v.clone()).ok())
            else {
                continue;
            };
            if sample.generation.is_empty()
                || !sample.output_tokens.is_finite()
                || sample.output_tokens < 0.0
                || !sample.generation_ms.is_finite()
                || sample.generation_ms < 0.0
            {
                continue;
            }
            // Absolute snapshots replace prior totals; repeated roster pushes never add twice.
            if self.samples.get(&row.session_id).is_some_and(|previous| {
                previous.generation == sample.generation
                    && (previous.responses > sample.responses
                        || previous.generation_ms > sample.generation_ms)
            }) {
                continue;
            }
            self.samples.insert(row.session_id.clone(), sample);
        }
    }
    fn average(&self) -> Option<f64> {
        if !self.supported {
            return None;
        }
        let (tokens, ms) = self
            .samples
            .values()
            .filter(|s| s.responses > 0 && s.generation_ms > 0.0)
            .fold((0.0, 0.0), |(tokens, ms), s| {
                (tokens + s.output_tokens, ms + s.generation_ms)
            });
        let rate = tokens / ms * 1000.0;
        (ms > 0.0 && rate.is_finite()).then_some(rate)
    }
    pub(super) fn indicator(&self) -> String {
        let value = self
            .average()
            .map(|rate| format!("{rate:.1}"))
            .unwrap_or_else(|| "—".into());
        format!("\x1b[1;38;5;196mAVG TPS {value}\x1b[22;39m")
    }
}

fn parent_keys(parent: &RosterParent) -> Vec<String> {
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
    use serde_json::json;
    fn row(id: &str, parent: Option<&str>, tokens: f64, ms: f64) -> SessionSummary {
        SessionSummary {
            session_id: id.into(),
            active_session_id: Some(id.into()),
            parent_session_id: parent.map(str::to_string),
            runtime_kind: Some(
                if parent.is_some() {
                    "subagent"
                } else {
                    "top-level"
                }
                .into(),
            ),
            extra: [(
                "generationThroughput".into(),
                json!({"generation":"g", "outputTokens":tokens, "generationMs":ms, "responses":1}),
            )]
            .into_iter()
            .collect(),
            ..Default::default()
        }
    }
    fn parent(id: &str) -> RosterParent {
        RosterParent {
            session_id: Some(id.into()),
            ..Default::default()
        }
    }
    #[test]
    fn throughput_weights_main_children_and_nested_children_and_deduplicates_roster() {
        let mut state = Throughput::default();
        let rows = vec![
            row("grandchild", Some("child"), 50.0, 1000.0),
            row("root", None, 100.0, 2000.0),
            row("child", Some("root"), 150.0, 3000.0),
            row("unrelated", None, 9000.0, 1.0),
            row("other-child", Some("unrelated"), 9000.0, 1.0),
        ];
        state.observe(&rows, &parent("root"));
        assert_eq!(state.average(), Some(50.0));
        state.observe(&rows, &parent("root"));
        assert_eq!(state.average(), Some(50.0));
        assert!(state.indicator().contains("\x1b[1;38;5;196mAVG TPS 50.0"));
        // Completed children disappearing from the live roster retain their measured contribution.
        state.observe(&[row("root", None, 200.0, 3000.0)], &parent("root"));
        assert!((state.average().unwrap() - 400.0 / 7.0).abs() < 1e-9);
    }
    #[test]
    fn throughput_missing_data_session_switch_and_worker_restart_are_safe() {
        let mut state = Throughput::default();
        assert!(state.indicator().contains("AVG TPS —"));
        state.observe(&[row("a", None, 100.0, 1000.0)], &parent("a"));
        assert_eq!(state.average(), Some(100.0));
        let mut restarted = row("a", None, 0.0, 0.0);
        restarted.extra.get_mut("generationThroughput").unwrap()["generation"] = json!("new");
        state.observe(&[restarted], &parent("a"));
        assert_eq!(state.average(), None);
        state.observe(&[row("b", None, 30.0, 1000.0)], &parent("b"));
        assert_eq!(state.average(), Some(30.0));
        state.observe(&[], &parent("b"));
        assert_eq!(state.average(), None);
    }
    #[test]
    fn throughput_is_a_weighted_average_not_sum_of_parallel_rates() {
        let mut state = Throughput::default();
        state.observe(
            &[
                row("a", None, 100.0, 1000.0),
                row("b", Some("a"), 30.0, 3000.0),
            ],
            &parent("a"),
        );
        assert_eq!(state.average(), Some(32.5));
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
            if width >= 40 {
                assert!(strip_ansi(&lines.join("\n")).contains("OCC2 ○  AVG TPS 50.0"));
                assert!(lines.join("\n").contains("\x1b[1;38;5;196mAVG TPS 50.0"));
            }
        }
    }

    #[test]
    fn throughput_rejects_stale_counters_and_invalid_samples() {
        let mut state = Throughput::default();
        state.observe(&[row("a", None, 100.0, 2000.0)], &parent("a"));
        state.observe(&[row("a", None, 80.0, 1000.0)], &parent("a"));
        assert_eq!(state.average(), Some(50.0));
        state.observe(&[row("a", None, -20.0, 2000.0)], &parent("a"));
        assert_eq!(state.average(), Some(50.0));
    }
}
