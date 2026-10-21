//! Offline checks through the native Agent and its real provider request path.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pi_ai::inference_attachment::{
    InferenceAttachment, InferenceStateLease, InferenceStateLeaseState, InferenceStateTarget,
};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, ContentBlock, Context, Message, Model, TextContent,
    ToolCall, UserContent, UserMessage, API_OPENAI_COMPLETIONS,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::time::timeout;

use crate::agent::{Agent, AgentOptions, PromptInput};
use crate::types::{
    AgentMessage, AgentState, AgentTool, AgentToolResult, CustomAgentMessage, CustomMessageContent,
    InferenceStateRequest, ResolveInferenceState, StreamFn,
};

fn model(base_url: &str) -> Model {
    Model::new(
        "fixture-model",
        "Fixture",
        API_OPENAI_COMPLETIONS,
        "native-fixture",
        base_url,
    )
}

fn lease(model: &Model) -> InferenceStateLease {
    InferenceStateLease::new(
        InferenceStateTarget {
            provider: model.provider.clone(),
            model_id: model.id.clone(),
            base_url: model.base_url.clone(),
        },
        InferenceAttachment {
            protocol_version: 1,
            request_id: "native-request-1".into(),
            message_id: "agentmsg-matching".into(),
            connector_id: "fixture-native".into(),
            receiver_session_id: "receiver-session".into(),
            model_fingerprint: "fixture-model-fingerprint".into(),
            runtime_id: "fixture-runtime".into(),
            state_handle: "opaque-native-fixture".into(),
            method: "kv-handoff".into(),
            runtime_generation: 1,
            expires_at_epoch: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs()
                + 600,
        },
    )
    .unwrap()
}

fn user(text: &str) -> AgentMessage {
    UserMessage::new(UserContent::Text(text.into()), 1).into()
}

fn admitted_message() -> AgentMessage {
    AgentMessage::Custom(CustomAgentMessage::Custom {
        custom_type: "agent_message".into(),
        content: CustomMessageContent::Text("validated text fallback".into()),
        display: true,
        details: Some(json!({
            "id": "agentmsg-matching", "message": "original-envelope",
            "from": {"sessionId": "sender-session"},
            "target": {"sessionId": "receiver-session"}
        })),
        timestamp: 2,
    })
}

fn message_id(message: &AgentMessage) -> Option<&str> {
    match message {
        AgentMessage::Custom(CustomAgentMessage::Custom {
            custom_type,
            details,
            ..
        }) if custom_type == "agent_message" => details.as_ref()?.get("id")?.as_str(),
        _ => None,
    }
}

fn converter(messages: Vec<AgentMessage>) -> Vec<Message> {
    messages
        .into_iter()
        .filter_map(|message| match message {
            AgentMessage::Message(message) => Some(message),
            AgentMessage::Custom(CustomAgentMessage::Custom {
                content: CustomMessageContent::Text(text),
                timestamp,
                ..
            }) => Some(Message::User(UserMessage::new(
                UserContent::Text(text),
                timestamp,
            ))),
            _ => None,
        })
        .collect()
}

fn fixture_tool(executions: Arc<AtomicUsize>) -> AgentTool {
    AgentTool {
        name: "fixture_tool".into(),
        description: "Offline fixture".into(),
        parameters: json!({"type":"object", "properties":{}}),
        label: "Fixture".into(),
        prepare_arguments: None,
        execute: Arc::new(move |_, _, _, _| {
            executions.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Ok(AgentToolResult::new(Vec::new(), json!({}))) })
        }),
        execution_mode: None,
    }
}

fn base_options(model: Model, stream_fn: StreamFn) -> AgentOptions {
    AgentOptions {
        initial_state: Some(AgentState {
            model,
            system_prompt: "native request fixture".into(),
            ..Default::default()
        }),
        convert_to_llm: Some(Arc::new(|messages| {
            Box::pin(async move { converter(messages) })
        })),
        get_api_key: Some(Arc::new(|_| {
            Box::pin(async { Some("offline-fixture-key".into()) })
        })),
        stream_fn: Some(stream_fn),
        ..Default::default()
    }
}

fn provider_stream() -> StreamFn {
    Arc::new(|model, context, options| {
        Box::pin(async move {
            pi_ai::providers::openai_completions::stream_simple_openai_completions(
                &model,
                &context,
                Some(options),
            )
        })
    })
}

fn fixed_resolver(state: InferenceStateLease) -> ResolveInferenceState {
    Arc::new(move |_| {
        let state = state.clone();
        Box::pin(async move { Ok(Some(state)) })
    })
}

fn immediate_stream(calls: Arc<AtomicUsize>, tool: bool) -> StreamFn {
    Arc::new(move |model, _, _| {
        calls.fetch_add(1, Ordering::SeqCst);
        let mut message = AssistantMessage::new(model.api, model.provider, model.id, 1);
        if tool {
            message.content.push(ContentBlock::ToolCall(ToolCall::new(
                "ignored-state-tool",
                "fixture_tool",
                serde_json::Map::new(),
            )));
            message.stop_reason = "toolUse".into();
        } else {
            message
                .content
                .push(ContentBlock::Text(TextContent::new("ordinary fixture")));
            message.stop_reason = "stop".into();
        }
        let stream = AssistantMessageEventStream::new();
        stream.push(AssistantMessageEvent::Done {
            reason: message.stop_reason.clone(),
            message,
        });
        stream.end(None);
        Box::pin(async move { stream })
    })
}

async fn read_request(socket: &mut TcpStream) -> Value {
    let mut bytes = Vec::new();
    let (header_end, length) = loop {
        let mut chunk = [0_u8; 4096];
        let count = socket.read(&mut chunk).await.unwrap();
        assert!(count > 0, "request closed before headers");
        bytes.extend_from_slice(&chunk[..count]);
        assert!(bytes.len() < 1024 * 1024);
        if let Some(end) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&bytes[..end]);
            assert!(headers.starts_with("POST /v1/chat/completions "));
            let length: usize = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse().unwrap())
                })
                .unwrap();
            break (end + 4, length);
        }
    };
    while bytes.len() < header_end + length {
        let mut chunk = [0_u8; 4096];
        let count = socket.read(&mut chunk).await.unwrap();
        assert!(count > 0, "request closed before body");
        bytes.extend_from_slice(&chunk[..count]);
    }
    serde_json::from_slice(&bytes[header_end..header_end + length]).unwrap()
}

async fn local_provider() -> (String, tokio::task::JoinHandle<Vec<Value>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}/v1", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let mut requests = Vec::new();
        for index in 0..4 {
            let (mut socket, _) = timeout(Duration::from_secs(10), listener.accept())
                .await
                .unwrap()
                .unwrap();
            let body = timeout(Duration::from_secs(10), read_request(&mut socket))
                .await
                .unwrap();
            let attachment = body.get("occ3");
            let receipt = attachment.map(|attachment| format!(
                "x-optimus-occ3-receipt: {}\r\n",
                json!({"attachment":attachment,"consumed":true,"fallback_used":false,"injected_state":true})
            )).unwrap_or_default();
            let (delta, finish) = if index == 1 {
                (
                    json!({"role":"assistant","tool_calls":[{
                        "index":0,"id":"fixture-call","type":"function",
                        "function":{"name":"fixture_tool","arguments":"{}"}
                    }]}),
                    "tool_calls",
                )
            } else {
                (
                    json!({"role":"assistant","content":"fixture completion"}),
                    "stop",
                )
            };
            let events = format!(
                "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                json!({"id":"fixture","choices":[{"index":0,"delta":delta,"finish_reason":null}]}),
                json!({"id":"fixture","choices":[{"index":0,"delta":{},"finish_reason":finish}]})
            );
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n{}\r\n{}",
                events.len(), receipt, events
            );
            socket.write_all(response.as_bytes()).await.unwrap();
            socket.shutdown().await.unwrap();
            requests.push(body);
        }
        requests
    });
    (base_url, server)
}

#[tokio::test]
async fn native_inference_state_reaches_matching_request_once_not_tool_or_later_turns() {
    let (base_url, server) = local_provider().await;
    let model = model(&base_url);
    let state = lease(&model);
    let pending = Arc::new(Mutex::new(HashMap::from([(
        "agentmsg-matching".to_string(),
        state.clone(),
    )])));
    let calls = Arc::new(AtomicUsize::new(0));
    let tools = Arc::new(AtomicUsize::new(0));
    let contexts = Arc::new(Mutex::new(Vec::<Context>::new()));
    let mut options = base_options(model.clone(), provider_stream());
    options.initial_state.as_mut().unwrap().tools = Some(vec![fixture_tool(tools.clone())]);
    options.transform_context = Some(Arc::new(|mut messages, _| {
        Box::pin(async move {
            if let Some(AgentMessage::Custom(CustomAgentMessage::Custom { content, .. })) =
                messages.last_mut()
            {
                *content = CustomMessageContent::Text("transformed fallback".into());
            }
            messages
        })
    }));
    options.resolve_inference_state = Some(Arc::new({
        let pending = pending.clone();
        let calls = calls.clone();
        let contexts = contexts.clone();
        move |request: InferenceStateRequest| {
            calls.fetch_add(1, Ordering::SeqCst);
            assert_eq!(request.model.base_url, base_url);
            assert!(!request.signal.as_ref().unwrap().is_cancelled());
            contexts.lock().unwrap().push(request.context);
            let state = request
                .messages
                .iter()
                .filter_map(message_id)
                .find_map(|id| pending.lock().unwrap().remove(id));
            Box::pin(async move { Ok(state) })
        }
    }));
    let agent = Agent::new(options);
    agent
        .prompt(PromptInput::Messages(vec![user("unrelated first")]))
        .await
        .unwrap();
    assert!(pending.lock().unwrap().contains_key("agentmsg-matching"));
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert!(
        agent.state().error_message.is_none(),
        "{:?}",
        agent.state().error_message
    );
    assert_eq!(tools.load(Ordering::SeqCst), 1);
    assert_eq!(state.state(), InferenceStateLeaseState::Consumed);
    agent
        .prompt(PromptInput::Messages(vec![user("later prompt")]))
        .await
        .unwrap();
    let requests = timeout(Duration::from_secs(10), server)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        requests
            .iter()
            .map(|body| body.get("occ3").is_some())
            .collect::<Vec<_>>(),
        [false, true, false, false]
    );
    assert_eq!(
        requests[1]["occ3"],
        serde_json::to_value(state.attachment()).unwrap()
    );
    assert_eq!(calls.load(Ordering::SeqCst), 4);
    assert_eq!(
        contexts.lock().unwrap()[1].messages.last().unwrap(),
        &Message::User(UserMessage::new(
            UserContent::Text("transformed fallback".into()),
            2
        ))
    );
    assert_eq!(
        contexts.lock().unwrap()[1].tools.as_ref().unwrap()[0].name,
        "fixture_tool"
    );

    let sent = Arc::new(AtomicUsize::new(0));
    *agent.stream_fn.lock().unwrap() = immediate_stream(sent.clone(), false);
    agent.set_resolve_inference_state(Some(fixed_resolver(state.clone())));
    agent
        .prompt(PromptInput::Messages(vec![user("must not replay")]))
        .await
        .unwrap();
    assert_eq!(sent.load(Ordering::SeqCst), 0);
    assert!(agent
        .state()
        .error_message
        .unwrap()
        .contains("cannot be replayed"));
    assert_eq!(state.state(), InferenceStateLeaseState::Consumed);
}

#[tokio::test]
async fn native_inference_state_cancellation_before_resolver_sends_nothing() {
    let calls = Arc::new(AtomicUsize::new(0));
    let resolved = Arc::new(AtomicUsize::new(0));
    let mut options = base_options(
        model("http://127.0.0.1:1/v1"),
        immediate_stream(calls.clone(), false),
    );
    options.before_request = Some(Arc::new(|_, signal| {
        Box::pin(async move {
            signal.unwrap().cancel();
            Ok(())
        })
    }));
    options.resolve_inference_state = Some(Arc::new({
        let resolved = resolved.clone();
        move |_| {
            resolved.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Ok(None) })
        }
    }));
    let agent = Agent::new(options);
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert_eq!(resolved.load(Ordering::SeqCst), 0);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn native_inference_state_cancel_after_resolution_closes_lease_before_send() {
    let model = model("http://127.0.0.1:1/v1");
    let state = lease(&model);
    let calls = Arc::new(AtomicUsize::new(0));
    let agent = Agent::new(base_options(model, immediate_stream(calls.clone(), false)));
    agent.set_resolve_inference_state(Some(Arc::new({
        let weak = Arc::downgrade(&agent);
        let state = state.clone();
        move |_| {
            let weak = weak.clone();
            let state = state.clone();
            Box::pin(async move {
                weak.upgrade().unwrap().abort();
                Ok(Some(state))
            })
        }
    })));
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert_eq!(state.state(), InferenceStateLeaseState::Cancelled);
    agent.set_resolve_inference_state(Some(fixed_resolver(state.clone())));
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(agent
        .state()
        .error_message
        .unwrap()
        .contains("cannot be replayed"));
}

#[tokio::test]
async fn native_inference_state_cancellation_drops_waiting_resolver_before_send() {
    let calls = Arc::new(AtomicUsize::new(0));
    let (started, ready) = tokio::sync::oneshot::channel();
    let started = Arc::new(Mutex::new(Some(started)));
    let mut options = base_options(
        model("http://127.0.0.1:1/v1"),
        immediate_stream(calls.clone(), false),
    );
    options.resolve_inference_state = Some(Arc::new(move |_| {
        let started = started.lock().unwrap().take();
        Box::pin(async move {
            started.unwrap().send(()).unwrap();
            std::future::pending::<anyhow::Result<Option<InferenceStateLease>>>().await
        })
    }));
    let agent = Agent::new(options);
    let run = tokio::spawn({
        let agent = agent.clone();
        async move {
            agent
                .prompt(PromptInput::Messages(vec![admitted_message()]))
                .await
        }
    });
    timeout(Duration::from_secs(5), ready)
        .await
        .unwrap()
        .unwrap();
    agent.abort();
    timeout(Duration::from_secs(5), run)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    agent.set_resolve_inference_state(None);
    agent
        .prompt(PromptInput::Messages(vec![user("fresh ordinary run")]))
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn native_inference_state_resolver_error_and_unsupported_api_do_not_send() {
    let calls = Arc::new(AtomicUsize::new(0));
    let mut options = base_options(
        model("http://127.0.0.1:1/v1"),
        immediate_stream(calls.clone(), false),
    );
    options.resolve_inference_state = Some(Arc::new(|_| {
        Box::pin(async {
            Err(anyhow::anyhow!(
                "uncertain admitted message: no ordinary retry"
            ))
        })
    }));
    let agent = Agent::new(options);
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(agent
        .state()
        .error_message
        .unwrap()
        .contains("no ordinary retry"));

    let mut unsupported = model("http://127.0.0.1:1/v1");
    unsupported.api = "unsupported-api".into();
    let state = lease(&unsupported);
    let mut options = base_options(unsupported, immediate_stream(calls.clone(), false));
    options.resolve_inference_state = Some(fixed_resolver(state.clone()));
    let agent = Agent::new(options);
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(agent
        .state()
        .error_message
        .unwrap()
        .contains("does not support inference state"));
    assert_eq!(state.state(), InferenceStateLeaseState::Cancelled);
}

#[tokio::test]
async fn native_inference_state_ignored_by_stream_cannot_execute_tools() {
    let model = model("http://127.0.0.1:1/v1");
    let state = lease(&model);
    let calls = Arc::new(AtomicUsize::new(0));
    let tools = Arc::new(AtomicUsize::new(0));
    let mut options = base_options(model, immediate_stream(calls.clone(), true));
    options.initial_state.as_mut().unwrap().tools = Some(vec![fixture_tool(tools.clone())]);
    options.resolve_inference_state = Some(fixed_resolver(state.clone()));
    let agent = Agent::new(options);
    agent
        .prompt(PromptInput::Messages(vec![admitted_message()]))
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(tools.load(Ordering::SeqCst), 0);
    assert!(agent
        .state()
        .error_message
        .unwrap()
        .contains("was not consumed"));
    assert_eq!(state.state(), InferenceStateLeaseState::Cancelled);
}
