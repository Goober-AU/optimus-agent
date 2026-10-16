//! Chat-local OCC controls; workers read session artifacts at each send.

use super::*;

#[derive(Debug, PartialEq)]
enum Choice {
    Menu,
    Status,
    Set(bool),
}

fn parse(args: &str) -> Result<Choice, String> {
    let words: Vec<_> = args.split_whitespace().collect();
    match words.as_slice() {
        [] => Ok(Choice::Menu),
        ["status"] => Ok(Choice::Status),
        ["1"] | ["occ1"] | ["off"] | ["2", "off"] | ["occ2", "off"] => Ok(Choice::Set(false)),
        ["2"] | ["occ2"] | ["on"] | ["2", "on"] | ["occ2", "on"] => Ok(Choice::Set(true)),
        _ => Err("Usage: /occ [status|1|2 on|2 off]. OCC-1 remains enabled as the default and fallback.".into()),
    }
}

fn status(enabled: bool) -> String {
    format!(
        "Agent communication (this chat)\n\nOCC-1: ON (default and fallback)\nOCC-2: {} (experimental symbolic encoding)\n\n/occ 2 on enables OCC-2; /occ 2 off returns to OCC-1.\nSaved for this chat and inherited by its subagents unless they have an override. Other chats are unchanged. Applies to subsequent structured messages. Plain-text messages remain TXT.\nNeon-blue TXT / OCC1 / OCC2 indicators flash on delivered messages. OCC-3 is not active.",
        if enabled { "ON" } else { "OFF" }
    )
}

pub(super) async fn run(
    connection: &Arc<dyn wire::AgentConnection>,
    send: &mpsc::Sender<HostEvent>,
    args: &str,
) -> Result<CommandOutput, String> {
    let choice = parse(args)?;
    if !connection.supports_occ2_communication() {
        return Ok(CommandOutput::Warning("This daemon does not support OCC-2 controls. Restart it with Optimus Agent v0.1.33 or later; OCC-1 remains available.".into()));
    }
    let state = connection.get_state().await?;
    let session_file = state.session_file.as_deref().ok_or("This chat has no saved session identity")?;
    let enabled = crate::core::occ::session_settings::load(session_file)?;
    let requested = match choice {
        Choice::Status => return Ok(CommandOutput::Panel(status(enabled))),
        Choice::Set(value) => value,
        Choice::Menu => {
            let title = format!("OCC-1 ON · OCC-2 {}", if enabled { "ON" } else { "OFF" });
            match select(send, &title, &["OCC-1 only (default)", "Enable OCC-2 (experimental)"]).await {
                Some(choice) => choice == "Enable OCC-2 (experimental)",
                None => return Ok(CommandOutput::Nothing),
            }
        }
    };
    crate::core::occ::session_settings::save(session_file, requested)?;
    Ok(CommandOutput::Status(status(requested)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn occ_control_accepts_modes_and_rejects_unsupported_or_ambiguous_input() {
        assert_eq!(parse("").unwrap(), Choice::Menu);
        assert_eq!(parse("status").unwrap(), Choice::Status);
        for args in ["1", "off", "2 off", "occ2 off"] {
            assert_eq!(parse(args).unwrap(), Choice::Set(false));
        }
        for args in ["2", "on", "2 on", "occ2 on"] {
            assert_eq!(parse(args).unwrap(), Choice::Set(true));
        }
        for args in ["3 on", "1 off", "2 maybe", "2 on off"] {
            assert!(parse(args).is_err());
        }
    }

}
