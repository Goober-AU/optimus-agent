//! Monotonic, session-scoped pulses over optional content-free roster metadata.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::time::{Duration, Instant};

use crate::core::occ::activity::{CommunicationActivity, CommunicationKind, MAX_ACTIVITY_MESSAGES};
use crate::modes::daemon::daemon_session_list::SessionSummary;
use crate::modes::interactive::components::subagent_summary_line::{linkage_row_for_roster_summary, RosterParent};

pub(super) const PULSE: Duration = Duration::from_millis(700);
const MAX_RECEIVERS: usize = 256;
const MAX_SEEN: usize = 256;

#[derive(Default)]
struct Receiver {
    generation: String,
    seen: VecDeque<String>,
}

#[derive(Default)]
pub(super) struct ActivityState {
    identity: (String, Option<String>, u64),
    initialized: bool,
    receivers: BTreeMap<String, Receiver>,
    until: [Option<Instant>; 2],
    supported: bool,
}

impl ActivityState {
    pub(super) fn observe(&mut self, rows: &[SessionSummary], parent: &RosterParent, now: Instant) -> bool {
        let epoch = rows.first().and_then(|row| row.extra.get("communicationObservationEpoch"))
            .and_then(|value| value.as_u64()).unwrap_or(0);
        let identity = (parent.session_id.clone().unwrap_or_default(), parent.active_session_id.clone(), epoch);
        if self.identity != identity {
            *self = Self { identity, ..Self::default() };
        }
        let is_child = |row: &&SessionSummary| row.runtime_kind.as_deref() == Some("subagent")
            && row.lifecycle == "live"
            && crate::modes::agents_view::agents_view_state::is_direct_agent_child(
                &linkage_row_for_roster_summary(row), parent.active_session_id.as_deref(),
                parent.session_id.as_deref(), parent.session_file.as_deref(),
            );
        let children: BTreeSet<&str> = rows.iter().filter(is_child).map(|row| row.session_id.as_str()).collect();
        let parent_id = parent.session_id.as_deref().unwrap_or("");
        let family = |id: &str| id == parent_id || children.contains(id);
        let mut received = BTreeSet::new();
        let mut pulse = false;
        self.supported = false;
        for row in rows.iter().filter(|row| family(&row.session_id)).take(MAX_RECEIVERS) {
            let Some(activity) = row.extra.get("communicationActivity")
                .and_then(|value| serde_json::from_value::<CommunicationActivity>(value.clone()).ok()) else { continue; };
            self.supported = true;
            received.insert(row.session_id.clone());
            let previous = self.receivers.get(&row.session_id);
            // New or recovered receivers establish a baseline, including their history.
            let baseline = !self.initialized || previous.is_none_or(|state| state.generation != activity.generation);
            let receiver = self.receivers.entry(row.session_id.clone()).or_default();
            if receiver.generation != activity.generation {
                receiver.generation = activity.generation;
                receiver.seen.clear();
            }
            for delivery in activity.deliveries.iter().rev().take(MAX_ACTIVITY_MESSAGES).rev() {
                if receiver.seen.contains(&delivery.id) { continue; }
                receiver.seen.push_back(delivery.id.clone());
                while receiver.seen.len() > MAX_SEEN { receiver.seen.pop_front(); }
                if baseline || !family(&delivery.source_session_id)
                    || (!children.contains(row.session_id.as_str()) && !children.contains(delivery.source_session_id.as_str())) {
                    continue;
                }
                let index = match delivery.kind {
                    CommunicationKind::Text => 0,
                    CommunicationKind::Occ1 => 1,
                    // SYM/LAT placeholders stay hidden until their transports exist.
                    CommunicationKind::Symbolic | CommunicationKind::Latent => continue,
                };
                self.until[index] = Some(now + PULSE);
                pulse = true;
            }
        }
        self.receivers.retain(|id, _| received.contains(id));
        if !self.supported { self.until = [None; 2]; }
        self.initialized = self.supported;
        pulse
    }

    pub(super) fn next_deadline(&self, now: Instant) -> Option<Instant> {
        self.until.iter().flatten().copied().filter(|deadline| *deadline > now).min()
    }

    pub(super) fn indicators(&self, now: Instant) -> String {
        ["TXT", "OCC"].iter().enumerate().map(|(index, label)| {
            let active = self.until[index].is_some_and(|until| now < until);
            let marker = if !self.supported { "-" } else if active { "●" } else { "○" };
            let intensity = if active { "1" } else { "2" };
            // ANSI 256-color 39 is neon blue and works without truecolor support.
            format!("\x1b[{intensity};38;5;39m{label} {marker}\x1b[22;39m")
        }).collect::<Vec<_>>().join("  ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use pi_tui::utils::strip_ansi;

    fn row(id: &str, child: bool, messages: serde_json::Value) -> SessionSummary {
        SessionSummary {
            id: id.into(), session_id: id.into(), lifecycle: "live".into(),
            active_session_id: Some(id.into()), runtime_kind: Some(if child { "subagent" } else { "top-level" }.into()),
            parent_session_id: child.then(|| "parent".into()),
            extra: std::collections::HashMap::from([
                ("communicationActivity".into(), json!({"generation":"g", "deliveries":messages})),
                ("communicationObservationEpoch".into(), json!(1)),
            ]), ..Default::default()
        }
    }
    fn message(id: &str, from: &str, kind: &str) -> serde_json::Value {
        json!({"id":id, "sourceSessionId":from, "kind":kind, "timestamp":0})
    }
    fn parent() -> RosterParent { RosterParent { session_id: Some("parent".into()), ..Default::default() } }

    #[test]
    fn communication_pulses_on_new_deliveries_expires_and_coalesces_bursts() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        let baseline = row("child", true, json!([message("old", "parent", "text")]));
        assert!(!state.observe(&[baseline], &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "TXT ○  OCC ○");
        let fresh = row("child", true, json!([message("new", "parent", "text")]));
        assert!(state.observe(&[fresh.clone()], &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "TXT ●  OCC ○");
        assert!(!state.observe(&[fresh], &parent(), now + Duration::from_millis(500)));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE)), "TXT ○  OCC ○");
        assert!(state.observe(&[row("child", true, json!([message("burst", "parent", "occ1")]))], &parent(), now + PULSE));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE)), "TXT ○  OCC ●");
        assert!(state.observe(&[row("child", true, json!([message("burst2", "parent", "occ1")]))], &parent(), now + PULSE + Duration::from_millis(500)));
        assert!(strip_ansi(&state.indicators(now + PULSE * 2)).contains("OCC ●"));
    }

    #[test]
    fn communication_family_scope_includes_siblings_and_child_to_parent_but_not_other_sessions() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        let mut rows = vec![row("parent", false, json!([])), row("a", true, json!([])), row("b", true, json!([]))];
        state.observe(&rows, &parent(), now);
        rows[2] = row("b", true, json!([message("1", "a", "occ1")]));
        assert!(state.observe(&rows, &parent(), now));
        rows[0] = row("parent", false, json!([message("2", "b", "text")]));
        assert!(state.observe(&rows, &parent(), now));
        rows[1] = row("a", true, json!([message("3", "unrelated", "text"), message("4", "parent", "symbolic"), message("5", "parent", "latent")]));
        assert!(!state.observe(&rows, &parent(), now + PULSE));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE)), "TXT ○  OCC ○");
    }

    #[test]
    fn communication_history_reconnect_and_session_changes_never_flash() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        let mut rows = vec![row("child", true, json!([]))];
        state.observe(&rows, &parent(), now);
        rows[0] = row("child", true, json!([message("1", "parent", "text")]));
        rows[0].extra.insert("communicationObservationEpoch".into(), json!(2));
        assert!(!state.observe(&rows, &parent(), now));
        rows[0].extra.get_mut("communicationActivity").unwrap()["generation"] = json!("new-runtime");
        assert!(!state.observe(&rows, &parent(), now));
        let other = RosterParent { session_id: Some("other".into()), ..Default::default() };
        assert!(!state.observe(&rows, &other, now));
        assert_eq!(strip_ansi(&state.indicators(now)), "TXT -  OCC -");
        assert!(!state.observe(&rows, &parent(), now));
        rows[0].extra.remove("communicationActivity");
        assert!(!state.observe(&rows, &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "TXT -  OCC -");
    }
}
