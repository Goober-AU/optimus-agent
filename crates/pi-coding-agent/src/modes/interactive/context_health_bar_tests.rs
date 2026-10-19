use super::*;
use pi_tui::utils::{strip_ansi, visible_width};

fn snapshot(
    session: &str,
    tokens: Option<f64>,
    cycles: f64,
    compacting: bool,
) -> local::AgentConnectionState {
    local::AgentConnectionState {
        session_id: session.into(),
        active_session_id: Some(format!("active-{session}")),
        context_usage: local::ContextUsage {
            tokens,
            context_window: 1000.0,
            percent: tokens.map(|tokens| tokens / 10.0),
        },
        compaction_count: cycles,
        is_compacting: compacting,
        ..Default::default()
    }
}

#[test]
fn context_health_bar_paints_beside_tps_in_both_styles_and_narrow_terminals() {
    for style in ["prime", "neon"] {
        crate::modes::interactive::theme::theme::init_theme(Some(style), false);
        let h = super::super::ui_tests::FrameHarness::new("context-health-fixture");
        h.mode.borrow_mut().options.return_to_agents_view = true;
        h.mode.borrow_mut().replace_subagent_summary(Some(&[
            local::AgentConnectionRlmChildAgentSnapshot {
                id: "fixture-child".into(),
                active_session_id: Some("active-fixture-child".into()),
                status: "idle".into(),
                ..Default::default()
            },
        ]));
        let mut bar = Bar::new(h.mode.clone());
        for (tokens, name, ansi) in [
            (100.0, "GREEN", 82),
            (750.0, "AMBER", 214),
            (900.0, "RED", 196),
        ] {
            h.mode
                .borrow_mut()
                .apply_connection_state_snapshot(snapshot(
                    "context-health-fixture",
                    Some(tokens),
                    0.0,
                    false,
                ));
            for focused in [false, true] {
                bar.line.set_focused(focused);
                for width in [0, 1, 2, 8, 20, 40, 80, 120] {
                    let rows = bar.render(width as f64);
                    assert!(
                        rows.iter().all(|row| visible_width(row) <= width),
                        "{style}/{width}: {rows:?}"
                    );
                    let rendered = rows.join("\n");
                    let plain = strip_ansi(&rendered);
                    if width >= 40 {
                        assert!(
                            plain.contains("STREAM TPS —  CTX"),
                            "{style}/{width}: {plain}"
                        );
                        assert!(
                            rendered.contains(&format!("\x1b[1;38;5;{ansi}mCTX")),
                            "{rendered}"
                        );
                    }
                    if width >= 80 {
                        assert!(
                            plain.contains(&format!("STREAM TPS —  CTX HEALTH {name}")),
                            "{plain}"
                        );
                    }
                }
            }
        }
    }
}

#[test]
fn context_health_bar_tracks_compaction_recovery_and_session_switch_without_stale_health() {
    let h = super::super::ui_tests::FrameHarness::new("context-health-switch");
    let mut bar = Bar::new(h.mode.clone());
    let mut paint = |state, expected: &str| {
        h.mode.borrow_mut().apply_connection_state_snapshot(state);
        let text = strip_ansi(&bar.render(100.0).join("\n"));
        assert!(text.contains(expected), "{text}");
    };
    paint(snapshot("old", Some(950.0), 3.0, false), "CTX HEALTH RED");
    paint(
        snapshot("old", None, 4.0, true),
        "CTX HEALTH AMBER compacting",
    );
    paint(snapshot("old", None, 4.0, false), "CTX HEALTH UNKNOWN");
    paint(
        snapshot("old", Some(100.0), 4.0, false),
        "CTX HEALTH AMBER 76",
    );
    paint(snapshot("new", None, 0.0, false), "CTX HEALTH UNKNOWN");
    paint(
        snapshot("new", Some(100.0), 0.0, false),
        "CTX HEALTH GREEN 100",
    );
}
