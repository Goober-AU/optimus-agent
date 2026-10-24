use super::*;
use serde_json::json;

fn row(id: &str, parent: Option<&str>, pid: i64) -> SessionSummary {
    SessionSummary {
        session_id: id.into(),
        session_name: Some(id.into()),
        active_session_id: Some(format!("active-{id}")),
        parent_session_id: parent.map(str::to_string),
        worker_pid: Some(pid),
        worker_state: Some("ready".into()),
        runtime_kind: Some(
            if parent.is_some() {
                "subagent"
            } else {
                "top-level"
            }
            .into(),
        ),
        lifecycle: "live".into(),
        extra: [(
            "streamingThroughput".into(),
            json!({
                "generation": "fixture", "outputTokens": 100, "streamingMs": 2000,
                "responses": 1, "sequence": 1
            }),
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

fn observe(monitor: &mut Monitor, rows: &[SessionSummary], root: &str) {
    let mut throughput = Throughput::default();
    throughput.observe(rows, &parent(root));
    monitor.observe(rows, &parent(root), &throughput);
}

fn inject(monitor: &Monitor, now: Instant, pid: u32) {
    let mut state = monitor.state.lock().unwrap();
    state.measured_request = state.request.clone();
    state.measured_at = Some(now);
    state.readings = state
        .request
        .targets
        .iter()
        .filter(|target| target.pid == pid)
        .map(|target| {
            (
                target.session.clone(),
                Reading {
                    cpu: Some(125.0),
                    ram: Some(512 * 1024 * 1024),
                },
            )
        })
        .collect();
}

#[test]
fn resource_family_includes_nested_aliases_and_excludes_duplicates_forks_and_other_sessions() {
    let root = row("root", None, 1);
    let child = row("child", Some("root"), 1);
    let mut nested = row("nested", Some("wrong-id"), 2);
    nested.parent_active_session_id = child.active_session_id.clone();
    let mut fork = row("fork", Some("root"), 3);
    fork.runtime_kind = Some("top-level".into());
    let rows = vec![
        nested,
        fork,
        row("other", None, 4),
        child.clone(),
        root,
        child,
    ];
    let mut monitor = Monitor::default();
    observe(&mut monitor, &rows, "root");
    assert_eq!(
        monitor
            .agents
            .iter()
            .map(|a| a.label.as_str())
            .collect::<Vec<_>>(),
        ["MAIN", "child", "nested"]
    );
    assert!(monitor.agents[0].shared && monitor.agents[1].shared);
    assert!(!monitor.agents[2].shared);
    observe(&mut monitor, &rows, "child");
    assert_eq!(
        monitor
            .agents
            .iter()
            .map(|a| a.label.as_str())
            .collect::<Vec<_>>(),
        ["MAIN", "nested"]
    );
}

#[test]
fn resource_owner_resolution_rejects_cycles_and_keeps_other_roots_separate() {
    let rows = vec![
        row("a", Some("b"), 0),
        row("b", Some("a"), 0),
        row("root", None, 0),
        row("child", Some("root"), 0),
        row("other", None, 0),
    ];
    let indexed = rows
        .iter()
        .flat_map(|row| summary_keys(row).into_iter().map(move |key| (key, row)))
        .collect();
    assert!(worker_owner(&rows[0], &indexed).is_empty());
    assert!(worker_owner(&rows[1], &indexed).is_empty());
    assert_eq!(worker_owner(&rows[3], &indexed), "active-root");
    assert_eq!(worker_owner(&rows[4], &indexed), "active-other");
}

#[test]
fn resource_rates_are_per_agent_and_preserve_live_estimates_and_missing_samples() {
    let root = row("root", None, 1);
    let mut child = row("child", Some("root"), 2);
    let sample = child.extra.get_mut("streamingThroughput").unwrap();
    sample["active"] = json!(true);
    sample["estimated"] = json!(true);
    sample["outputTokens"] = json!(150);
    let mut unknown = row("unknown", Some("root"), 3);
    unknown.extra.clear();
    let mut monitor = Monitor::default();
    observe(&mut monitor, &[root, child, unknown], "root");
    let rendered = strip_ansi(&monitor.render("root", 200, 3, Instant::now()).join("\n"));
    assert!(rendered.contains("MAIN CPU — RAM — TPS 50.0"), "{rendered}");
    assert!(
        rendered.contains("child CPU — RAM — TPS ~75.0"),
        "{rendered}"
    );
    assert!(rendered.contains("unknown CPU — RAM — TPS —"), "{rendered}");
}

#[test]
fn resource_readings_expire_and_cannot_cross_sessions_or_worker_restarts() {
    let now = Instant::now();
    let mut monitor = Monitor::default();
    let root = row("root", None, 42);
    observe(&mut monitor, &[root.clone()], "root");
    inject(&monitor, now, 42);
    let render = |m: &Monitor, root, time| strip_ansi(&m.render(root, 160, 1, time).join("\n"));
    assert!(render(&monitor, "root", now).contains("CPU 125% RAM 512M TPS 50.0"));
    assert!(
        render(&monitor, "root", now + FRESH_FOR + Duration::from_millis(1))
            .contains("CPU — RAM —")
    );
    assert!(!render(&monitor, "other", now).contains("50.0"));
    let mut restarted = root.clone();
    restarted.worker_pid = Some(43);
    observe(&mut monitor, &[restarted], "root");
    assert!(render(&monitor, "root", now).contains("CPU — RAM —"));
    let mut stopped = root;
    stopped.worker_state = Some("failed".into());
    observe(&mut monitor, &[stopped], "root");
    assert_eq!(monitor.agents[0].target.pid, 0);
    assert!(render(&monitor, "root", now).contains("CPU — RAM —"));
    observe(&mut monitor, &[], "root");
    assert!(render(&monitor, "root", now).contains("MAIN CPU — RAM — TPS —"));
}

#[test]
fn resource_pages_reach_every_agent_without_overflow_or_losing_neon_blue() {
    let labels: Vec<_> = (0..12)
        .map(|i| format!("agent-{i} CPU 2% RAM 64MiB TPS 50.0"))
        .collect();
    for width in [0, 1, 8, 24, 40, 80, 120, 200] {
        for budget in 0..4 {
            let mut seen = String::new();
            for page in 0..24 {
                let rows = layout(&labels, 0, width, budget, page);
                assert!(rows.len() <= budget);
                assert!(
                    rows.iter().all(|r| visible_width(r) <= width),
                    "{width}: {rows:?}"
                );
                assert!(rows.iter().all(|r| r.starts_with(BLUE)));
                seen.push_str(&strip_ansi(&rows.join("\n")));
            }
            if width >= 40 && budget > 0 {
                for i in 0..12 {
                    assert!(
                        seen.contains(&format!("agent-{i} ")),
                        "{width}/{budget}: {seen}"
                    );
                }
            }
        }
    }
    assert!(strip_ansi(&layout(&labels[..1], 8, 80, 1, 0).join("\n")).contains("+8"));
    assert!(!clean_label("\x1b[31munsafe\nname\x07").contains(['\x1b', '\n', '\x07']));
}

#[test]
fn resource_sampler_proves_local_worker_identity_and_collects_real_process_readings() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let dir = tempfile::tempdir().unwrap();
    let workers = dir.path().join("daemon-workers/fixture");
    std::fs::create_dir_all(&workers).unwrap();
    // Optional read-only diagnostic against an explicitly selected local process.
    let pid = std::env::var("OPTIMUS_RESOURCE_SAMPLE_PID")
        .ok()
        .map(|pid| pid.parse::<u32>().expect("local diagnostic PID"))
        .unwrap_or_else(std::process::id);
    let start = crate::core::session_lease::get_process_start_id(i64::from(pid)).unwrap();
    let descriptor = workers.join("fixture.json");
    let owner = Target {
        session: "root".into(),
        active: "active-root".into(),
        pid,
        owner: "active-root".into(),
    };
    let write = |active: &str, start: &str| {
        std::fs::write(
            &descriptor,
            json!({
                "pid": pid, "rootActiveSessionId": active, "processStartId": start,
                "rootSessionId": "root"
            })
            .to_string(),
        )
        .unwrap()
    };
    let path = dir.path().to_string_lossy().to_string();
    write("other-worker", &start);
    assert!(local_worker_pids(&path, &[owner.clone()]).is_empty());
    write("active-root", "stale-pid-identity");
    assert!(local_worker_pids(&path, &[owner.clone()]).is_empty());
    write("active-root", &start);
    assert_eq!(
        local_worker_pids(&path, &[owner.clone()]).get("active-root"),
        Some(&pid)
    );
    let mut wrong_owner = owner.clone();
    wrong_owner.session = "other".into();
    assert!(local_worker_pids(&path, &[wrong_owner]).is_empty());
    let mut wrong_pid = owner;
    wrong_pid.pid = pid.saturating_add(1);
    assert!(local_worker_pids(&path, &[wrong_pid]).is_empty());
    let mut monitor = Monitor::default();
    // Production roster_subscribe omits workerPid, unlike list/get_state.
    let mut rows = vec![
        row("root", None, 0),
        row("child", Some("root"), 0),
        row("nested", Some("child"), 0),
        row("passive", Some("root"), 0),
    ];
    for row in &mut rows {
        row.worker_pid = None;
    }
    rows[3].active_session_id = None;
    observe(&mut monitor, &rows, "root");
    assert_eq!(
        monitor.agents.iter().filter(|agent| agent.shared).count(),
        3
    );
    let updates = Arc::new(AtomicUsize::new(0));
    let count = updates.clone();
    monitor.start(
        path.clone(),
        Arc::new(move || {
            count.fetch_add(1, Ordering::SeqCst);
        }),
    );
    monitor.start(path, Arc::new(|| panic!("sampler must start only once")));
    let deadline = Instant::now() + Duration::from_secs(6);
    loop {
        let sampled = monitor.state.lock().unwrap().readings.get("root").cloned();
        if sampled
            .as_ref()
            .is_some_and(|r| r.cpu.is_some() && r.ram.is_some())
            && updates.load(Ordering::SeqCst) >= 2
        {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "background sampler did not collect two samples"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(
        updates.load(Ordering::SeqCst) >= 2,
        "idle views must receive repaint notifications"
    );
    if pid == std::process::id() {
        // A zero-only sampler passes the memory/availability checks above. Keep
        // one thread busy across refreshes to exercise real per-PID CPU deltas.
        let work = std::thread::spawn(|| {
            let until = Instant::now() + Duration::from_millis(2200);
            let mut value = 1_u64;
            while Instant::now() < until {
                value = std::hint::black_box(value.wrapping_mul(6364136223846793005).wrapping_add(1));
            }
        });
        let mut peak = 0.0_f32;
        while !work.is_finished() {
            if let Some(cpu) = monitor.state.lock().unwrap().readings.get("root").and_then(|r| r.cpu) {
                peak = peak.max(cpu);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        work.join().unwrap();
        assert!(peak > 1.0, "busy local worker reported {peak}% CPU");
    }
    let state = monitor.state.lock().unwrap();
    for session in ["root", "child", "nested"] {
        let reading = &state.readings[session];
        assert!(reading.cpu.is_some_and(|cpu| cpu >= 0.0 && cpu.is_finite()));
        assert!(reading.ram.is_some_and(|ram| ram > 0));
    }
    assert!(!state.readings.contains_key("passive"));
    if let Ok(directory) = std::env::var("OPTIMUS_RESOURCE_CAPTURE_DIR") {
        std::fs::create_dir_all(&directory).unwrap();
        let reading = &state.readings["root"];
        std::fs::write(
            Path::new(&directory).join("process-sample.json"),
            json!({"pid":pid, "cpu":reading.cpu, "residentBytes":reading.ram,
                "sessions":state.readings.len(), "refreshes":updates.load(Ordering::SeqCst)})
            .to_string(),
        )
        .unwrap();
    }
    drop(state);
    let rendered = strip_ansi(&monitor.render("root", 60, 2, Instant::now()).join("\n"));
    assert!(
        rendered.contains("MAIN* CPU ") && !rendered.contains("CPU —"),
        "{rendered}"
    );
    let weak = Arc::downgrade(&monitor.state);
    drop(monitor);
    while weak.strong_count() > 0 {
        assert!(Instant::now() < deadline, "sampler retained its owner");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn resource_full_frame_keeps_one_context_health_indicator_and_a_live_header() {
    use crate::modes::agent_connection::daemon_agent_connection::{
        AgentConnectionRosterStore, DaemonTransportClient,
    };
    use pi_ai::types::BoxFuture;
    use pi_tui::tui::Component;
    use std::cell::RefCell;
    use std::rc::Rc;
    struct Roster(Vec<serde_json::Value>);
    impl AgentConnectionRosterStore for Roster {
        fn attach(&self, _: Arc<dyn DaemonTransportClient>) -> BoxFuture<Result<bool, String>> {
            Box::pin(async { Ok(true) })
        }
        fn on_update(&self, _: Arc<dyn Fn() + Send + Sync>) -> Box<dyn Fn() + Send + Sync> {
            Box::new(|| {})
        }
        fn summaries(&self) -> Vec<serde_json::Value> {
            self.0.clone()
        }
        fn dispose(&self) -> BoxFuture<()> {
            Box::pin(async {})
        }
    }
    for style in ["neon", "prime"] {
        let h = super::super::super::ui_tests::FrameHarness::new("root");
        crate::modes::interactive::theme::theme::init_theme(Some(style), false);
        h.mode.borrow_mut().options.return_to_agents_view = true;
        h.mode.borrow_mut().patch_connection_state(|state| {
            state.session_id = "root".into();
            state.active_session_id = Some("active-root".into());
            state.context_usage =
                crate::modes::interactive::interactive_mode_services::ContextUsage {
                    tokens: Some(100.0),
                    context_window: 1000.0,
                    percent: Some(10.0),
                };
        });
        h.width.set(160);
        h.height.set(48);
        let bar = Rc::new(RefCell::new(super::super::Bar::new(h.mode.clone())));
        *bar.borrow().roster.lock().unwrap() = Some(Arc::new(Roster(
            [row("root", None, 42), row("tests", Some("root"), 43)]
                .into_iter()
                .map(|row| serde_json::to_value(row).unwrap())
                .collect(),
        )));
        h.transcript.borrow_mut().subagents = Some(bar.clone());
        // The harness mounts a dock at construction; re-enter to mount this roster.
        h.ui.borrow_mut()
            .exit_fullscreen(pi_tui::tui::ExitFullscreenOptions {
                flush: false,
                leave_alt_screen: true,
            });
        super::super::super::native_settings::fullscreen(
            true,
            &h.mode,
            &h.editor,
            &h.ui,
            &h.transcript,
        );
        bar.borrow_mut().render(160.0);
        inject(&bar.borrow().resources, Instant::now(), 42);
        let frame = h.paint();
        let text = frame.join("\n");
        let metric = frame
            .iter()
            .position(|line| line.contains("MAIN CPU 125% RAM 512M TPS 50.0"))
            .unwrap();
        let brand = frame
            .iter()
            .position(|line| line.contains("telemus.ai"))
            .unwrap();
        let column = |row: &str, label: &str| {
            let row = strip_ansi(row);
            visible_width(&row[..row.find(label).unwrap()])
        };
        assert_eq!(metric, brand + 1, "{text}");
        assert_eq!(
            column(&frame[metric], "MAIN"),
            column(&frame[brand], "telemus.ai"),
            "{text}"
        );
        assert!(
            frame[..brand].iter().all(|line| !line.contains("CPU")),
            "{text}"
        );
        assert!(text.contains("tests CPU — RAM — TPS 50.0"), "{text}");
        assert_eq!(text.matches("CTX HEALTH").count(), 1, "{text}");
        assert!(!text.contains("CONTEXT HEALTH"), "{text}");
        assert!(
            text.contains("STREAM TPS 0.0  CTX HEALTH GREEN"),
            "{text}"
        );
        assert!(frame.iter().all(|line| visible_width(line) <= 160));
        if let Ok(directory) = std::env::var("OPTIMUS_RESOURCE_CAPTURE_DIR") {
            std::fs::create_dir_all(&directory).unwrap();
            std::fs::write(
                Path::new(&directory).join(format!("{style}-160x48.txt")),
                text,
            )
            .unwrap();
            let mut header = super::super::super::native_neon::Header(
                h.mode.clone(),
                Rc::downgrade(&h.transcript),
            );
            std::fs::write(
                Path::new(&directory).join(format!("{style}-header.ansi")),
                header.render_with_height(160.0, 12).join("\n"),
            )
            .unwrap();
        }
    }
}
