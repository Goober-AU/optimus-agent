//! Cancellation contract for the agent-loop tool-batch paths (wave-2 SB fixes).
//!
//! SB-1: cancelling a sequential (or parallel) tool batch must leave a
//! protocol-valid AgentContext: every tool call id issued by an assistant
//! message has exactly one matching tool result, and the skipped calls carry
//! the dedicated "Tool execution aborted" wording rather than nothing.
//! SB-3: an abort during the before_tool_call hook must leave the same
//! dedicated abort wording, not the raw display of an aborted future.
//!
//! Every test drives the real `agent_loop` entry point with the faux provider,
//! the same transport the in-crate loop tests use.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use pi_agent_core::agent_loop::{agent_loop, run_agent_loop, TOOL_EXECUTION_ABORTED_MESSAGE};
use pi_agent_core::types::{
    AgentContext, AgentEvent, AgentLoopConfig, AgentMessage, AgentTool, AgentToolResult,
    ToolExecutionMode,
};
use pi_ai::providers::faux::{faux_assistant_message, register_faux_provider, FauxResponseStep};
use pi_ai::types::{Message, ToolCall, UserContent, UserMessage};
use serde_json::json;
use tokio::time::timeout;
use tokio_util::sync::CancellationToken;

/// Bounded safety net so a stuck batch can never hang the test binary.
const BUDGET: Duration = Duration::from_secs(10);

fn user_prompt() -> Vec<AgentMessage> {
    vec![AgentMessage::from(UserMessage {
        role: "user".to_string(),
        content: UserContent::Text("go".to_string()),
        provider_context: None,
        timestamp: 1,
    })]
}

fn tool_call_response(calls: &[(&str, &str)]) -> FauxResponseStep {
    let mut message = faux_assistant_message("".into(), None);
    message.content = calls
        .iter()
        .map(|(id, name)| {
            pi_ai::types::ContentBlock::ToolCall(ToolCall::new(
                id.to_string(),
                name.to_string(),
                serde_json::Map::new(),
            ))
        })
        .collect();
    message.stop_reason = pi_ai::types::STOP_REASON_TOOL_USE.to_string();
    FauxResponseStep::Message(message)
}

/// The AgentContext-level protocol invariant: every tool call id issued by an
/// assistant message has exactly one matching tool result, in source order.
fn assert_every_tool_call_answered(context_messages: &[AgentMessage]) {
    let mut expected: Vec<&str> = Vec::new();
    let mut answered: Vec<&str> = Vec::new();
    for message in context_messages {
        match message {
            AgentMessage::Message(Message::Assistant(assistant)) => {
                for block in &assistant.content {
                    if let Some(call) = block.as_tool_call() {
                        expected.push(call.id.as_str());
                    }
                }
            }
            AgentMessage::Message(Message::ToolResult(result)) => {
                answered.push(result.tool_call_id.as_str());
            }
            _ => {}
        }
    }
    assert_eq!(
        answered, expected,
        "every assistant tool call id must have exactly one matching ToolResultMessage"
    );
}

fn tool_result_text(message: &AgentMessage) -> Option<&str> {
    match message {
        AgentMessage::Message(Message::ToolResult(result)) => {
            result.content.first().and_then(|block| match block {
                pi_ai::types::ImageOrTextContent::Text(text) => Some(text.text.as_str()),
                pi_ai::types::ImageOrTextContent::Image(_) => None,
            })
        }
        _ => None,
    }
}

fn is_error_result(message: &AgentMessage) -> bool {
    matches!(
        message,
        AgentMessage::Message(Message::ToolResult(result)) if result.is_error
    )
}

fn tool_results_of(messages: &[AgentMessage]) -> Vec<&AgentMessage> {
    messages
        .iter()
        .filter(|message| matches!(message, AgentMessage::Message(Message::ToolResult(_))))
        .collect()
}

/// Drives one `agent_loop` run to completion and returns
/// (agent-end new messages, all emitted events).
async fn run_agent_loop_collecting(
    signal: Option<CancellationToken>,
    context: AgentContext,
    config: AgentLoopConfig,
) -> (Vec<AgentMessage>, Vec<AgentEvent>) {
    let stream = agent_loop(user_prompt(), context, config, signal, None);
    let events: Arc<Mutex<Vec<AgentEvent>>> = Arc::new(Mutex::new(Vec::new()));
    let collected = events.clone();
    let drain = tokio::spawn(async move {
        while let Some(event) = stream.next().await {
            collected.lock().unwrap().push(event);
        }
    });
    timeout(BUDGET, drain)
        .await
        .expect("the cancelled loop must still terminate")
        .unwrap();
    let events = Arc::try_unwrap(events)
        .ok()
        .expect("the drain task must release its event handle")
        .into_inner()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut new_messages = Vec::new();
    for event in &events {
        if let AgentEvent::AgentEnd { messages } = event {
            new_messages = messages.clone();
        }
    }
    (new_messages, events)
}

/// Drives one `run_agent_loop` run to completion with a recording sink that
/// cancels the run when `cancel_on` fires, and returns (new messages, events).
async fn run_agent_loop_with_sink_cancelling_on(
    cancel_on: &'static str,
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationToken,
) -> (Vec<AgentMessage>, Vec<AgentEvent>) {
    let events: Arc<Mutex<Vec<AgentEvent>>> = Arc::new(Mutex::new(Vec::new()));
    let cancel = signal.clone();
    let emit: pi_agent_core::agent_loop::AgentEventSink = {
        let events = events.clone();
        Arc::new(move |event| {
            let events = events.clone();
            let cancel = cancel.clone();
            Box::pin(async move {
                if let AgentEvent::ToolExecutionEnd { tool_call_id, .. } = &event {
                    if tool_call_id == cancel_on {
                        cancel.cancel();
                    }
                }
                events.lock().unwrap().push(event);
                Ok(())
            })
        })
    };
    let new_messages = timeout(
        BUDGET,
        run_agent_loop(user_prompt(), context, config, emit, Some(signal), None),
    )
    .await
    .expect("the cancelled loop must still terminate")
    .expect("the loop must not fail");
    let events = Arc::try_unwrap(events)
        .ok()
        .expect("the sink must be released with the run")
        .into_inner()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    (new_messages, events)
}

/// SB-1: a run cancelled in the middle of a sequential tool batch must answer
/// every tool call. Before the fix the loop broke out of the sequential batch
/// without synthesizing results for the unstarted calls, persisting an
/// assistant message whose calls had no matching tool results.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sequential_cancellation_answers_every_assistant_tool_call() {
    let provider = register_faux_provider(None);
    provider.set_responses(vec![tool_call_response(&[
        ("call-1", "sequential-cancel"),
        ("call-2", "sequential-cancel"),
        ("call-3", "sequential-cancel"),
    ])]);
    let run_signal = CancellationToken::new();
    let tool = AgentTool {
        name: "sequential-cancel".to_string(),
        description: "returns normally; the run is cancelled after its result".to_string(),
        parameters: json!({"type": "object", "properties": {}}),
        label: "Sequential cancel".to_string(),
        prepare_arguments: None,
        execute: Arc::new(|_, _, _, _| {
            Box::pin(async {
                Ok(AgentToolResult::new(
                    vec![pi_agent_core::types::ContentBlock::text("first finished")],
                    json!({}),
                ))
            })
        }),
        execution_mode: Some(ToolExecutionMode::Sequential),
    };
    let mut config = AgentLoopConfig::new(provider.get_model());
    config.tool_execution = Some(ToolExecutionMode::Sequential);
    // The sink cancels right after the first call completes: the first result
    // keeps its own text, the never-started calls must be answered with the
    // dedicated abort result.
    let (new_messages, events) = run_agent_loop_with_sink_cancelling_on(
        "call-1",
        AgentContext {
            system_prompt: String::new(),
            messages: Vec::new(),
            tools: Some(vec![tool]),
        },
        config,
        run_signal,
    )
    .await;

    // The AgentContext-level invariant over the run's produced history.
    // `new_messages` is exactly the context the run built: the prompt, the
    // assistant message, and every tool result it produced.
    assert_every_tool_call_answered(&new_messages);
    assert_eq!(
        new_messages.last().map(AgentMessage::role),
        Some("toolResult"),
        "the answered context must end at a toolResult, the precondition a later continue() needs"
    );

    let results = tool_results_of(&new_messages);
    assert_eq!(
        results.len(),
        3,
        "three tool calls must yield three results"
    );
    assert_eq!(tool_result_text(results[0]), Some("first finished"));
    assert!(!is_error_result(results[0]));
    for skipped in &results[1..] {
        assert_eq!(
            tool_result_text(skipped),
            Some(TOOL_EXECUTION_ABORTED_MESSAGE),
            "a call skipped by cancellation must carry the dedicated abort wording"
        );
        assert!(is_error_result(skipped));
    }
    // Only the first call ever started: the skipped calls get no execution
    // events, only their synthesized result messages.
    let execution_starts = events
        .iter()
        .filter(|event| matches!(event, AgentEvent::ToolExecutionStart { .. }))
        .count();
    assert_eq!(
        execution_starts, 1,
        "a cancelled sequential batch must not start the later calls"
    );
    let execution_ends = events
        .iter()
        .filter(|event| matches!(event, AgentEvent::ToolExecutionEnd { .. }))
        .count();
    assert_eq!(execution_ends, 1);
    // The tool-result message events flow for the synthesized answers as well,
    // so context observers (state, persistence) see them.
    let tool_result_ends = events
        .iter()
        .filter(|event| {
            matches!(
                event,
                AgentEvent::MessageEnd {
                    message: AgentMessage::Message(Message::ToolResult(_))
                }
            )
        })
        .count();
    assert_eq!(
        tool_result_ends, 3,
        "every tool result must be emitted as a message"
    );
    // The turn closes with all three results attached.
    let turn_results = events
        .iter()
        .find_map(|event| match event {
            AgentEvent::TurnEnd { tool_results, .. } if !tool_results.is_empty() => {
                Some(tool_results.len())
            }
            _ => None,
        })
        .expect("a TurnEnd with tool results must be emitted");
    assert_eq!(turn_results, 3);
    provider.unregister();
}

/// SB-1 parallel control: cancelling mid parallel batch still yields exactly
/// one result per call, in assistant source order.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn parallel_cancellation_produces_one_result_per_call() {
    let provider = register_faux_provider(None);
    provider.set_responses(vec![tool_call_response(&[
        ("call-1", "parallel-cancel"),
        ("call-2", "parallel-cancel"),
        ("call-3", "parallel-cancel"),
    ])]);
    let run_signal = CancellationToken::new();
    let cancel = run_signal.clone();
    let tool = AgentTool {
        name: "parallel-cancel".to_string(),
        description: "first call cancels the run; the rest wait for cancellation".to_string(),
        parameters: json!({"type": "object", "properties": {}}),
        label: "Parallel cancel".to_string(),
        prepare_arguments: None,
        execute: Arc::new(move |tool_call_id, _, tool_signal, _| {
            let cancel = cancel.clone();
            Box::pin(async move {
                if tool_call_id == "call-1" {
                    cancel.cancel();
                    Ok(AgentToolResult::new(
                        vec![pi_agent_core::types::ContentBlock::text("first finished")],
                        json!({}),
                    ))
                } else {
                    // Deterministic abort: the biased race resolves the signal
                    // arm before this future can complete.
                    tool_signal
                        .expect("the loop must pass the run signal")
                        .cancelled()
                        .await;
                    Ok(AgentToolResult::new(
                        vec![pi_agent_core::types::ContentBlock::text("late")],
                        json!({}),
                    ))
                }
            })
        }),
        execution_mode: None,
    };
    let config = AgentLoopConfig::new(provider.get_model());
    let (new_messages, _events) = run_agent_loop_collecting(
        Some(run_signal),
        AgentContext {
            system_prompt: String::new(),
            messages: Vec::new(),
            tools: Some(vec![tool]),
        },
        config,
    )
    .await;

    assert_every_tool_call_answered(&new_messages);
    let results = tool_results_of(&new_messages);
    assert_eq!(results.len(), 3);
    // The cancelling call itself is aborted mid-flight by the biased abort
    // race, so every result of this batch carries the abort wording; the
    // contract is one result per call, in source order.
    for aborted in &results {
        assert_eq!(
            tool_result_text(aborted),
            Some(TOOL_EXECUTION_ABORTED_MESSAGE)
        );
        assert!(is_error_result(aborted));
    }
    let execution_starts = _events
        .iter()
        .filter(|event| matches!(event, AgentEvent::ToolExecutionStart { .. }))
        .count();
    assert_eq!(execution_starts, 3, "a parallel batch starts every call");
    provider.unregister();
}

/// SB-3: an abort during the before_tool_call hook leaves the dedicated abort
/// wording in the persisted tool result, not the raw anyhow display of the
/// aborted hook future ("Request was aborted").
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn before_hook_abort_uses_the_dedicated_abort_wording() {
    let provider = register_faux_provider(None);
    provider.set_responses(vec![tool_call_response(&[("call-1", "hook-abort")])]);
    let tool_body_ran = Arc::new(AtomicBool::new(false));
    let tool = AgentTool {
        name: "hook-abort".to_string(),
        description: "never runs: the before-hook aborts first".to_string(),
        parameters: json!({"type": "object", "properties": {}}),
        label: "Hook abort".to_string(),
        prepare_arguments: None,
        execute: {
            let tool_body_ran = tool_body_ran.clone();
            Arc::new(move |_, _, _, _| {
                let tool_body_ran = tool_body_ran.clone();
                Box::pin(async move {
                    tool_body_ran.store(true, Ordering::SeqCst);
                    Ok(AgentToolResult::new(
                        vec![pi_agent_core::types::ContentBlock::text("tool ran")],
                        json!({}),
                    ))
                })
            })
        },
        execution_mode: None,
    };
    let mut config = AgentLoopConfig::new(provider.get_model());
    config.before_tool_call = Some(Arc::new(|_, hook_signal| {
        Box::pin(async move {
            if let Some(signal) = hook_signal {
                signal.cancel();
            }
            // Stay pending so the abort race, not the hook, resolves the future.
            std::future::pending::<()>().await;
            unreachable!("the aborted hook future must never resolve")
        })
    }));
    let run_signal = CancellationToken::new();
    let (new_messages, _events) = run_agent_loop_collecting(
        Some(run_signal),
        AgentContext {
            system_prompt: String::new(),
            messages: Vec::new(),
            tools: Some(vec![tool]),
        },
        config,
    )
    .await;

    assert_every_tool_call_answered(&new_messages);
    let result = new_messages
        .iter()
        .find(|message| matches!(message, AgentMessage::Message(Message::ToolResult(_))))
        .expect("the aborted hook must still produce a tool result");
    assert_eq!(
        tool_result_text(result),
        Some(TOOL_EXECUTION_ABORTED_MESSAGE),
        "an aborted before-hook must use the dedicated abort wording"
    );
    assert!(is_error_result(result));
    // The tool body itself never ran; only the aborted hook's result exists.
    assert!(
        !tool_body_ran.load(Ordering::SeqCst),
        "the tool body must not run when the before-hook aborts"
    );
    provider.unregister();
}
