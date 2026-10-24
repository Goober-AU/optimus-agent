//! Monotonic, session-scoped pulses over optional content-free roster metadata.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::time::{Duration, Instant};

use crate::core::occ::activity::{CommunicationActivity, CommunicationKind, MAX_ACTIVITY_MESSAGES};
use crate::modes::daemon::daemon_session_list::SessionSummary;
use crate::modes::interactive::components::subagent_summary_line::{linkage_row_for_roster_summary, RosterParent};

pub(super) const PULSE: Duration = Duration::from_millis(1400);
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
    until: [Option<Instant>; 3],
    supported: bool,
    observed_since_ms: Option<i64>,
}

impl ActivityState {
    pub(super) fn observe(&mut self, rows: &[SessionSummary], parent: &RosterParent, now: Instant) -> bool {
        let epoch = rows.first().and_then(|row| row.extra.get("communicationObservationEpoch"))
            .and_then(|value| value.as_u64()).unwrap_or(0);
        let identity = (parent.session_id.clone().unwrap_or_default(), parent.active_session_id.clone(), epoch);
        if self.identity != identity {
            *self = Self { identity, ..Self::default() };
        }
        let wall_now_ms = chrono::Utc::now().timestamp_millis();
        let observed_since_ms = *self.observed_since_ms.get_or_insert(wall_now_ms);
        let is_child = |row: &&SessionSummary| row.runtime_kind.as_deref() == Some("subagent")
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
            // Reconnects baseline history; newly spawned children may already have a fresh task.
            let new_receiver = previous.is_none();
            let baseline = !self.initialized || previous.is_some_and(|state| state.generation != activity.generation);
            let receiver = self.receivers.entry(row.session_id.clone()).or_default();
            if receiver.generation != activity.generation {
                receiver.generation = activity.generation;
                receiver.seen.clear();
            }
            for delivery in activity.deliveries.iter().rev().take(MAX_ACTIVITY_MESSAGES).rev() {
                if receiver.seen.contains(&delivery.id) { continue; }
                receiver.seen.push_back(delivery.id.clone());
                while receiver.seen.len() > MAX_SEEN { receiver.seen.pop_front(); }
                if baseline || (new_receiver && delivery.timestamp < observed_since_ms)
                    || !family(&delivery.source_session_id)
                    || (!children.contains(row.session_id.as_str()) && !children.contains(delivery.source_session_id.as_str())) {
                    continue;
                }
                let index = match delivery.kind {
                    CommunicationKind::Text => 0,
                    CommunicationKind::Occ1 => 1,
                    // OCC2 is live: the roster projection reports Symbolic only
                    // for delivered bodies that decoded through the strict
                    // OCC-2 symbolic path, so the pulse reflects the wire
                    // encoding, not the sender's feature flag. LAT stays
                    // reserved: OCC-3 has no producer yet, so latent-kind
                    // data is ignored rather than rendered.
                    CommunicationKind::Symbolic => 2,
                    CommunicationKind::Latent => continue,
                };
                self.until[index] = Some(now + PULSE);
                pulse = true;
            }
        }
        self.receivers.retain(|id, _| received.contains(id));
        if !self.supported { self.until = [None; 3]; }
        self.initialized = self.supported;
        pulse
    }

    pub(super) fn next_deadline(&self, now: Instant) -> Option<Instant> {
        self.until.iter().flatten().copied().filter(|deadline| *deadline > now).min()
    }

    pub(super) fn indicators(&self, now: Instant) -> String {
        ["TXT", "OCC1", "OCC2"].iter().enumerate().map(|(index, label)| {
            let active = self.until[index].is_some_and(|until| now < until);
            let marker = if !self.supported { "-" } else if active { "●" } else { "○" };
            let intensity = if active { "1" } else { "2" };
            // ANSI 256-color 39 is neon blue and works without truecolor support.
            format!("\x1b[{intensity};38;5;39m{marker} {label}\x1b[22;39m")
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
        assert_eq!(strip_ansi(&state.indicators(now)), "○ TXT  ○ OCC1  ○ OCC2");
        let fresh = row("child", true, json!([message("new", "parent", "text")]));
        assert!(state.observe(&[fresh.clone()], &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "● TXT  ○ OCC1  ○ OCC2");
        assert!(!state.observe(&[fresh], &parent(), now + Duration::from_millis(500)));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE)), "○ TXT  ○ OCC1  ○ OCC2");
        assert!(state.observe(&[row("child", true, json!([message("burst", "parent", "occ1")]))], &parent(), now + PULSE));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE)), "○ TXT  ● OCC1  ○ OCC2");
        assert!(state.observe(&[row("child", true, json!([message("burst2", "parent", "occ1")]))], &parent(), now + PULSE + Duration::from_millis(500)));
        assert!(strip_ansi(&state.indicators(now + PULSE * 2)).contains("● OCC1"));
        // A symbolic delivery pulses SYM with the same lifetime and
        // coalescing rules; it never lights the OCC slot. burst2's OCC
        // pulse is still live here, so SYM lights beside it.
        assert!(state.observe(&[row("child", true, json!([message("sym", "parent", "symbolic")]))], &parent(), now + PULSE * 2));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE * 2)), "○ TXT  ● OCC1  ● OCC2");
        assert!(!state.observe(&[row("child", true, json!([message("sym", "parent", "symbolic")]))], &parent(), now + PULSE * 2 + Duration::from_millis(500)));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE * 3)), "○ TXT  ○ OCC1  ○ OCC2");
    }

    #[test]
    fn communication_family_scope_includes_siblings_and_child_to_parent_but_not_other_sessions() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        let mut rows = vec![row("parent", false, json!([])), row("a", true, json!([])), row("b", true, json!([]))];
        state.observe(&rows, &parent(), now);
        rows[2] = row("b", true, json!([message("1", "a", "occ1")]));
        assert!(state.observe(&rows, &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "○ TXT  ● OCC1  ○ OCC2");
        rows[0] = row("parent", false, json!([message("2", "b", "text")]));
        assert!(state.observe(&rows, &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "● TXT  ● OCC1  ○ OCC2");
        // A text delivery from outside the family stays filtered (the
        // scope rule), while an in-family symbolic delivery pulses SYM and
        // a latent-kind delivery is reserved (never pulses, never renders).
        rows[1] = row("a", true, json!([message("3", "unrelated", "text"), message("4", "parent", "symbolic"), message("5", "parent", "latent")]));
        assert!(state.observe(&rows, &parent(), now + PULSE));
        assert_eq!(strip_ansi(&state.indicators(now + PULSE)), "○ TXT  ○ OCC1  ● OCC2");
        assert!(!state.indicators(now + PULSE).contains("LAT"));
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
        assert_eq!(strip_ansi(&state.indicators(now)), "- TXT  - OCC1  - OCC2");
        assert!(!state.observe(&rows, &parent(), now));
        rows[0].extra.remove("communicationActivity");
        assert!(!state.observe(&rows, &parent(), now));
        assert_eq!(strip_ansi(&state.indicators(now)), "- TXT  - OCC1  - OCC2");
    }

    #[test]
    fn symbolic_deliveries_pulse_the_sym_slot_exactly_like_occ1() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        // Baseline first, so both bursts are non-baseline deliveries.
        state.observe(&[row("child", true, json!([message("base", "parent", "text")]))], &parent(), now);
        // An occ1 delivery pulses the OCC slot only.
        let occ = row("child", true, json!([message("occ", "parent", "occ1")]));
        assert!(state.observe(&[occ], &parent(), now + Duration::from_millis(100)));
        assert_eq!(strip_ansi(&state.indicators(now + Duration::from_millis(100))), "○ TXT  ● OCC1  ○ OCC2");
        // A symbolic delivery drives the same pulse path (dedupe, family
        // scope, PULSE lifetime) with no special-casing: it lights SYM
        // only, never OCC.
        let sym = row("child", true, json!([message("sym", "parent", "symbolic")]));
        assert!(state.observe(&[sym.clone()], &parent(), now + Duration::from_millis(200)));
        assert_eq!(strip_ansi(&state.indicators(now + Duration::from_millis(200))), "○ TXT  ● OCC1  ● OCC2");
        // Identical replay is a no-op, exactly like an occ1 replay.
        assert!(!state.observe(&[sym], &parent(), now + Duration::from_millis(300)));
        // The SYM pulse expires on the same PULSE clock as OCC: both lit
        // at +200ms are dark again at +200ms + PULSE.
        assert_eq!(strip_ansi(&state.indicators(now + Duration::from_millis(200) + PULSE)), "○ TXT  ○ OCC1  ○ OCC2");
        // A fresh symbolic delivery after expiry pulses again.
        assert!(state.observe(&[row("child", true, json!([message("sym2", "parent", "symbolic")]))], &parent(), now + Duration::from_millis(200) + PULSE));
        assert_eq!(strip_ansi(&state.indicators(now + Duration::from_millis(200) + PULSE)), "○ TXT  ○ OCC1  ● OCC2");
    }

    #[test]
    fn latent_deliveries_are_reserved_and_never_pulse_or_render() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        let baseline = row("child", true, json!([message("base", "parent", "text")]));
        state.observe(&[baseline], &parent(), now);
        // LAT-kind data may appear on the wire (the kind is serde-decodable);
        // the state machine ignores it: no pulse, no LAT indicator.
        let latent = row("child", true, json!([message("lat", "parent", "latent")]));
        assert!(!state.observe(&[latent], &parent(), now));
        let rendered = state.indicators(now);
        assert_eq!(strip_ansi(&rendered), "○ TXT  ○ OCC1  ○ OCC2");
        assert!(!rendered.contains("LAT"));
        // LAT never renders in any state: idle, pulsing or unsupported.
        assert!(state.observe(&[row("child", true, json!([message("t", "parent", "text")]))], &parent(), now));
        assert!(!state.indicators(now).contains("LAT"));
        let unsupported = ActivityState::default();
        assert!(!unsupported.indicators(now).contains("LAT"));
    }
    #[test]
    fn admitted_message_projection_reaches_neon_bar_for_all_three_formats() {
        use crate::core::agent_messages::{create_agent_session_message, AgentSessionMessagePayload, AgentSessionMessageSender, AgentSessionMessageEndpoint};
        use crate::core::occ::{activity::from_admitted_messages, contract::AgentCommMessage, dialect::DialectCard, occ2};
        use pi_agent_core::types::AgentMessage;
        use crate::modes::interactive::components::subagent_summary_line::SubagentSummaryLine;
        use pi_tui::tui::Component;
        crate::modes::interactive::theme::theme::init_theme(Some("dark"), false);
        let contract = AgentCommMessage::new("TASK", "test-task", "pending");
        let cases = [
            ("TXT", "Please inspect this task".to_string()),
            ("OCC1", contract.to_occ1().unwrap().encode().unwrap()),
            ("OCC2", occ2::encode_dual(&contract, &DialectCard::sym_v1()).unwrap()),
        ];
        for (label, body) in cases {
            let now = Instant::now();
            let mut state = ActivityState::default();
            let parent_row = row("parent", false, json!([]));
            state.observe(&[parent_row.clone()], &parent(), now);
            let message = AgentMessage::Custom(create_agent_session_message(&AgentSessionMessagePayload {
                id: "fresh-delivery".into(), message: body,
                from: Some(AgentSessionMessageSender { session_id: Some("parent".into()), ..Default::default() }),
                target: AgentSessionMessageEndpoint { session_id: "child".into(), ..Default::default() },
                ..Default::default()
            }, chrono::Utc::now().timestamp_millis()));
            let mut child = row("child", true, json!([]));
            child.extra.insert("communicationActivity".into(), serde_json::to_value(from_admitted_messages(&[message], "child", "g")).unwrap());
            let rows = [parent_row, child];
            assert!(state.observe(&rows, &parent(), now));
            let mut line = SubagentSummaryLine::default();
            line.set_always_visible(true);
            line.set_communication_status(Some(state.indicators(now)));
            let rendered = line.render(80.0).join("\n");
            assert!(rendered.contains(&format!("\x1b[1;38;5;39m● {label}")), "{rendered}");
            assert!(!state.observe(&rows, &parent(), now));
            assert_eq!(state.next_deadline(now), Some(now + PULSE));
            assert!(state.next_deadline(now + PULSE).is_none());
            line.set_communication_status(Some(state.indicators(now + PULSE)));
            assert!(strip_ansi(&line.render(80.0).join("\n")).contains(&format!("○ {label}")));
        }
    }

    #[test]
    fn communication_new_child_first_delivery_flashes_but_old_history_does_not() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        state.observe(&[row("parent", false, json!([]))], &parent(), now);
        let mut fresh = message("spawn:new", "parent", "symbolic");
        fresh["timestamp"] = json!(chrono::Utc::now().timestamp_millis());
        let rows = vec![row("parent", false, json!([])), row("new-child", true, json!([fresh]))];
        assert!(state.observe(&rows, &parent(), now));
        assert!(state.indicators(now).contains("\x1b[1;38;5;39m● OCC2"));
        assert!(!state.observe(&rows, &parent(), now));
        let old = vec![row("parent", false, json!([])), row("old-child", true, json!([message("historical", "parent", "text")]))];
        assert!(!state.observe(&old, &parent(), now + PULSE));
        assert!(!state.indicators(now + PULSE).contains('●'));
    }

    #[test]
    fn communication_reply_from_just_completed_child_still_flashes() {
        let now = Instant::now();
        let mut state = ActivityState::default();
        let mut rows = vec![row("parent", false, json!([])), row("child", true, json!([]))];
        state.observe(&rows, &parent(), now);
        rows[1].lifecycle = "saved".into();
        rows[0] = row("parent", false, json!([message("reply", "child", "occ1")]));
        assert!(state.observe(&rows, &parent(), now));
        assert!(state.indicators(now).contains("\x1b[1;38;5;39m● OCC1"));
    }

}
