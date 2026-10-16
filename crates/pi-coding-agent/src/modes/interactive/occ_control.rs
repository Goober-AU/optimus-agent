//! Local OCC controls; workers read the persisted setting at each send.

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
        "Agent communication\n\nOCC-1: ON (default and fallback)\nOCC-2: {} (experimental symbolic encoding)\n\n/occ 2 on enables OCC-2; /occ 2 off returns to OCC-1.\nApplies to subsequent structured messages, including existing subagents. Plain-text messages remain TXT.\nNeon-blue TXT / OCC1 / OCC2 indicators flash on delivered messages. OCC-3 is not active.",
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
    let mut settings = SettingsManager::create(&state.cwd, None);
    if let Some(error) = settings.drain_errors(None).first() {
        return Err(error.error.to_string());
    }
    let enabled = settings.get_agent_communication_occ2_enabled();
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
    settings.set_agent_communication_occ2_enabled(requested);
    settings.flush().await;
    if let Some(error) = settings.drain_errors(None).first() {
        return Err(error.error.to_string());
    }
    let effective = settings.get_agent_communication_occ2_enabled();
    if effective != requested {
        return Ok(CommandOutput::Warning(format!(
            "Saved the global OCC-2 setting, but this project's override keeps OCC-2 {}. Update agentCommunication.occ2Enabled in project settings to change it.",
            if effective { "ON" } else { "OFF" }
        )));
    }
    let _ = send.send(HostEvent::ReloadSettings);
    Ok(CommandOutput::Status(status(effective)))
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

    #[test]
    fn occ_control_persistence_is_seen_by_already_running_settings_readers() {
        let directory = tempfile::tempdir().unwrap();
        let cwd = directory.path().join("workspace");
        let profile = directory.path().join("profile");
        std::fs::create_dir_all(&cwd).unwrap();
        let cwd = cwd.to_str().unwrap();
        let profile = profile.to_str().unwrap();
        let mut writer = SettingsManager::create(cwd, Some(profile));
        let reader = SettingsManager::create(cwd, Some(profile));
        assert!(!reader.get_agent_communication_occ2_enabled());
        writer.set_agent_communication_occ2_enabled(true);
        assert!(writer.drain_errors(None).is_empty());
        assert!(reader.get_agent_communication_occ2_enabled());
        writer.set_agent_communication_occ2_enabled(false);
        assert!(!reader.get_agent_communication_occ2_enabled());
        let mut overridden = SettingsManager::create(cwd, Some(profile));
        overridden.apply_overrides(&serde_json::from_value(serde_json::json!({"agentCommunication":{"occ2Enabled":false}})).unwrap());
        writer.set_agent_communication_occ2_enabled(true);
        assert!(!overridden.get_agent_communication_occ2_enabled());
        std::fs::write(std::path::Path::new(profile).join("settings.json"), "invalid").unwrap();
        assert!(!reader.get_agent_communication_occ2_enabled());
    }
}
