//! Pure shutdown argument parsing; never discovers or stops a process.
#[derive(Debug, Default, PartialEq, Eq)]
pub(super) struct ShutdownOptions {
    pub socket: Option<String>,
    pub force: bool,
    pub json: bool,
}

pub(super) fn parse(args: &[String]) -> Result<ShutdownOptions, String> {
    let mut options = ShutdownOptions::default();
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--force" | "-f" => options.force = true,
            "--json" => options.json = true,
            "--socket" | "--daemon-socket" => {
                let value = args
                    .next()
                    .filter(|v| !v.is_empty() && !v.starts_with('-'))
                    .ok_or_else(|| format!("{arg} requires a socket path"))?;
                if options.socket.replace(value.clone()).is_some() {
                    return Err("Specify only one shutdown socket".into());
                }
            }
            _ => return Err(format!("Unknown option for shutdown: {arg}")),
        }
    }
    Ok(options)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|s| s.to_string()).collect()
    }
    #[test]
    fn shutdown_socket_aliases_preserve_force_json_and_spaces() {
        for flag in ["--socket", "--daemon-socket"] {
            assert_eq!(
                parse(&args(&[
                    "--json",
                    flag,
                    "/tmp/with spaces/daemon.sock",
                    "--force"
                ]))
                .unwrap(),
                ShutdownOptions {
                    socket: Some("/tmp/with spaces/daemon.sock".into()),
                    json: true,
                    force: true
                }
            );
        }
        assert_eq!(parse(&[]).unwrap(), ShutdownOptions::default());
        assert!(parse(&args(&["-f"])).unwrap().force);
    }
    #[test]
    fn shutdown_invalid_socket_never_falls_back_to_stopping_all() {
        for values in [
            vec!["--socket"],
            vec!["--socket", ""],
            vec!["--socket", "--force"],
            vec!["--socket", "a", "--daemon-socket", "b"],
            vec!["--unknown"],
            vec!["extra"],
        ] {
            assert!(parse(&args(&values)).is_err(), "{values:?}");
        }
    }
}
