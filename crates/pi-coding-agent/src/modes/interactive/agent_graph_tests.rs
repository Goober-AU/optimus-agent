use super::*;
use pi_tui::utils::{strip_ansi, visible_width};
use serde_json::json;

fn parent(id: &str) -> RosterParent {
    RosterParent {
        session_id: Some(id.into()),
        active_session_id: Some(format!("active-{id}")),
        ..Default::default()
    }
}
fn row(id: &str, owner: Option<&str>, working: bool) -> SessionSummary {
    SessionSummary {
        id: id.into(),
        session_id: id.into(),
        active_session_id: Some(format!("active-{id}")),
        parent_session_id: owner.map(str::to_string),
        lifecycle: "live".into(),
        runtime_kind: Some(
            if owner.is_some() {
                "subagent"
            } else {
                "top-level"
            }
            .into(),
        ),
        activity: if working { "working" } else { "idle" }.into(),
        extra: [
            (
                "communicationActivity".into(),
                json!({"generation":"g", "deliveries":[]}),
            ),
            ("communicationObservationEpoch".into(), json!(1)),
        ]
        .into_iter()
        .collect(),
        ..Default::default()
    }
}
fn deliver(row: &mut SessionSummary, id: &str, from: &str) {
    row.extra.get_mut("communicationActivity").unwrap()["deliveries"] = json!([{
        "id":id, "sourceSessionId":from, "kind":"text", "timestamp":chrono::Utc::now().timestamp_millis(),
    }]);
}

#[test]
fn graph_uses_nested_roster_links_and_live_work_without_cross_session_nodes() {
    let now = Instant::now();
    let mut graph = Graph::default();
    let mut rows = vec![
        row("nested", Some("child"), true),
        row("root", None, true),
        row("child", Some("root"), false),
        row("foreign", None, true),
        row("foreign-child", Some("foreign"), true),
    ];
    graph.observe(&rows, &parent("root"), true, now);
    assert_eq!(
        graph
            .nodes
            .iter()
            .map(|node| node.id.as_str())
            .collect::<Vec<_>>(),
        ["root", "child", "nested"]
    );
    assert_eq!(graph.nodes[2].parent.as_deref(), Some("child"));
    assert!(graph.nodes[0].working && graph.nodes[2].working);
    assert!(!graph.nodes[1].working);
    rows[0].lifecycle = "saved".into();
    graph.observe(&rows, &parent("root"), false, now);
    assert!(graph.nodes.iter().all(|node| !node.working));
    rows.retain(|row| row.session_id != "nested");
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.nodes.len(), 2);
    graph.observe(&rows, &parent("foreign"), false, now);
    assert_eq!(
        graph
            .nodes
            .iter()
            .map(|node| node.id.as_str())
            .collect::<Vec<_>>(),
        ["foreign", "foreign-child"]
    );
}

#[test]
fn graph_messages_light_exact_edges_expire_and_never_replay_on_attach_or_reconnect() {
    let now = Instant::now();
    let mut graph = Graph::default();
    let mut rows = vec![
        row("root", None, false),
        row("a", Some("root"), false),
        row("b", Some("root"), false),
        row("nested", Some("a"), false),
    ];
    deliver(&mut rows[1], "historical", "root");
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.activity.live_links(now).count(), 0);
    deliver(&mut rows[2], "sibling", "a");
    deliver(&mut rows[3], "nested-task", "a");
    graph.observe(&rows, &parent("root"), false, now);
    let links = graph
        .activity
        .live_links(now)
        .map(|(a, b, _)| (a, b))
        .collect::<Vec<_>>();
    assert_eq!(links, [("a", "b"), ("a", "nested")]);
    graph.observe(
        &rows,
        &parent("root"),
        false,
        now + Duration::from_millis(900),
    );
    assert_eq!(graph.activity.live_links(now + PULSE).count(), 0);
    rows[0]
        .extra
        .insert("communicationObservationEpoch".into(), json!(2));
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.activity.live_links(now).count(), 0);
    deliver(&mut rows[1], "unrelated", "foreign");
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.activity.live_links(now).count(), 0);
    deliver(&mut rows[1], "fresh", "root");
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.activity.live_links(now).count(), 1);
    rows[1].extra.get_mut("communicationActivity").unwrap()["generation"] = json!("restarted");
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.activity.live_links(now).count(), 0);
    for row in &mut rows {
        row.extra.remove("communicationActivity");
    }
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.activity.live_links(now).count(), 0);
}

#[test]
fn graph_preserves_positions_on_roster_reorder_and_repaint_and_sleeps_when_settled() {
    let now = Instant::now();
    let mut graph = Graph::default();
    let mut rows = vec![
        row("root", None, false),
        row("a", Some("root"), false),
        row("b", Some("a"), false),
    ];
    graph.observe(&rows, &parent("root"), false, now);
    let initial = graph.nodes.iter().map(|n| (n.x, n.y)).collect::<Vec<_>>();
    graph.render("root", 24, 3, now);
    let first = graph
        .nodes
        .iter()
        .map(|n| (n.x, n.y, n.vx, n.vy))
        .collect::<Vec<_>>();
    for _ in 0..20 {
        graph.render("root", 24, 3, now);
    }
    assert_eq!(
        first,
        graph
            .nodes
            .iter()
            .map(|n| (n.x, n.y, n.vx, n.vy))
            .collect::<Vec<_>>(),
        "input repaints without elapsed time must not advance or damp the layout"
    );
    for frame in 0..90 {
        let time = now + FRAME * frame;
        graph.render("root", 24, 3, time);
    }
    let settled = graph.nodes.iter().map(|n| (n.x, n.y)).collect::<Vec<_>>();
    assert_ne!(
        initial, settled,
        "spring/repulsion forces must actually move nodes"
    );
    assert!(graph.next_frame(now + SETTLE).is_none());
    rows.reverse();
    graph.observe(&rows, &parent("root"), false, now + SETTLE);
    graph.render("root", 24, 3, now + SETTLE);
    assert_eq!(
        settled,
        graph.nodes.iter().map(|n| (n.x, n.y)).collect::<Vec<_>>()
    );
    assert!(graph.nodes.iter().all(|n| n.x.is_finite()
        && n.y.is_finite()
        && (0.0..=1.0).contains(&n.x)
        && (0.0..=1.0).contains(&n.y)));
    rows.push(row("new", Some("root"), true));
    graph.observe(&rows, &parent("root"), false, now + SETTLE);
    assert!(graph.next_frame(now + SETTLE).is_some());
    assert_eq!(graph.nodes[0].x, 0.5);
}

#[test]
fn graph_resizes_bounds_large_families_and_handles_missing_metadata_and_cycles() {
    let now = Instant::now();
    let mut graph = Graph::default();
    let mut rows = vec![row("root", None, false)];
    rows.extend((0..200).map(|i| row(&format!("child-{i:03}"), Some("root"), i % 2 == 0)));
    rows[0].parent_session_id = Some("child-000".into());
    rows[0].runtime_kind = Some("subagent".into());
    for row in &mut rows {
        row.extra.clear();
    }
    graph.observe(&rows, &parent("root"), false, now);
    assert_eq!(graph.nodes.len(), MAX_NODES);
    assert_eq!(graph.omitted, 137);
    assert_eq!(graph.activity.live_links(now).count(), 0);
    for (width, height) in [(0, 0), (5, 3), (8, 1), (24, 3), (40, 5)] {
        let rows = graph.render("root", width, height, now);
        assert!(rows.len() <= height);
        assert!(rows.iter().all(|row| visible_width(row) <= width));
        if width >= 8 {
            assert!(strip_ansi(&rows.join("\n")).contains("+137"));
        }
    }
    assert!(graph.render("different-session", 24, 3, now).is_empty());
}

#[test]
fn graph_frame_fixture_shows_neon_blue_work_and_communication_beside_resources() {
    use super::super::super::native_neon::{render_header, HeaderData};
    crate::modes::interactive::theme::theme::init_theme(Some("neon"), false);
    let now = Instant::now();
    let mut graph = Graph::default();
    let mut rows = vec![
        row("root", None, false),
        row("reader", Some("root"), true),
        row("tests", Some("root"), false),
        row("review", Some("reader"), true),
        row("docs", Some("root"), false),
    ];
    graph.observe(&rows, &parent("root"), false, now);
    for frame in 0..70 {
        graph.render("root", 24, 3, now + FRAME * frame);
    }
    let time = now + Duration::from_secs(6);
    deliver(&mut rows[2], "handoff", "reader");
    graph.observe(&rows, &parent("root"), false, time);
    let capture = std::env::var("OPTIMUS_GRAPH_CAPTURE_DIR").ok();
    for (name, time) in [
        ("active", time + Duration::from_millis(160)),
        ("expired", time + PULSE),
    ] {
        let graph_rows = graph.render("root", 24, 3, time);
        assert!(graph_rows.iter().all(|row| visible_width(row) == 24));
        assert!(graph_rows.join("").contains("38;5;39m"));
        let plain = strip_ansi(&graph_rows.join(""));
        assert_eq!(plain.matches('◆').count(), 1);
        assert_eq!(plain.matches('●').count(), 2);
        assert_eq!(plain.matches('○').count(), 2);
        let resources = vec![
            "\x1b[1;38;5;39mMAIN CPU 12% RAM 512M TPS 50.0\x1b[22;39m".into(),
            "\x1b[1;38;5;39mreader CPU 8% RAM 512M TPS 42.0\x1b[22;39m".into(),
        ];
        let data = HeaderData {
            cwd: "/workspace",
            session: "Live agents",
            model: "offline-fixture",
            execution: None,
            phase: "WORKING",
            jev: Some("Jev standard mode"),
            clock: "12:34:56",
            resources: &resources,
            graph: &graph_rows,
        };
        for width in [80, 110, 120, 160, 200] {
            let header = render_header(width, 7, &data);
            assert!(header.iter().all(|row| visible_width(row) == width));
            if width >= 110 {
                let first = strip_ansi(&header[0]);
                let metric = strip_ansi(&header[1]);
                assert_eq!(
                    visible_width(&first[..first.find("telemus.ai").unwrap()]),
                    visible_width(&metric[..metric.find("MAIN CPU").unwrap()])
                );
                assert!(header[..3].iter().any(|row| strip_ansi(row)
                    .chars()
                    .any(|c| ('\u{2801}'..='\u{28ff}').contains(&c))));
            }
            if let Some(dir) = &capture {
                std::fs::create_dir_all(dir).unwrap();
                std::fs::write(format!("{dir}/{name}-{width}.ansi"), header.join("\n")).unwrap();
                std::fs::write(
                    format!("{dir}/{name}-{width}.txt"),
                    strip_ansi(&header.join("\n")),
                )
                .unwrap();
            }
        }
    }
}
