use super::*;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compaction_context_and_persist_listener_complete_concurrently() {
    let directory = tempfile::tempdir().unwrap();
    let cwd = directory.path().to_str().unwrap();
    let session = test_session(ScriptedAgent::new(vec![]));
    *session.session_manager.lock().unwrap() = SessionManager::create(cwd, Some(cwd)).unwrap();
    let (persisted_tx, persisted_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let release_rx = Mutex::new(release_rx);
    let settings = session.settings_manager.clone();
    session
        .session_manager
        .lock()
        .unwrap()
        .on_persist(Box::new(move |_| {
            persisted_tx.send(()).unwrap();
            release_rx.lock().unwrap().recv().unwrap();
            // The installed trace listener reads this setting while persistence
            // still owns the manager lock, even when trace sharing is disabled.
            assert!(!settings.lock().unwrap().get_agent_traces_enabled());
        }));
    let (done_tx, done_rx) = std::sync::mpsc::channel();
    let manager = session.session_manager.clone();
    let persist_done = done_tx.clone();
    let persist = std::thread::spawn(move || {
        manager.lock().unwrap().flush_now().unwrap();
        persist_done.send(()).unwrap();
    });
    persisted_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .unwrap();
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let compact = std::thread::spawn(move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        started_tx.send(()).unwrap();
        let context =
            runtime.block_on(session.compaction_provider_context(&[], &CancellationToken::new()));
        assert!(context.messages.is_empty());
        done_tx.send(()).unwrap();
    });
    started_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .unwrap();
    // Give compaction the contended manager before the persist callback reads
    // settings. The deadline is outside Tokio: a mutex cycle stalls its timers.
    std::thread::sleep(std::time::Duration::from_millis(100));
    release_tx.send(()).unwrap();
    for _ in 0..2 {
        done_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("compaction and transcript persistence must not deadlock");
    }
    persist.join().unwrap();
    compact.join().unwrap();
}
