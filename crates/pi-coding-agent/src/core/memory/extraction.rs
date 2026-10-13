//! Internal model completion plumbing shared by both TypeScript memory extractors.
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use serde_json::Value;

use super::evidence::{hash, Evidence};
use super::jobs::ExtractionResult;
use super::service::MemoryService;
use crate::core::refinement::refinement::{
    plan_refinement, JsonMap, PlanRefinementRequest, RefineModel, RefineOptions, RefinementEdit,
    RefinementProposal,
};

/// Map the pi-ai `AssistantMessage` onto refinement's minimal local shape.
fn to_refinement_assistant(
    message: &pi_ai::types::AssistantMessage,
) -> crate::core::refinement::refinement::AssistantMessage {
    use crate::core::refinement::refinement::{
        AssistantContent, AssistantMessage as LocalAssistant, AssistantUsage, StopReason,
    };
    let content = message
        .content
        .iter()
        .filter_map(|block| match block {
            pi_ai::types::ContentBlock::Text(text) => Some(AssistantContent::Text {
                text: text.text.clone(),
            }),
            pi_ai::types::ContentBlock::Thinking(thinking) => Some(AssistantContent::Thinking {
                thinking: thinking.thinking.clone(),
            }),
            pi_ai::types::ContentBlock::ToolCall(_) => None,
        })
        .collect();
    let stop_reason = match message.stop_reason.as_str() {
        "length" => StopReason::Length,
        "toolUse" => StopReason::ToolUse,
        "error" => StopReason::Error,
        "aborted" => StopReason::Aborted,
        _ => StopReason::Stop,
    };
    LocalAssistant {
        content,
        usage: AssistantUsage {
            input: message.usage.input,
            output: message.usage.output,
            cache_read: message.usage.cache_read,
            cache_write: message.usage.cache_write,
        },
        stop_reason,
        error_message: message.error_message.clone(),
    }
}

/// `completeWithProviderRetry(() => completeSimple(model, context, options))`.
///
/// The TypeScript closes over `model`, `apiKey` and `headers` captured just
/// before the completion; the port takes the same captured values.
pub(crate) fn completion_fn_for(
    model: pi_ai::types::Model,
    api_key: String,
    headers: Option<HashMap<String, String>>,
    retry: Option<crate::core::refinement::refinement::ProviderRetryPolicy>,
) -> crate::core::refinement::refinement::CompletionFn {
    Arc::new(
        move |request: crate::core::refinement::refinement::RefinementCompletionRequest| {
            let model = model.clone();
            let api_key = api_key.clone();
            let headers = headers.clone();
            Box::pin(async move {
                let options = pi_ai::types::SimpleStreamOptions {
                    stream: pi_ai::types::StreamOptions {
                        max_tokens: Some(request.max_tokens),
                        api_key: Some(api_key),
                        headers: headers.clone().map(|headers| {
                            headers
                                .into_iter()
                                .collect::<indexmap::IndexMap<String, String>>()
                        }),
                        ..Default::default()
                    },
                    ..Default::default()
                };
                let context = pi_ai::types::Context {
                    system_prompt: Some(request.system_prompt),
                    messages: request
                        .messages
                        .iter()
                        .filter_map(|message| local_message_to_pi_ai(message))
                        .collect(),
                    tools: None,
                };
                let response = crate::core::provider_retry::complete_with_provider_retry(
                    || pi_ai::stream::complete_simple(&model, &context, Some(&options)),
                    crate::core::provider_retry::ProviderRetryExecutionOptions {
                        policy: retry.map(|policy| {
                            crate::core::provider_retry::ProviderRetryPolicy {
                                enabled: policy.enabled,
                                max_retries: f64::from(policy.max_retries),
                                base_delay_ms: policy.base_delay_ms,
                                max_retry_delay_ms: policy.max_retry_delay_ms,
                            }
                        }),
                        ..Default::default()
                    },
                )
                .await;
                to_refinement_assistant(&response)
            })
        },
    )
}

/// `[{ type: "text", text: prompt }]` as a pi-ai user message.
fn local_message_to_pi_ai(
    message: &crate::core::memory::evidence::AgentMessage,
) -> Option<pi_ai::types::Message> {
    let crate::core::memory::evidence::AgentMessage::User { content, timestamp } = message else {
        return None;
    };
    let text = match content {
        Value::String(text) => text.clone(),
        Value::Array(blocks) => blocks
            .iter()
            .filter_map(|block| {
                if block.get("type").and_then(Value::as_str) == Some("text") {
                    block
                        .get("text")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                } else {
                    None
                }
            })
            .collect::<Vec<String>>()
            .join(""),
        _ => String::new(),
    };
    Some(pi_ai::types::Message::User(pi_ai::types::UserMessage::new(
        pi_ai::types::UserContent::Text(text),
        *timestamp as i64,
    )))
}

/// `extractor` / `createMemoryHostHandlers`: count each planning and repair response.
pub async fn extract(
    memory: &MemoryService,
    records: Vec<Evidence>,
    model: pi_ai::types::Model,
    api_key: String,
    headers: Option<HashMap<String, String>>,
    mut options: RefineOptions,
) -> Result<ExtractionResult, String> {
    let complete = completion_fn_for(
        model.clone(),
        api_key.clone(),
        headers.clone(),
        options.retry,
    );
    let usage = Arc::new(Mutex::new((0.0, 0.0)));
    let recorded_usage = usage.clone();
    let complete: crate::core::refinement::refinement::CompletionFn = Arc::new(move |request| {
        let complete = complete.clone();
        let recorded_usage = recorded_usage.clone();
        Box::pin(async move {
            let response = complete(request).await;
            let mut usage = recorded_usage.lock().unwrap_or_else(|p| p.into_inner());
            usage.0 +=
                response.usage.input + response.usage.cache_read + response.usage.cache_write;
            usage.1 += response.usage.output;
            response
        })
    });
    options.evidence = Some(records);
    options.max_output_tokens = Some(memory.store.settings().max_extraction_tokens as f64);
    let state = memory.store.read()?.harness();
    let plan = plan_refinement(PlanRefinementRequest {
        messages: &[],
        state: &state,
        history: &[],
        model: RefineModel {
            max_tokens: model.max_tokens,
        },
        api_key,
        options,
        headers: headers.clone(),
        thinking_level: None,
        complete,
    })
    .await
    .map_err(|error| error.message)?;
    let (edits, converted_updates, dropped_edits) = select_import_edits(plan.proposal.edits);
    let mut rationale = plan.proposal.rationale;
    if converted_updates > 0 || dropped_edits > 0 {
        // The import normalizer is no longer silent: every conversion and every
        // policy drop is recorded in the persisted chunk rationale so the job
        // document (and the refinement event) keeps an audit trail.
        rationale.push_str(&format!(
            " [import-normalization: converted {converted_updates} update edit(s) to create; dropped {dropped_edits} edit(s) as uncited/delete/foreign-kind]"
        ));
    }
    let proposal = RefinementProposal {
        rationale,
        edits,
        ..plan.proposal
    };
    let (input, output) = *usage.lock().unwrap_or_else(|p| p.into_inner());
    Ok(ExtractionResult {
        proposal,
        input,
        output,
    })
}

/// How many update edits were converted into creates, and how many edits the
/// import policy dropped (uncited evidence, deletes, non-memory kinds).
pub(crate) type ImportEditSelection = (Vec<RefinementEdit>, usize, usize);

/// Normalize a raw extraction proposal into import-applicable edits.
///
/// The import pipeline can only apply `create` edits of kind `memory` with
/// cited evidence. Historically every other edit was filtered out here, so a
/// chunk whose model output only proposed `update` edits (the generic /refine
/// system prompt invites consolidation updates) normalized to `edits: []` and
/// the chunk's durable facts were silently lost (LME-S 00ca467f: the Dr. Smith
/// March 3 PCP visit in job import_8319fcc5 chunk 0 never entered the store).
///
/// A cited update of kind `memory` is now converted into a `create` edit that
/// carries the same title/content/metadata under a fresh derived id, with the
/// original target recorded in `metadata.updateTargetId` for traceability.
/// The fact is durable, so a new entry is preferable to silent loss.
pub(crate) fn select_import_edits(edits: Vec<RefinementEdit>) -> ImportEditSelection {
    let mut selected: Vec<RefinementEdit> = Vec::new();
    let mut converted_updates = 0usize;
    let mut dropped_edits = 0usize;
    for mut edit in edits {
        let cited = edit
            .metadata
            .as_ref()
            .and_then(|metadata| metadata.get("evidenceStatus"))
            .and_then(Value::as_str)
            == Some("cited");
        match (edit.action.as_str(), edit.kind.as_str(), cited) {
            ("create", "memory", true) => selected.push(edit),
            ("update", "memory", true) => {
                let content = edit.content.clone().unwrap_or_default();
                if content.trim().is_empty() {
                    // An update without content carries no durable fact to
                    // preserve; count it as dropped instead of creating an
                    // empty entry.
                    dropped_edits += 1;
                    continue;
                }
                let update_target = edit.id.clone();
                // A create that re-uses the update target id would be skipped
                // by the apply-time idempotent filter (the entry exists), so
                // derive a fresh stable id from the content, exactly like the
                // apply-time fallback for model ids that fail validation.
                let content_key = hash(&format!("{}:{}", edit.kind, content.trim()));
                edit.id = Some(format!(
                    "imported_{:.48}",
                    &content_key[..24.min(content_key.len())]
                ));
                edit.action = "create".to_string();
                if let Some(target) = update_target {
                    let metadata = edit.metadata.get_or_insert_with(JsonMap::new);
                    metadata.insert("updateTargetId".to_string(), Value::String(target));
                }
                converted_updates += 1;
                selected.push(edit);
            }
            _ => dropped_edits += 1,
        }
    }
    (selected, converted_updates, dropped_edits)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::refinement::refinement::normalize_refinement_proposal;
    use serde_json::json;

    fn update_proposal() -> RefinementProposal {
        // Reproduces LME-S 00ca467f, job import_8319fcc5 chunk 0: the model
        // proposed updating memory_lingering_cough_guaifenesin with the new
        // dated fact (Dr. Smith PCP visit, March 3, bronchitis diagnosis).
        normalize_refinement_proposal(&json!({
            "summary": "Updated the cough memory with the bronchitis diagnosis",
            "rationale": "Record m_0001:0 (user report) states the user saw their primary care physician, Dr. Smith, on March 3rd, who diagnosed bronchitis - a durable, dated health fact absent from the existing cough memory.",
            "expectedOutcome": "Future questions can retrieve the March 3 diagnosis and the doctor's name.",
            "edits": [{
                "action": "update",
                "kind": "memory",
                "id": "memory_lingering_cough_guaifenesin",
                "title": "Lingering cough",
                "content": "User has had a lingering cough for six weeks. Over-the-counter expectorants (guaifenesin) have not been working well. The user saw their primary care physician, Dr. Smith, on March 3rd and was diagnosed with bronchitis.",
                "metadata": {"sourceIds": ["m_0001:0"], "evidenceStatus": "cited"}
            }]
        }))
    }

    #[test]
    fn cited_update_edits_convert_to_creates_instead_of_vanishing() {
        // The pre-fix filter kept only `create` edits, so this proposal
        // normalized to edits: [] and the fact was silently dropped.
        let proposal = update_proposal();
        let (edits, converted, dropped) = select_import_edits(proposal.edits);
        assert_eq!(converted, 1, "the update is converted, not dropped");
        assert_eq!(dropped, 0);
        assert_eq!(edits.len(), 1, "the durable fact survives normalization");
        let edit = &edits[0];
        assert_eq!(edit.action, "create");
        assert_eq!(edit.kind, "memory");
        // Fresh derived id: re-using the target id would make the apply-time
        // idempotent filter drop the edit again (the entry already exists).
        let id = edit.id.as_deref().expect("derived id");
        assert_ne!(id, "memory_lingering_cough_guaifenesin");
        assert!(id.starts_with("imported_"));
        assert_eq!(edit.title.as_deref(), Some("Lingering cough"));
        let content = edit.content.as_deref().expect("content preserved");
        assert!(content.contains("Dr. Smith"));
        assert!(content.contains("March 3rd"));
        let metadata = edit.metadata.as_ref().expect("metadata preserved");
        assert_eq!(
            metadata.get("updateTargetId").and_then(Value::as_str),
            Some("memory_lingering_cough_guaifenesin"),
            "the original update target stays traceable"
        );
        assert_eq!(
            metadata.get("evidenceStatus").and_then(Value::as_str),
            Some("cited")
        );
    }

    #[test]
    fn update_conversion_is_deterministic_and_idempotent() {
        let first = select_import_edits(update_proposal().edits);
        let second = select_import_edits(update_proposal().edits);
        assert_eq!(first.0, second.0, "same content derives the same edit");
        // The derived id is stable, so re-extracting the same chunk content
        // coalesces at apply time instead of duplicating entries.
        assert_eq!(first.0[0].id, second.0[0].id);
    }

    #[test]
    fn cited_creates_are_kept_unchanged() {
        let proposal = normalize_refinement_proposal(&json!({
            "summary": "s", "rationale": "r", "expectedOutcome": "o",
            "edits": [{
                "action": "create", "kind": "memory", "id": "fresh_fact",
                "title": "Fresh", "content": "A newly reported durable fact.",
                "metadata": {"sourceIds": ["m_0002:0"], "evidenceStatus": "cited"}
            }]
        }));
        let (edits, converted, dropped) = select_import_edits(proposal.edits);
        assert_eq!(converted, 0);
        assert_eq!(dropped, 0);
        assert_eq!(edits.len(), 1);
        assert_eq!(edits[0].action, "create");
        assert_eq!(edits[0].id.as_deref(), Some("fresh_fact"));
        assert!(
            edits[0]
                .metadata
                .as_ref()
                .unwrap()
                .get("updateTargetId")
                .is_none()
        );
    }

    #[test]
    fn uncited_updates_are_dropped_and_counted() {
        // Assistant-asserted content (no user/tool/file evidence) stays
        // excluded by the import evidence policy.
        let mut proposal = update_proposal();
        proposal.edits[0].metadata.as_mut().unwrap().insert(
            "evidenceStatus".to_string(),
            Value::String("uncited".to_string()),
        );
        let (edits, converted, dropped) = select_import_edits(proposal.edits);
        assert!(edits.is_empty());
        assert_eq!(converted, 0);
        assert_eq!(dropped, 1, "policy drop is counted, never silent");
    }

    #[test]
    fn delete_edits_and_foreign_kinds_are_dropped_and_counted() {
        let proposal = normalize_refinement_proposal(&json!({
            "summary": "s", "rationale": "r", "expectedOutcome": "o",
            "edits": [
                {"action": "delete", "kind": "memory", "id": "old",
                 "metadata": {"sourceIds": ["m_0001:0"], "evidenceStatus": "cited"}},
                {"action": "create", "kind": "prompt", "id": "p", "title": "t", "content": "c",
                 "metadata": {"sourceIds": ["m_0001:0"], "evidenceStatus": "cited"}},
                {"action": "update", "kind": "memory", "id": "x", "title": "t",
                 "metadata": {"sourceIds": ["m_0001:0"], "evidenceStatus": "cited"}}
            ]
        }));
        let (edits, converted, dropped) = select_import_edits(proposal.edits);
        assert!(edits.is_empty());
        assert_eq!(converted, 0);
        assert_eq!(
            dropped, 3,
            "delete, foreign kind and contentless update all counted"
        );
    }

    #[test]
    fn mixed_proposals_keep_creates_and_convert_updates() {
        let proposal = normalize_refinement_proposal(&json!({
            "summary": "s", "rationale": "r", "expectedOutcome": "o",
            "edits": [
                {"action": "create", "kind": "memory", "id": "new_fact",
                 "title": "New", "content": "A new fact.",
                 "metadata": {"sourceIds": ["m_0005:0"], "evidenceStatus": "cited"}},
                {"action": "update", "kind": "memory", "id": "existing_fact",
                 "title": "Existing", "content": "The consolidated fact with new evidence.",
                 "metadata": {"sourceIds": ["m_0006:0"], "evidenceStatus": "cited"}},
                {"action": "delete", "kind": "memory", "id": "stale_fact",
                 "metadata": {"sourceIds": ["m_0007:0"], "evidenceStatus": "cited"}}
            ]
        }));
        let (edits, converted, dropped) = select_import_edits(proposal.edits);
        assert_eq!(edits.len(), 2);
        assert_eq!(converted, 1);
        assert_eq!(dropped, 1);
        assert_eq!(edits[0].id.as_deref(), Some("new_fact"));
        assert_eq!(edits[1].action, "create");
        assert!(edits[1].id.as_deref().unwrap().starts_with("imported_"));
    }
}
