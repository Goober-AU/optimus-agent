//! Detached RLM runs and the session's agent-message host wrapper.
//! Reference: agent-session.ts `_startRlmChildRun` and child lifecycle methods.

use super::*;
use crate::core::agent_messages::{
    agent_session_message_prompt_body, wrap_outgoing_body, AgentFamilyRosterResult,
    AgentSessionMessageReceipt, AgentSessionMessageSendInput, Occ2SendPolicy,
};
use crate::core::messages::RlmChildFailureDetails;
use crate::core::occ::dialect::OccCapability;

pub(super) struct SessionMessageController(pub std::sync::Weak<AgentSession>);

impl AgentSessionMessageController for SessionMessageController {
    fn roster(&self) -> BoxFuture<Result<AgentFamilyRosterResult, String>> {
        let weak = self.0.clone();
        Box::pin(async move {
            let session = weak.upgrade().ok_or("Session disposed")?;
            let result = session
                .handle_agent_message_host_request("agent_message.list_agents", None)?
                .await?;
            serde_json::from_value(result).map_err(|error| error.to_string())
        })
    }

    fn await_pending_child_publication(
        &self,
        selector: String,
    ) -> BoxFuture<Result<Option<String>, String>> {
        let weak = self.0.clone();
        Box::pin(async move {
            weak.upgrade()
                .ok_or("Session disposed")?
                .await_pending_rlm_child_publication(&selector)
                .await
        })
    }

    fn send_agent_message(
        &self,
        input: AgentSessionMessageSendInput,
    ) -> BoxFuture<Result<AgentSessionMessageReceipt, String>> {
        let weak = self.0.clone();
        Box::pin(async move {
            weak.upgrade()
                .ok_or("Session disposed")?
                .send_agent_message(input.target, input.message, input.receiver_role)
                .await
        })
    }

    /// OCC-2 S1 negotiation source (OCC-2 integration design, section 4):
    /// the feature flag plus the target's advertised capability decide the
    /// dialect; anything missing keeps the send OCC-1.
    fn occ2_send_policy(&self, target: &str, message: &str) -> BoxFuture<Occ2SendPolicy> {
        let weak = self.0.clone();
        let target = target.to_string();
        let message = message.to_string();
        Box::pin(async move {
            match weak.upgrade() {
                Some(session) => session.occ2_target_send_policy(&target, &message).await,
                None => Occ2SendPolicy::disabled(),
            }
        })
    }
}

/// Activity start time for one child event: the event's own timestamp when the
/// serialized JSON carries one (assistant message events do), otherwise the
/// observation time. Tool execution events carry no timestamp.
fn rlm_child_activity_since_ms(value: &Value) -> f64 {
    value
        .get("timestamp")
        .and_then(Value::as_f64)
        .or_else(|| {
            value
                .get("message")
                .and_then(|message| message.get("timestamp"))
                .and_then(Value::as_f64)
        })
        .unwrap_or_else(now_ms)
}

/// One child event's transition onto the parent's run row. Extracted from the
/// run subscription so the activity bookkeeping (kind, tool, start time, and
/// the stall-notice reset) is directly testable without a live child.
fn apply_rlm_child_run_event(
    run: &mut RlmChildRun,
    running_tools: &AtomicU64,
    kind: &str,
    value: &Value,
    assistant: Option<&AssistantMessage>,
) {
    match kind {
        "agent_start" => run.activity = Some(RlmChildAgentActivity {
            kind: "waiting".into(),
            tool_name: None,
            since_ms: Some(rlm_child_activity_since_ms(value)),
        }),
        "agent_end" => {
            run.activity = None;
            run.stall_notice_since_ms = None;
        }
        "message_start" | "message_update" | "message_end" if assistant.is_some() => {
            let text = compact_rlm_text(&read_assistant_text(assistant.unwrap()), 160);
            if !text.is_empty() { run.answer_preview = Some(text); }
            if kind != "message_end" {
                run.activity = Some(RlmChildAgentActivity {
                    kind: "writing".into(),
                    tool_name: None,
                    since_ms: Some(rlm_child_activity_since_ms(value)),
                });
            }
        }
        "tool_execution_start" => {
            run.tool_use_count += 1.0;
            running_tools.fetch_add(1, Ordering::SeqCst);
            run.activity = Some(RlmChildAgentActivity {
                kind: "executing".into(),
                tool_name: value.get("toolName").and_then(Value::as_str).map(str::to_string),
                since_ms: Some(rlm_child_activity_since_ms(value)),
            });
            run.stall_notice_since_ms = None;
        }
        "tool_execution_end" => {
            let previous = running_tools.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |count| Some(count.saturating_sub(1))).unwrap_or(0);
            if previous <= 1 {
                run.activity = Some(RlmChildAgentActivity {
                    kind: "waiting".into(),
                    tool_name: None,
                    since_ms: Some(rlm_child_activity_since_ms(value)),
                });
                run.stall_notice_since_ms = None;
            }
        }
        _ => {}
    }
}

/// Wording for the one-per-stalled-call notice.
fn rlm_child_stall_notice_text(session_name: &str, tool_name: Option<&str>, elapsed_ms: f64) -> String {
    let minutes = ((elapsed_ms / 60_000.0).round() as u64).max(1);
    format!(
        "Child {} tool {} has been running for {} min",
        session_name,
        tool_name.unwrap_or("unknown"),
        minutes
    )
}

impl AgentSession {
    /// Read this chat's preference, inheriting only through its subagent ancestry.
    fn occ2_enabled(&self) -> bool {
        crate::core::occ::session_settings::enabled(self.session_file().as_deref())
    }

    /// OCC-2 send policy for one target session (S1). The peer capability
    /// comes from the family listing's optional `occ_capability` field; a
    /// target that reports nothing, or a dialect this build does not
    /// register, keeps the send OCC-1.
    ///
    /// Selection is delegated to `occ2_send_policy_from_selection`
    /// (selection.rs policy v1): the production policy runs first and
    /// selects OCC-2 only for a validated dialect that wins on measured
    /// economics; the experimental `/occ 2` opt-in additionally allows
    /// the unevaluated dialect through the development policy (SILO
    /// reference profile), still behind the small-message floor, the
    /// strike guard and the codebook amortization gate.
    pub(super) async fn occ2_target_send_policy(
        self: &Arc<Self>,
        target: &str,
        message: &str,
    ) -> Occ2SendPolicy {
        if !self.occ2_enabled() {
            return Occ2SendPolicy::disabled();
        }
        let Some(controller) = self.agent_message_controller.clone() else {
            return Occ2SendPolicy::disabled();
        };
        let listed = match controller.list_agents().await {
            Ok(Some(listed)) => listed,
            _ => return Occ2SendPolicy::disabled(),
        };
        let Some(peer_entry) = listed
            .agents
            .iter()
            .find(|agent| agent.active_session_id == target || agent.session_id == target)
        else {
            return Occ2SendPolicy::disabled();
        };
        let peer = peer_entry.occ_capability.clone();
        // The delivery-side pair key uses the target's session id; the
        // listing entry carries it even when the caller passed an
        // active-session id.
        let listener = peer_entry.session_id.clone();
        self.occ2_selected_send_policy(peer.as_ref(), &listener, message, None)
            .await
    }

    /// OCC-2 send policy for the S2 spawn seam: a freshly spawned child
    /// shares this daemon's codec build, so v1 negotiates
    /// `OccCapability::here()` against itself (design sections 4 and 7).
    /// The spawn prompt length feeds the small-message floor and the
    /// codebook economics; the child's fresh context never holds the
    /// codebook yet.
    async fn occ2_spawn_send_policy(
        self: &Arc<Self>,
        listener: &str,
        prompt: &str,
    ) -> Occ2SendPolicy {
        if !self.occ2_enabled() {
            return Occ2SendPolicy::disabled();
        }
        let local = OccCapability::here();
        self.occ2_selected_send_policy(Some(&local), listener, prompt, None)
            .await
    }

    /// The shared selection assembly behind both send seams: resolves
    /// this session's real inputs (evaluation store, strike ledger,
    /// model identity) and delegates to the one send-selection
    /// function in `agent_messages.rs`, which the daemon-bridged
    /// controller also calls.
    async fn occ2_selected_send_policy(
        self: &Arc<Self>,
        peer: Option<&OccCapability>,
        listener: &str,
        message: &str,
        _listener_model: Option<&str>,
    ) -> Occ2SendPolicy {
        let session_file = self.session_file();
        let speaker_model = self.model().map(|model| model.id);
        let speaker = self.session_id();
        crate::core::agent_messages::occ2_send_policy_for_send(
            peer,
            listener,
            message,
            session_file.as_deref(),
            speaker_model.as_deref(),
            &speaker,
            true,
        )
    }
}

struct NameReservation {
    parent: std::sync::Weak<AgentSession>,
    name: String,
}

impl NameReservation {
    fn new(parent: &Arc<AgentSession>, name: &str) -> Result<Self, String> {
        if !parent
            .pending_rlm_subagent_session_names
            .lock()
            .unwrap()
            .insert(name.into())
        {
            return Err(format_agent_session_name_unavailable(
                name,
                (parent.rlm_depth + 1) as f64,
            ));
        }
        Ok(Self {
            parent: Arc::downgrade(parent),
            name: name.into(),
        })
    }
}

impl Drop for NameReservation {
    fn drop(&mut self) {
        if let Some(parent) = self.parent.upgrade() {
            parent
                .pending_rlm_subagent_session_names
                .lock()
                .unwrap()
                .remove(&self.name);
        }
    }
}

fn custom_notice(message: CustomAgentMessage) -> CustomMessage {
    match message {
        CustomAgentMessage::Custom {
            custom_type,
            content,
            display,
            details,
            timestamp,
        } => CustomMessage {
            role: "custom".into(),
            custom_type,
            content,
            display,
            details,
            timestamp,
        },
        _ => unreachable!("RLM notice constructors return custom messages"),
    }
}

impl AgentSession {
    /// `_startRlmChildRun`: admit immediately; an owned task completes startup,
    /// execution, durable parent notification, retention and cleanup.
    pub(super) async fn start_rlm_child_run(
        self: &Arc<Self>,
        prompt: &str,
        kwargs: &Map<String, Value>,
        spawn_code: Option<String>,
    ) -> Result<RlmSpawnHandle, String> {
        let spawned_by_request_id = self
            .is_streaming()
            .then(|| self.semantic_edges.lock().unwrap().last_turn_request_id())
            .flatten();
        let mut unsupported: Vec<_> = kwargs
            .keys()
            .filter(|key| !matches!(key.as_str(), "name" | "model" | "thinking"))
            .cloned()
            .collect();
        if !unsupported.is_empty() {
            unsupported.sort();
            return Err(format!(
                "Unsupported rlm.run kwargs: {}",
                unsupported.join(", ")
            ));
        }
        let requested_name =
            normalize_requested_rlm_subagent_session_name(kwargs.get("name"), None)?;
        let requested_model = normalize_requested_rlm_subagent_model(kwargs.get("model"), None)?;
        let requested_thinking =
            normalize_requested_rlm_subagent_thinking_level(kwargs.get("thinking"), None)?;
        if let Some(name) = &requested_name {
            assert_direct_agent_message_target(name)?;
        }
        if self.rlm_depth >= self.rlm_max_depth() {
            return Err(crate::core::rlm_max_depth::rlm_depth_limit_reached_error(
                self.rlm_depth,
                self.rlm_max_depth(),
            ));
        }
        let mut reservation = requested_name
            .as_deref()
            .map(|name| NameReservation::new(self, name))
            .transpose()?;
        let selection = async {
            if let Some(name) = &requested_name {
                self.assert_rlm_subagent_session_name_available(name, true)
                    .await?;
            }
            self.resolve_rlm_subagent_model(requested_model.as_deref(), "subagent")
                .await
        }
        .await;
        let model = selection?.model;
        if let Some(thinking) = requested_thinking {
            let supported = get_supported_thinking_levels(&model);
            if !supported
                .iter()
                .any(|level| level == &thinking_level_name(&thinking))
            {
                return Err(format!("Requested thinking level \"{}\" is not supported by model \"{}/{}\"; supported levels: {}", thinking_level_name(&thinking), model.provider, model.id, supported.join(", ")));
            }
        }
        if self.disposed.load(Ordering::SeqCst) || self.disposing.load(Ordering::SeqCst) {
            return Err("Cannot spawn a subagent after its parent was disposed".into());
        }
        let session_dir = self.create_child_rlm_session_dir()?;
        let id = Path::new(&session_dir)
            .file_name()
            .ok_or("Invalid child session directory")?
            .to_string_lossy()
            .into_owned();
        let name =
            requested_name.unwrap_or_else(|| create_default_rlm_subagent_session_name(prompt, &id));
        if reservation.is_none() {
            reservation = Some(NameReservation::new(self, &name)?);
        }
        self.assert_rlm_subagent_session_name_available(&name, true)
            .await?;
        // The parent's effective mode is snapshotted at child creation, so a
        // later global change cannot silently alter an existing child chat.
        // Best-effort: failing to record inheritance must not fail the spawn,
        // and a skipped record resolves to the same global default (built-in
        // Off) - never a silent Compare.
        if let Some(agent_dir) = self.agent_dir.as_deref() {
            let bridge = crate::modes::interactive::native_host::JevModeBridge::new(
                std::path::Path::new(agent_dir),
            );
            let _ = bridge.inherit_into_child(&id, &self.session_id(), None);
        }
        let handle = RlmSpawnHandle {
            rlm_child_id: id.clone(),
            name: name.clone(),
            session_dir: session_dir.clone(),
            model: format!("{}/{}", model.provider, model.id),
        };
        let mut options =
            self.create_rlm_subagent_runtime_options(RlmSubagentRuntimeOptionsInput {
                id: id.clone(),
                prompt: prompt.into(),
                session_name: name.clone(),
                session_dir: session_dir.clone(),
                thinking_level: Some(requested_thinking.unwrap_or_else(|| {
                    clamp_thinking_level_for_model(&model, self.thinking_level())
                })),
                model: model.clone(),
                spawn_code,
                spawned_by_request_id,
            })?;
        let run = Arc::new(Mutex::new(RlmChildRun {
            id: id.clone(),
            prompt: prompt.into(),
            session_name: name,
            session_dir,
            model: Some(model),
            status: "queued".into(),
            duration_ms: None,
            answer_preview: None,
            tool_use_count: 0.0,
            activity: None,
            error: None,
            abort: Arc::new(noop_rlm_child_abort),
            publication: create_agent_message_deferred(),
            settlement: create_agent_message_deferred(),
            session: None,
            settled: false,
            suppress_terminal_notice: None,
            abandoned_for_quiescence: None,
            detached_deletion: None,
            deletion_cleanup: None,
            deletion_cleanup_observer: None,
            deletion_reservation: create_agent_message_deferred(),
            deletion_cleanup_failed: None,
            deletion_run_finished: None,
            deletion_notice: None,
            deletion_failure_notice: None,
            deletion_needs_completion_notice: None,
            complete_deletion: None,
            report_deletion_cleanup_failure: None,
            emit_update: None,
            last_emitted_update: None,
            unsubscribe: None,
            stall_notice_since_ms: None,
        }));
        let weak_parent = Arc::downgrade(self);
        let weak_run = Arc::downgrade(&run);
        run.lock().unwrap().complete_deletion = Some(Arc::new(move || {
            let parent = weak_parent.upgrade();
            let run = weak_run.upgrade();
            Box::pin(async move {
                if let (Some(parent), Some(run)) = (parent, run) {
                    parent.publish_deletion_notice(&run, None).await;
                }
            })
        }));
        let weak_parent = Arc::downgrade(self);
        let weak_run = Arc::downgrade(&run);
        run.lock().unwrap().report_deletion_cleanup_failure = Some(Arc::new(move |error| {
            let parent = weak_parent.upgrade();
            let run = weak_run.upgrade();
            Box::pin(async move {
                if let (Some(parent), Some(run)) = (parent, run) {
                    parent.publish_deletion_notice(&run, Some(error)).await;
                }
            })
        }));
        let weak_parent = Arc::downgrade(self);
        let weak_run = Arc::downgrade(&run);
        run.lock().unwrap().emit_update = Some(Arc::new(move || {
            if let (Some(parent), Some(run)) = (weak_parent.upgrade(), weak_run.upgrade()) {
                parent.emit_child_run_update(&run);
            }
        }));
        let weak_run = Arc::downgrade(&run);
        let weak_parent = Arc::downgrade(self);
        options.on_session_published = Some(Arc::new(move |child| {
            if let Some(run) = weak_run.upgrade() {
                let weak_child = Arc::downgrade(child);
                let abort: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                    if let Some(child) = weak_child.upgrade() {
                        tokio::spawn(Box::pin(async move {
                            let _ = child.abort().await;
                        }));
                    }
                });
                let cancelled = {
                    let mut run = run.lock().unwrap();
                    run.session = Some(child.clone());
                    run.abort = abort.clone();
                    run.publication.resolve();
                    run.status == "cancelled"
                };
                let retained = weak_parent.upgrade().and_then(|parent| {
                    parent.retained_stop_ids.lock().unwrap().get(&run.lock().unwrap().id).cloned()
                });
                if let Some(generation) = retained {
                    let id = run.lock().unwrap().id.clone();
                    child.request_retained_stop(&id, &generation);
                } else if cancelled {
                    abort();
                }
            }
        }));
        self.active_rlm_child_runs
            .lock()
            .unwrap()
            .insert(id, run.clone());
        drop(reservation);
        self.unsettled_rlm_child_runs
            .lock()
            .unwrap()
            .push(run.clone());
        self.emit_child_run_update(&run);
        let parent = self.clone();
        let parent_assistant = self.find_last_assistant_message();
        tokio::spawn(Box::pin(async move {
            let started = std::time::Instant::now();
            let outcome = std::panic::AssertUnwindSafe(parent.execute_child_run(
                run.clone(),
                options.clone(),
                parent_assistant,
            ))
            .catch_unwind()
            .await;
            let outcome = outcome.unwrap_or_else(|_| Err("Subagent task panicked".into()));
            parent
                .settle_child_run(
                    run,
                    options,
                    outcome,
                    started.elapsed().as_secs_f64() * 1000.0,
                )
                .await;
        }));
        Ok(handle)
    }

    async fn publish_deletion_notice(
        self: &Arc<Self>,
        run: &Arc<Mutex<RlmChildRun>>,
        error: Option<String>,
    ) {
        if self.disposed.load(Ordering::SeqCst) || self.disposing.load(Ordering::SeqCst) {
            return;
        }
        let (notice, pending, existing) = {
            let mut run = run.lock().unwrap();
            if run.suppress_terminal_notice == Some(true)
                || (error.is_none() && run.deletion_needs_completion_notice != Some(true))
            {
                return;
            }
            let notice = match error.as_ref() {
                Some(error) => create_rlm_child_failure_message(RlmChildFailureDetails {
                    child_id: run.id.clone(), session_name: run.session_name.clone(),
                    error: format!("Deletion cleanup failed; retry rlm.delete_subagent(\"{}\") before completion: {error}", run.id),
                }, now_ms_i64()),
                None => create_rlm_child_terminal_notice_message(RlmChildTerminalNoticeDetails {
                    kind: "cancelled".into(), child_id: run.id.clone(), session_name: run.session_name.clone(),
                    reason: run.error.clone().or_else(|| Some("Deleted by parent orchestrator".into())), last_assistant_text_preview: None,
                }, now_ms_i64()),
            };
            let slot = if error.is_some() {
                &mut run.deletion_failure_notice
            } else {
                &mut run.deletion_notice
            };
            let existing = slot.is_some();
            let pending = slot
                .get_or_insert_with(|| Arc::new(create_agent_message_deferred()))
                .clone();
            (notice, pending, existing)
        };
        if existing {
            let _ = pending.wait().await;
            return;
        }
        match self.defer_rlm_terminal_notice(custom_notice(notice)).await {
            Ok(()) => pending.resolve(),
            Err(error) => {
                eprintln!("Could not publish subagent deletion: {error}");
                pending.reject(error);
            }
        }
    }

    fn emit_child_run_update(&self, run: &Arc<Mutex<RlmChildRun>>) {
        let current = run.lock().unwrap().clone();
        let child = self.rlm_child_snapshot_for_run(&current, None);
        let serialized = serde_json::to_string(&child).ok();
        {
            let mut run = run.lock().unwrap();
            if run.last_emitted_update == serialized {
                return;
            }
            run.last_emitted_update = serialized;
        }
        self.emit(AgentSessionEvent::RlmChildUpdate { child });
    }

    /// Stall visibility for one child run: when the live tool-call age crosses
    /// `DEFAULT_RLM_CHILD_TOOL_STALL_NOTICE_MS`, emit one `RlmChildUpdate`
    /// carrying the stall summary plus the notice wording. The last-notified
    /// tool-call start is tracked on the run, so each stalled call notifies at
    /// most once. Piggybacks on roster and collect reads; a stall nobody
    /// observes emits nothing, and no timer is spawned.
    pub(super) fn maybe_emit_rlm_child_stall_notice(&self, run: &Arc<Mutex<RlmChildRun>>) {
        let (current, notice) = {
            let mut guard = run.lock().unwrap();
            let Some(activity) = guard.activity.clone() else { return };
            if activity.kind != "executing" {
                return;
            }
            let Some(since_ms) = activity.since_ms else { return };
            if guard.stall_notice_since_ms == Some(since_ms) {
                return;
            }
            let elapsed_ms = now_ms() - since_ms;
            if elapsed_ms <= DEFAULT_RLM_CHILD_TOOL_STALL_NOTICE_MS as f64 {
                return;
            }
            guard.stall_notice_since_ms = Some(since_ms);
            let notice = rlm_child_stall_notice_text(
                &guard.session_name,
                activity.tool_name.as_deref(),
                elapsed_ms,
            );
            (guard.clone(), notice)
        };
        let mut child = self.rlm_child_snapshot_for_run(&current, None);
        child.stall_notice = Some(notice);
        self.emit(AgentSessionEvent::RlmChildUpdate { child });
    }

    async fn execute_child_run(
        self: &Arc<Self>,
        run: Arc<Mutex<RlmChildRun>>,
        options: CreateRlmSubagentRuntimeOptions,
        parent_assistant: Option<AssistantMessage>,
    ) -> Result<(), String> {
        let publish = options.on_session_published.clone();
        let runtime = self.create_rlm_subagent_runtime(options).await?;
        let child = runtime.session;
        child.persist_inherited_execution_mode()?;
        if let Some(publish) = publish {
            publish(&child);
        }
        {
            let mut run = run.lock().unwrap();
            if run.status == "cancelled" {
                return Err(run
                    .error
                    .clone()
                    .unwrap_or_else(|| "RLM child cancelled".into()));
            }
            run.status = "running".into();
        }
        self.emit_child_run_update(&run);
        let weak_parent = Arc::downgrade(self);
        let weak_run = Arc::downgrade(&run);
        let running_tools = Arc::new(AtomicU64::new(0));
        let weak_child = Arc::downgrade(&child);
        let unsubscribe = child.subscribe(Arc::new(move |event| {
            let (Some(parent), Some(run)) = (weak_parent.upgrade(), weak_run.upgrade()) else { return; };
            if matches!(event, AgentSessionEvent::RlmChildUpdate { .. }) { parent.emit(event); return; }
            let value = serde_json::to_value(&event).unwrap_or(Value::Null);
            let kind = event.type_name();
            let assistant = value.get("message").filter(|message| message.get("role").and_then(Value::as_str) == Some("assistant"))
                .and_then(|message| serde_json::from_value::<AssistantMessage>(message.clone()).ok());
            if kind == "message_end" {
                if let (Some(target), Some(assistant)) = (&parent_assistant, &assistant) {
                    if !matches!(assistant.stop_reason.as_str(), "error" | "aborted") {
                        let messages = weak_child.upgrade().map(|child| child.messages()).unwrap_or_default();
                        let index = messages.iter().position(|message| matches!(message, AgentMessage::Message(Message::Assistant(candidate)) if assistant_message_key(candidate) == assistant_message_key(assistant))).unwrap_or(messages.len());
                        parent.attribute_child_completion(target, assistant, rlm_child_usage_origin(&messages, index), false);
                    }
                }
            }
            {
                let mut run = run.lock().unwrap();
                apply_rlm_child_run_event(&mut run, &running_tools, kind, &value, assistant.as_ref());
            }
            parent.emit_child_run_update(&run);
        }));
        run.lock().unwrap().unsubscribe = Some(unsubscribe);
        let current = run.lock().unwrap().clone();
        // OCC-2 S2 seam (OCC-2 integration design, section 2): wrap the
        // spawn prompt when the feature is enabled and a dialect is
        // negotiated for the child. The model-visible prompt carries the
        // symbolic section of a dual payload; the durable `message` detail
        // keeps the full dual body.
        let spawn_policy = self
            .occ2_spawn_send_policy(&child.session_id(), &current.prompt)
            .await;
        let dual_prompt = wrap_outgoing_body(&current.prompt, &spawn_policy);
        let visible_prompt = agent_session_message_prompt_body(&dual_prompt).to_string();
        let content = format!("[task from parent]\n\n{}", visible_prompt);
        let before_reply = child.parent_reply_count.load(Ordering::SeqCst);
        child.prompt_and_wait(&content, Some(PromptOptions {
            expand_prompt_templates: Some(false), source: Some(crate::core::session_action_store::InputSource::Extension),
            custom_message: Some(CustomMessage {
                role: "custom".into(), custom_type: AGENT_MESSAGE_CUSTOM_TYPE.into(), content: CustomMessageContent::Text(content.clone()), display: true,
                details: Some(serde_json::json!({ "id": format!("spawn:{}", current.id), "message": dual_prompt, "target": { "sessionId": child.session_id() }, "from": { "sessionId": self.session_id(), "sessionName": self.session_name(), "activeSessionId": self.current_active_session_id().await }, "fromRelationship": "parent" })),
                timestamp: now_ms_i64(),
            }), ..Default::default()
        })).await?;
        child.wait_for_rlm_quiescence(None).await?;
        {
            let mut run = run.lock().unwrap();
            if let Some(error) = &run.error {
                return Err(error.clone());
            }
            let continuation = child.rlm_continuation.lock().unwrap();
            if continuation.terminal_status.as_deref() == Some("failed") {
                run.status = "error".into();
                run.error = Some(
                    continuation
                        .pending_result
                        .as_ref()
                        .and_then(|result| result.reason.clone())
                        .unwrap_or_else(|| "Child reported failure".into()),
                );
            } else {
                run.status = "done".into();
            }
            // An explicit reply already delivered the result through agent_message.
            if child.parent_reply_count.load(Ordering::SeqCst) != before_reply {
                run.suppress_terminal_notice = Some(true);
            }
        }
        Ok(())
    }

    fn attribute_child_completion(
        self: &Arc<Self>,
        target: &AssistantMessage,
        child: &AssistantMessage,
        origin: &'static str,
        after_drain: bool,
    ) {
        // Usage changes after each child completion; identify the parent message
        // without its mutable usage so later completions still find the same row.
        let entry = self
            .session_manager
            .lock()
            .unwrap()
            .get_entries()
            .into_iter()
            .find(|entry| {
                entry.get("type").and_then(Value::as_str) == Some("message")
                    && entry
                        .get("message")
                        .and_then(|message| {
                            serde_json::from_value::<AssistantMessage>(message.clone()).ok()
                        })
                        .is_some_and(|mut message| {
                            message.usage = target.usage.clone();
                            assistant_message_key(&message) == assistant_message_key(target)
                        })
            });
        let Some(entry) = entry else {
            if !after_drain {
                let parent = self.clone();
                let target = target.clone();
                let child = child.clone();
                tokio::spawn(async move {
                    parent.await_agent_event_queue().await;
                    parent.attribute_child_completion(&target, &child, origin, true);
                });
            }
            return;
        };
        let Some(id) = entry.get("id").and_then(Value::as_str) else {
            return;
        };
        let mut manager = self.session_manager.lock().unwrap();
        let current = manager
            .get_entry(id)
            .and_then(|entry| entry.get("message").cloned())
            .and_then(|message| serde_json::from_value::<AssistantMessage>(message).ok());
        let mut usage = current
            .map(|message| message.usage)
            .unwrap_or_else(|| target.usage.clone());
        attribute_child_usage(&mut usage, &child.usage);
        if let Err(error) =
            manager.append_child_usage_attribution(id, &child.usage, &usage, Some(origin))
        {
            eprintln!("Could not persist subagent usage: {error}");
        }
        drop(manager);
        *self.own_usage_memo.lock().unwrap() = None;
    }

    async fn settle_child_run(
        self: &Arc<Self>,
        run: Arc<Mutex<RlmChildRun>>,
        options: CreateRlmSubagentRuntimeOptions,
        outcome: Result<(), String>,
        duration_ms: f64,
    ) {
        let completed = outcome.is_ok();
        {
            let mut run = run.lock().unwrap();
            if let Err(error) = outcome {
                run.publication.reject(error.clone());
                if run.status != "cancelled" {
                    run.status = "error".into();
                    run.error = Some(error);
                }
            }
            run.duration_ms = Some(duration_ms);
            run.activity = None;
        }
        let current = run.lock().unwrap().clone();
        if let Some(child) = &current.session {
            if current.status != "cancelled" {
                let request = child
                    .semantic_edges
                    .lock()
                    .unwrap()
                    .last_committed_request_id();
                if let Some(request) = request {
                    self.semantic_edges
                        .lock()
                        .unwrap()
                        .record_child_returned(&child.session_id(), Some(&request));
                }
            }
        }
        if current.session.is_none() {
            let mut child = self.rlm_child_snapshot_for_run(&current, None);
            child.status = "cancelled".into();
            self.emit(AgentSessionEvent::RlmChildUpdate { child });
        } else {
            self.emit_child_run_update(&run);
        }
        if current.detached_deletion.is_none() && current.suppress_terminal_notice != Some(true) {
            let notice = if current.status == "error" {
                create_rlm_child_failure_message(
                    RlmChildFailureDetails {
                        child_id: current.id.clone(),
                        session_name: current.session_name.clone(),
                        error: current
                            .error
                            .clone()
                            .unwrap_or_else(|| "unknown error".into()),
                    },
                    now_ms_i64(),
                )
            } else {
                let preview = current
                    .session
                    .as_ref()
                    .and_then(|child| child.find_last_assistant_message())
                    .map(|message| read_assistant_text(&message));
                create_rlm_child_terminal_notice_message(
                    RlmChildTerminalNoticeDetails {
                        kind: if current.status == "cancelled" {
                            "cancelled"
                        } else {
                            "completed_without_reply"
                        }
                        .into(),
                        child_id: current.id.clone(),
                        session_name: current.session_name.clone(),
                        reason: current.error.clone(),
                        last_assistant_text_preview: preview
                            .map(|text| text.chars().take(8000).collect()),
                    },
                    now_ms_i64(),
                )
            };
            if let Err(error) = self.defer_rlm_terminal_notice(custom_notice(notice)).await {
                eprintln!("Could not publish subagent completion: {error}");
            }
        }
        if current.detached_deletion.is_some() {
            run.lock().unwrap().deletion_run_finished = Some(true);
            if let Some(child) = current.session.clone() {
                let cleanup = self.ensure_rlm_run_deletion_cleanup(&current, &child).await;
                let snapshot = run.lock().unwrap().clone();
                if self
                    .observe_rlm_run_deletion_cleanup(
                        snapshot,
                        current.detached_deletion.clone().unwrap(),
                        child,
                        cleanup,
                    )
                    .await
                {
                    let snapshot = run.lock().unwrap().clone();
                    self.finish_rlm_run_deletion(&snapshot).await;
                }
            } else {
                self.finish_rlm_run_deletion(&current).await;
            }
            return;
        }
        if self.finish_retained_rlm_run(&run, &current) { return; }
        let retained = completed
            && current
                .session
                .as_ref()
                .is_some_and(|child| self.register_rlm_child_session(&current.id, child.clone()));
        if retained {
            self.active_rlm_child_runs
                .lock()
                .unwrap()
                .remove(&current.id);
            if let Some(unsubscribe) = current.unsubscribe.clone() {
                self.rlm_child_unsubscribes
                    .lock()
                    .unwrap()
                    .insert(current.id.clone(), unsubscribe);
            }
        } else {
            // This is the last synchronous boundary before any release await.
            // Stop and cleanup compete under one parent admission gate.
            if let Some(child) = &current.session {
                if !self.try_claim_rlm_runtime_release(&current.id, child) {
                    if self.finish_retained_rlm_run(&run, &current) { return; }
                    // Explicit deletion may have superseded retention under the same gate.
                    if !self.try_claim_rlm_runtime_release(&current.id, child) { return; }
                }
            }
            if let Some(unsubscribe) = &current.unsubscribe {
                unsubscribe();
            }
            if let Some(child) = &current.session {
                let host = self.subagent_runtime_host.lock().unwrap().clone();
                if let Some(host) = host {
                    if let Err(error) = host
                        .release_rlm_subagent_runtime(
                            RlmSubagentRuntime {
                                session: child.clone(),
                            },
                            options,
                            &current.status,
                        )
                        .await
                    {
                        eprintln!("Could not release subagent: {error}");
                    }
                } else {
                    child.dispose_async(None).await;
                }
            }
            if current.status != "error" {
                self.active_rlm_child_runs
                    .lock()
                    .unwrap()
                    .remove(&current.id);
            }
        }
        {
            let mut run = run.lock().unwrap();
            run.unsubscribe = None;
            run.abort = Arc::new(noop_rlm_child_abort);
            run.settled = true;
            run.settlement.resolve();
        }
        self.unsettled_rlm_child_runs
            .lock()
            .unwrap()
            .retain(|other| !Arc::ptr_eq(other, &run));
        self.maybe_resume_goal_continuation_after_rlm_work();
    }

    /// Retain the addressable child when stop wins before release admission.
    /// No await or destructive action is allowed between that decision and registration.
    pub(in crate::core::agent_session) fn finish_retained_rlm_run(self: &Arc<Self>, run: &Arc<Mutex<RlmChildRun>>, current: &RlmChildRun) -> bool {
        let _admission = self.rlm_child_lifecycle_admission.lock().unwrap();
        if self.rlm_child_release_claims.lock().unwrap().contains(&current.id) { return false; }
        let Some(generation) = self.retained_stop_ids.lock().unwrap().get(&current.id).cloned() else { return false; };
        if let Some(child) = &current.session {
            child.request_retained_stop(&current.id, &generation);
            if !self.register_rlm_child_session(&current.id, child.clone()) {
                // A failed host row update must never turn retain into destructive release.
                self.rlm_child_sessions.lock().unwrap().insert(current.id.clone(), RetainedRlmChild {
                    session: child.clone(), run: Some(run.clone()),
                });
            }
        }
        if current.session.is_some() {
            self.active_rlm_child_runs.lock().unwrap().remove(&current.id);
        }
        if let Some(unsubscribe) = current.unsubscribe.clone() {
            self.rlm_child_unsubscribes.lock().unwrap().insert(current.id.clone(), unsubscribe);
        }
        let mut current_run = run.lock().unwrap();
        current_run.settled = true; // Initial task only; retained stop has separate acknowledgements.
        current_run.settlement.resolve();
        drop(current_run);
        self.unsettled_rlm_child_runs.lock().unwrap().retain(|other| !Arc::ptr_eq(other, run));
        true
    }

    pub fn register_rlm_child_session(
        self: &Arc<Self>,
        child_id: &str,
        child: Arc<AgentSession>,
    ) -> bool {
        if self.disposed.load(Ordering::SeqCst)
            || self.disposing.load(Ordering::SeqCst)
            || self
                .deleting_rlm_children
                .lock()
                .unwrap()
                .contains_key(child_id)
            || self
                .deleted_rlm_child_ids
                .lock()
                .unwrap()
                .contains(child_id)
        {
            return false;
        }
        let host = self.subagent_runtime_host.lock().unwrap().clone();
        if host.is_some_and(|host| !host.complete_rlm_subagent_runtime(child_id, &child)) {
            return false;
        }
        let run = self
            .active_rlm_child_runs
            .lock()
            .unwrap()
            .get(child_id)
            .cloned();
        self.rlm_child_sessions.lock().unwrap().insert(
            child_id.into(),
            RetainedRlmChild {
                session: child,
                run,
            },
        );
        true
    }

    pub fn get_rlm_child_snapshots(self: &Arc<Self>) -> Vec<RlmChildAgentSnapshot> {
        let mut snapshots = Vec::new();
        for owner in self.rlm_subtree_sessions() {
            let runs: Vec<_> = owner
                .active_rlm_child_runs
                .lock()
                .unwrap()
                .values()
                .cloned()
                .collect();
            let retained = owner.rlm_child_sessions.lock().unwrap().clone();
            let mut recorded = HashSet::new();
            for run in runs {
                let current = run.lock().unwrap().clone();
                if current.detached_deletion.is_some()
                    || owner
                        .deleting_rlm_children
                        .lock()
                        .unwrap()
                        .contains_key(&current.id)
                    || owner
                        .deleted_rlm_child_ids
                        .lock()
                        .unwrap()
                        .contains(&current.id)
                    || owner.is_unbound_terminal_rlm_child_run(&current)
                {
                    continue;
                }
                recorded.insert(current.id.clone());
                // Stall visibility piggybacks on roster reads: a silent child
                // whose tool call outlived the threshold gets one notice.
                owner.maybe_emit_rlm_child_stall_notice(&run);
                snapshots.push(owner.rlm_child_snapshot_for_run(&current, None));
            }
            for (id, retained) in retained {
                if recorded.contains(&id)
                    || owner
                        .deleting_rlm_children
                        .lock()
                        .unwrap()
                        .contains_key(&id)
                    || owner.deleted_rlm_child_ids.lock().unwrap().contains(&id)
                {
                    continue;
                }
                let child = retained.session;
                let mut snapshot = match retained.run {
                    Some(run) => {
                        owner.rlm_child_snapshot_for_run(&run.lock().unwrap().clone(), Some(child))
                    }
                    None => owner.rlm_child_snapshot_for_session(&id, &child),
                };
                if owner
                    .rlm_child_cleanup_failures
                    .lock()
                    .unwrap()
                    .contains_key(&id)
                {
                    snapshot.status = "cancelled".into();
                }
                snapshots.push(snapshot);
            }
        }
        snapshots
    }

    pub fn get_rlm_child_session(self: &Arc<Self>, child_id: &str) -> Option<Arc<Self>> {
        for owner in self.rlm_subtree_sessions() {
            let active = owner
                .active_rlm_child_runs
                .lock()
                .unwrap()
                .get(child_id)
                .and_then(|run| run.lock().unwrap().session.clone());
            if active.is_some() {
                return active;
            }
            if let Some(child) = owner.rlm_child_sessions.lock().unwrap().get(child_id) {
                return Some(child.session.clone());
            }
        }
        None
    }

    pub fn cancel_rlm_child_run_by_id(self: &Arc<Self>, child_id: &str, reason: &str) -> bool {
        for owner in self.rlm_subtree_sessions() {
            let run = owner
                .active_rlm_child_runs
                .lock()
                .unwrap()
                .get(child_id)
                .cloned();
            if let Some(run) = run {
                let current = run.lock().unwrap().clone();
                if !matches!(current.status.as_str(), "running" | "queued") && !current.settled {
                    run.lock().unwrap().suppress_terminal_notice = Some(true);
                    return true;
                }
                if owner.cancel_rlm_child_run(&current, reason) {
                    owner.emit_child_run_update(&run);
                    return true;
                }
            }
            if let Some(child) = owner.get_rlm_child_session(child_id) {
                if child.is_session_active() {
                    tokio::spawn(Box::pin(async move {
                        let _ = child.abort().await;
                    }));
                    return true;
                }
            }
        }
        false
    }
}

#[cfg(test)]
mod rlm_child_activity_tests {
    use super::*;
    use crate::core::rlm_runtime::RlmCollectResultEntry;
    use pi_agent_core::types::{
        AgentEvent, AgentMessage, AgentState, ShouldStopAfterTurnContext, StreamFn,
    };
    use pi_ai::types::{Message, OnPayload, OnResponse};
    use serde_json::json;

    type Listener =
        Arc<dyn Fn(AgentEvent, Option<CancellationToken>) -> BoxFuture<()> + Send + Sync>;

    /// Minimal scripted agent: idle, no queue, `prompt` rejects so an accidental
    /// dispatch is loud instead of silently completing (same shape as the
    /// sibling test suites).
    struct ScriptedAgent {
        state: Mutex<AgentState>,
        stream_fn: Mutex<StreamFn>,
    }

    impl ScriptedAgent {
        fn new() -> Arc<Self> {
            Arc::new(Self {
                state: Mutex::new(AgentState::default()),
                stream_fn: Mutex::new(pi_agent_core::agent::default_stream_fn()),
            })
        }
    }

    impl AgentHandle for ScriptedAgent {
        fn state(&self) -> AgentState {
            self.state.lock().unwrap().clone()
        }

        fn set_state(&self, state: AgentState) {
            *self.state.lock().unwrap() = state;
        }

        fn subscribe(&self, _listener: Listener) -> Box<dyn Fn() + Send + Sync> {
            Box::new(|| {})
        }

        fn set_before_tool_call(&self, _hook: BeforeToolCallHook) {}
        fn set_after_tool_call(&self, _hook: AfterToolCallHook) {}
        fn set_get_continuation_messages(&self, _hook: GetContinuationMessagesHook) {}
        fn set_before_request(&self, _hook: BeforeRequestHook) {}
        fn set_should_stop_before_turn(&self, _hook: Arc<dyn Fn() -> bool + Send + Sync>) {}
        fn set_should_stop_after_turn(
            &self,
            _hook: Arc<
                dyn Fn(ShouldStopAfterTurnContext) -> BoxFuture<bool> + Send + Sync,
            >,
        ) {
        }

        fn set_stream_fn(&self, stream_fn: StreamFn) {
            *self.stream_fn.lock().unwrap() = stream_fn;
        }

        fn stream_fn(&self) -> StreamFn {
            self.stream_fn.lock().unwrap().clone()
        }

        fn abort(&self) {}

        fn wait_for_idle(&self) -> BoxFuture<()> {
            Box::pin(async {})
        }

        fn prompt(&self, _messages: Vec<AgentMessage>) -> BoxFuture<Result<(), String>> {
            Box::pin(async { Err("scripted agent does not run turns".to_string()) })
        }

        fn continue_(&self) -> BoxFuture<Result<(), AgentContinueError>> {
            Box::pin(async { Ok(()) })
        }

        fn is_streaming(&self) -> bool {
            self.state.lock().unwrap().is_streaming
        }

        fn has_queued_messages(&self) -> bool {
            false
        }

        fn clear_all_queues(&self) {}

        fn remove_queued_messages(
            &self,
            _predicate: Arc<dyn Fn(&AgentMessage) -> bool + Send + Sync>,
        ) -> Vec<AgentMessage> {
            Vec::new()
        }

        fn follow_up(&self, _message: AgentMessage) {}
        fn set_follow_up_mode(&self, _mode: String) {}
        fn set_steering_mode(&self, _mode: String) {}

        fn set_convert_to_llm(
            &self,
            _convert: Arc<dyn Fn(Vec<AgentMessage>) -> BoxFuture<Vec<Message>> + Send + Sync>,
        ) {
        }

        fn set_transform_context(
            &self,
            _transform: Arc<
                dyn Fn(Vec<AgentMessage>, Option<CancellationToken>) -> BoxFuture<Vec<AgentMessage>>
                    + Send
                    + Sync,
            >,
        ) {
        }

        fn set_get_api_key(
            &self,
            _get_api_key: Arc<dyn Fn(String) -> BoxFuture<Option<String>> + Send + Sync>,
        ) {
        }

        fn set_on_payload(&self, _hook: OnPayload) {}
        fn set_on_response(&self, _hook: OnResponse) {}
        fn set_tool_execution(&self, _mode: String) {}
        fn performance_metrics(&self) -> Option<AgentLoopPerformanceMetrics> {
            None
        }
        fn set_performance_metrics(&self, _metrics: Option<AgentLoopPerformanceMetrics>) {}
        fn signal(&self) -> Option<CancellationToken> {
            None
        }
    }

    fn stall_test_session() -> Arc<AgentSession> {
        let cwd = std::env::temp_dir().to_string_lossy().to_string();
        let settings = Arc::new(Mutex::new(
            crate::core::settings_manager::SettingsManager::in_memory(
                serde_json::json!({
                    "autoRefine": {"enabled": false},
                    "retry": {"enabled": false},
                    "compaction": {"enabled": false},
                    "telemetryEnabled": false,
                    "agentTracesEnabled": false,
                })
                .as_object()
                .unwrap()
                .clone(),
            ),
        ));
        let manager =
            crate::core::session_manager::SessionManager::in_memory(Some(&cwd), Some(&cwd))
                .unwrap();
        let loader = Arc::new(crate::core::resource_loader::DefaultResourceLoader::new(
            crate::core::resource_loader::DefaultResourceLoaderOptions {
                cwd: cwd.clone(),
                agent_dir: cwd.clone(),
                settings_manager: Some(settings.clone()),
                no_extensions: true,
                no_skills: true,
                no_prompt_templates: true,
                no_themes: true,
                no_context_files: true,
                bundled_skills_dir: Some(None),
                ..Default::default()
            },
        ));
        AgentSession::new(AgentSessionConfig {
            agent: ScriptedAgent::new() as Arc<dyn AgentHandle>,
            session_manager: Arc::new(Mutex::new(manager)),
            settings_manager: settings,
            service_tier_preference: None,
            cwd: cwd.clone(),
            agent_dir: Some(cwd.clone()),
            scoped_models: None,
            resource_loader: loader,
            custom_tools: None,
            model_registry: Arc::new(Mutex::new(
                crate::core::model_registry::ModelRegistry::in_memory(
                    crate::core::auth_storage::AuthStorage::in_memory(Default::default(), None),
                ),
            )),
            initial_active_tool_names: None,
            allowed_tool_names: None,
            include_goals: Some(false),
            agent_message_controller: None,
            agent_observe_controller: None,
            include_compact_skill: Some(false),
            rlm_heartbeat_controller: None,
            mcp_manager: None,
            base_tools_override: Some(Vec::new()),
            extension_runner_ref: None,
            session_start_event: None,
            rlm_depth: Some(0),
            rlm_max_depth: Some(2),
            rlm_session_dir: None,
            rlm_parent_node_id: None,
            rlm_parent_agent: None,
            semantic_parent_session_id: None,
            semantic_spawned_by_request_id: None,
            subagent_runtime_host: None,
            autonomous: None,
            prewarm_ipython_kernel: Some(false),
            auto_refine_reviewer: None,
            serialized_refine: None,
            initial_goal: None,
        })
        .unwrap()
    }

    /// `tool_execution_start` records the tool name and an activity start time.
    #[test]
    fn tool_execution_start_sets_since_ms_and_tool_name() {
        let mut run = empty_rlm_child_run("child");
        let running_tools = AtomicU64::new(0);
        let before = now_ms();
        apply_rlm_child_run_event(
            &mut run,
            &running_tools,
            "tool_execution_start",
            &json!({"toolCallId": "c1", "toolName": "ipython", "args": {}}),
            None,
        );
        let after = now_ms();
        let activity = run.activity.as_ref().expect("activity recorded");
        assert_eq!(activity.kind, "executing");
        assert_eq!(activity.tool_name.as_deref(), Some("ipython"));
        let since_ms = activity.since_ms.expect("sinceMs recorded");
        assert!(
            since_ms >= before && since_ms <= after,
            "sinceMs must be the observe time when the event carries no timestamp: {since_ms}"
        );
        assert_eq!(run.tool_use_count, 1.0);
        assert_eq!(running_tools.load(Ordering::SeqCst), 1);
    }

    /// `tool_execution_end` returns the run to waiting with a fresh start time
    /// and clears the stall-notice marker for the finished call.
    #[test]
    fn tool_execution_end_resets_activity_and_stall_tracking() {
        let mut run = empty_rlm_child_run("child");
        let running_tools = AtomicU64::new(0);
        apply_rlm_child_run_event(
            &mut run,
            &running_tools,
            "tool_execution_start",
            &json!({"toolCallId": "c1", "toolName": "ipython", "args": {}}),
            None,
        );
        run.stall_notice_since_ms = run.activity.as_ref().and_then(|a| a.since_ms);
        let stale_since_ms = run.activity.as_ref().and_then(|a| a.since_ms).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(2));
        apply_rlm_child_run_event(
            &mut run,
            &running_tools,
            "tool_execution_end",
            &json!({"toolCallId": "c1", "toolName": "ipython", "result": {}, "isError": false}),
            None,
        );
        let activity = run.activity.as_ref().expect("waiting activity recorded");
        assert_eq!(activity.kind, "waiting");
        assert_eq!(activity.tool_name, None);
        let fresh_since_ms = activity.since_ms.expect("fresh sinceMs recorded");
        assert!(
            fresh_since_ms > stale_since_ms,
            "waiting sinceMs must be fresh, not the finished tool call's start"
        );
        assert_eq!(run.stall_notice_since_ms, None);
        assert_eq!(running_tools.load(Ordering::SeqCst), 0);
    }

    /// Assistant message events seed the writing activity from the message's
    /// own timestamp when the event JSON carries one.
    #[test]
    fn writing_activity_uses_event_message_timestamp() {
        let assistant = AssistantMessage {
            content: vec![pi_ai::types::ContentBlock::Text(pi_ai::types::TextContent::new(
                "partial",
            ))],
            stop_reason: "stop".to_string(),
            timestamp: 777_000,
            ..AssistantMessage::default()
        };
        let mut run = empty_rlm_child_run("child");
        apply_rlm_child_run_event(
            &mut run,
            &AtomicU64::new(0),
            "message_update",
            &json!({"message": serde_json::to_value(&assistant).unwrap()}),
            Some(&assistant),
        );
        let activity = run.activity.as_ref().expect("writing activity recorded");
        assert_eq!(activity.kind, "writing");
        assert_eq!(activity.since_ms, Some(777_000.0));
    }

    /// Roster and collect summaries recompute a live elapsed value at read
    /// time, and an executing tool crosses into `stalledMs` past the threshold.
    #[tokio::test]
    async fn summaries_report_live_elapsed_and_stall_past_threshold() {
        let session = stall_test_session();
        let stalled = Arc::new(Mutex::new({
            let mut run = empty_rlm_child_run("stalled");
            run.status = RLM_CHILD_AGENT_STATUS_RUNNING.to_string();
            run.session_name = "scanner".into();
            run.activity = Some(RlmChildAgentActivity {
                kind: "executing".into(),
                tool_name: Some("ipython".into()),
                since_ms: Some(now_ms() - DEFAULT_RLM_CHILD_TOOL_STALL_NOTICE_MS as f64 - 60_000.0),
            });
            run
        }));
        let fresh = Arc::new(Mutex::new({
            let mut run = empty_rlm_child_run("fresh");
            run.status = RLM_CHILD_AGENT_STATUS_RUNNING.to_string();
            run.session_name = "quick".into();
            run.activity = Some(RlmChildAgentActivity {
                kind: "executing".into(),
                tool_name: Some("bash".into()),
                since_ms: Some(now_ms()),
            });
            run
        }));
        let untracked = Arc::new(Mutex::new({
            let mut run = empty_rlm_child_run("untracked");
            run.status = RLM_CHILD_AGENT_STATUS_RUNNING.to_string();
            run.session_name = "legacy".into();
            run.activity = Some(RlmChildAgentActivity {
                kind: "executing".into(),
                tool_name: Some("read".into()),
                since_ms: None,
            });
            run
        }));
        {
            let mut runs = session.active_rlm_child_runs.lock().unwrap();
            runs.insert("stalled".into(), stalled.clone());
            runs.insert("fresh".into(), fresh.clone());
            runs.insert("untracked".into(), untracked.clone());
        }
        let snapshots = session.get_rlm_child_snapshots();
        let by_id = |id: &str| {
            snapshots
                .iter()
                .find(|snapshot| snapshot.id == id)
                .unwrap_or_else(|| panic!("missing snapshot {id}"))
        };
        let stalled_snapshot = by_id("stalled");
        assert_eq!(stalled_snapshot.activity.as_ref().unwrap().kind, "executing");
        let elapsed = stalled_snapshot.activity_elapsed_ms.expect("live elapsed");
        assert!(
            elapsed > DEFAULT_RLM_CHILD_TOOL_STALL_NOTICE_MS as f64,
            "stalled elapsed must exceed the threshold: {elapsed}"
        );
        assert_eq!(stalled_snapshot.stalled_ms, Some(elapsed));
        let fresh_snapshot = by_id("fresh");
        let fresh_elapsed = fresh_snapshot.activity_elapsed_ms.expect("live elapsed");
        assert!(fresh_elapsed < 5_000.0, "fresh elapsed must be small: {fresh_elapsed}");
        assert_eq!(fresh_snapshot.stalled_ms, None);
        let untracked_snapshot = by_id("untracked");
        assert_eq!(untracked_snapshot.activity_elapsed_ms, None);
        assert_eq!(untracked_snapshot.stalled_ms, None);

        let collected = session.collect_rlm_children(&[], 0).await.unwrap();
        let entry = collected
            .results
            .iter()
            .find(|entry| entry.rlm_child_id == "stalled")
            .expect("stalled entry collected");
        let collected_elapsed = entry.activity_elapsed_ms.expect("live elapsed");
        assert!(
            collected_elapsed >= elapsed && collected_elapsed < elapsed + 10_000.0,
            "collect recomputes the live elapsed at read time: {collected_elapsed} vs {elapsed}"
        );
        assert_eq!(entry.stalled_ms, Some(collected_elapsed));
        let fresh_entry = collected
            .results
            .iter()
            .find(|entry| entry.rlm_child_id == "fresh")
            .expect("fresh entry collected");
        assert!(fresh_entry.stalled_ms.is_none());
    }

    /// One stall notice per stalled tool call: repeated reads stay quiet, a new
    /// stalled call notifies again.
    #[tokio::test]
    async fn stall_notice_emitted_once_per_stalled_call() {
        let session = stall_test_session();
        let notices: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        {
            let notices = notices.clone();
            session.subscribe(Arc::new(move |event| {
                if let AgentSessionEvent::RlmChildUpdate { child } = event {
                    if let Some(notice) = &child.stall_notice {
                        notices.lock().unwrap().push(notice.clone());
                    }
                }
            }));
        }
        let run = Arc::new(Mutex::new({
            let mut run = empty_rlm_child_run("stalled");
            run.status = RLM_CHILD_AGENT_STATUS_RUNNING.to_string();
            run.session_name = "scanner".into();
            run.activity = Some(RlmChildAgentActivity {
                kind: "executing".into(),
                tool_name: Some("ipython".into()),
                since_ms: Some(now_ms() - DEFAULT_RLM_CHILD_TOOL_STALL_NOTICE_MS as f64 - 60_000.0),
            });
            run
        }));
        session
            .active_rlm_child_runs
            .lock()
            .unwrap()
            .insert("stalled".into(), run.clone());

        session.get_rlm_child_snapshots();
        assert_eq!(
            notices.lock().unwrap().len(),
            1,
            "first roster read emits exactly one stall notice"
        );
        assert_eq!(
            notices.lock().unwrap()[0],
            "Child scanner tool ipython has been running for 11 min"
        );
        session.collect_rlm_children(&[], 0).await.unwrap();
        session.get_rlm_child_snapshots();
        assert_eq!(
            notices.lock().unwrap().len(),
            1,
            "repeated reads must not repeat the notice for one stalled call"
        );

        // The stalled call ends; the next tool call stalls again and notifies again.
        let mut guard = run.lock().unwrap();
        apply_rlm_child_run_event(
            &mut guard,
            &AtomicU64::new(0),
            "tool_execution_start",
            &json!({"toolCallId": "c2", "toolName": "ipython", "args": {}}),
            None,
        );
        guard.activity = Some(RlmChildAgentActivity {
            kind: "executing".into(),
            tool_name: Some("ipython".into()),
            since_ms: Some(now_ms() - DEFAULT_RLM_CHILD_TOOL_STALL_NOTICE_MS as f64 - 120_000.0),
        });
        drop(guard);
        session.get_rlm_child_snapshots();
        assert_eq!(
            notices.lock().unwrap().len(),
            2,
            "a new stalled tool call notifies once more"
        );
    }

    /// Old serialized state without the new fields still deserializes, and
    /// absent values stay out of the wire shape.
    #[test]
    fn serde_old_json_without_new_fields_still_deserializes() {
        let activity: RlmChildAgentActivity =
            serde_json::from_value(json!({"kind": "executing", "toolName": "ipython"})).unwrap();
        assert_eq!(activity.kind, "executing");
        assert_eq!(activity.tool_name.as_deref(), Some("ipython"));
        assert_eq!(activity.since_ms, None);
        assert!(serde_json::to_value(&activity).unwrap().get("sinceMs").is_none());

        let snapshot: RlmChildAgentSnapshot = serde_json::from_value(json!({
            "id": "child",
            "label": "child agent",
            "status": "running",
            "sessionDir": "/synthetic/child",
            "activity": {"kind": "waiting"},
        }))
        .unwrap();
        assert_eq!(snapshot.activity_elapsed_ms, None);
        assert_eq!(snapshot.stalled_ms, None);
        assert_eq!(snapshot.stall_notice, None);
        let serialized = serde_json::to_value(&RlmChildAgentSnapshot {
            activity_elapsed_ms: Some(1_234.0),
            ..snapshot.clone()
        })
        .unwrap();
        assert_eq!(serialized["activityElapsedMs"], json!(1_234.0));
        assert!(serialized.get("stalledMs").is_none());
        assert!(serialized.get("stallNotice").is_none());

        let entry: RlmCollectResultEntry = serde_json::from_value(json!({
            "rlm_child_id": "child",
            "session_dir": "/synthetic/child",
            "status": "running",
            "settled": false,
        }))
        .unwrap();
        assert_eq!(entry.activity_elapsed_ms, None);
        assert_eq!(entry.stalled_ms, None);
        let entry_serialized =
            serde_json::to_value(&RlmCollectResultEntry {
                activity_elapsed_ms: Some(5.0),
                stalled_ms: Some(6.0),
                ..entry
            })
            .unwrap();
        assert_eq!(entry_serialized["activity_elapsed_ms"], json!(5.0));
        assert_eq!(entry_serialized["stalled_ms"], json!(6.0));
    }
}
