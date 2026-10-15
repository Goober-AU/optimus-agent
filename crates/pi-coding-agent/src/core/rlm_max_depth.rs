//! Port of packages/coding-agent/src/core/rlm-max-depth.ts
//!
//! Wire-safe types for the immediate /rlm-max-depth state APIs.

use serde::{Deserialize, Serialize};

pub type RlmMaxDepthSource = String;

pub const RLM_MAX_DEPTH_SOURCE_DEFAULT: &str = "default";
pub const RLM_MAX_DEPTH_SOURCE_ENV: &str = "env";
pub const RLM_MAX_DEPTH_SOURCE_GLOBAL: &str = "global";
pub const RLM_MAX_DEPTH_SOURCE_INHERITED: &str = "inherited";
pub const RLM_MAX_DEPTH_SOURCE_CHAT: &str = "chat";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RlmMaxDepthStatus {
    pub max_depth: f64,
    pub source: RlmMaxDepthSource,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetRlmMaxDepthResult {
    pub max_depth: f64,
    pub source: RlmMaxDepthSource,
    pub global_saved: bool,
    /// `globalError?: string` - absent when the global save succeeded.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub global_error: Option<String>,
}

/// Error returned when a session at the recursion limit tries to spawn a
/// child. Mirrors the reference RLM rule that an RLM at max depth degrades
/// to a plain LM: the message keeps the machine-readable limit facts and
/// tells the deepest agent to answer directly instead of delegating.
pub fn rlm_depth_limit_reached_error(rlm_depth: i64, rlm_max_depth: i64) -> String {
    format!(
        "RLM recursion depth limit reached (RLM_DEPTH={rlm_depth}, RLM_MAX_DEPTH={rlm_max_depth}); \
you are the deepest agent: answer directly from your current context instead of delegating further"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn depth_limit_error_keeps_limit_facts_and_adds_degradation_guidance() {
        let message = rlm_depth_limit_reached_error(3, 3);
        assert!(
            message.starts_with("RLM recursion depth limit reached (RLM_DEPTH=3, RLM_MAX_DEPTH=3)"),
            "machine-readable prefix must stay stable: {message}"
        );
        assert!(
            message.contains("answer directly from your current context"),
            "the deepest agent must be told how to degrade: {message}"
        );
    }

    #[test]
    fn status_serializes_camel_case_and_omits_absent_error() {
        let result = SetRlmMaxDepthResult {
            max_depth: 3.0,
            source: RLM_MAX_DEPTH_SOURCE_CHAT.to_string(),
            global_saved: false,
            global_error: None,
        };
        assert_eq!(
            serde_json::to_string(&result).unwrap(),
            r#"{"maxDepth":3.0,"source":"chat","globalSaved":false}"#
        );
        let with_error = SetRlmMaxDepthResult {
            global_error: Some("boom".to_string()),
            ..result
        };
        assert!(serde_json::to_string(&with_error).unwrap().contains(r#""globalError":"boom""#));
    }
}
