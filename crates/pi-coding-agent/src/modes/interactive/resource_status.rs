//! Local worker resources, sampled off the render thread. TPS uses roster telemetry.
use super::generation_throughput::{parent_keys, summary_keys, Throughput};
use crate::modes::daemon::daemon_session_list::SessionSummary;
use crate::modes::interactive::components::subagent_summary_line::{
    linkage_row_for_roster_summary, RosterParent,
};
use pi_tui::utils::{strip_ansi, truncate_to_width, visible_width};
use std::cell::Cell;
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System};

const MAX_AGENTS: usize = 256;
const FRESH_FOR: Duration = Duration::from_secs(3);
const BLUE: &str = "\x1b[1;38;5;39m";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct Target {
    session: String,
    active: String,
    pid: u32,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct Request {
    root: String,
    targets: Vec<Target>,
    // Root identities prove that a PID belongs to this machine's worker, even
    // when the selected chat is a nested session sharing that worker.
    owners: Vec<Target>,
}

#[derive(Clone, Debug)]
struct Reading {
    cpu: Option<f32>,
    ram: Option<u64>,
}

#[derive(Default)]
struct State {
    request: Request,
    measured_request: Request,
    measured_at: Option<Instant>,
    readings: BTreeMap<u32, Reading>,
}

#[derive(Default)]
pub(super) struct Monitor {
    state: Arc<Mutex<State>>,
    started: Cell<bool>,
    agents: Vec<Agent>,
    omitted: usize,
    selected_at: Option<Instant>,
}

struct Agent {
    target: Target,
    label: String,
    rate: Option<(f64, bool)>,
    shared: bool,
}

fn target(row: &SessionSummary) -> Target {
    let live = row
        .active_session_id
        .as_deref()
        .is_some_and(|id| !id.is_empty())
        && row.lifecycle != "archived"
        && !matches!(
            row.worker_state.as_deref(),
            Some("stopping" | "recovering" | "failed")
        )
        && !matches!(
            row.status_label.as_deref(),
            Some("failed" | "recovering" | "queued")
        );
    Target {
        session: row.session_id.clone(),
        active: row.active_session_id.clone().unwrap_or_default(),
        pid: if live {
            row.worker_pid
                .and_then(|pid| u32::try_from(pid).ok())
                .unwrap_or(0)
        } else {
            0
        },
    }
}

/// Same parent-key contract as the agents list, including nested descendants.
fn family<'a>(rows: &'a [SessionSummary], parent: &RosterParent) -> Vec<&'a SessionSummary> {
    use crate::modes::agents_view::agents_view_state::get_parent_keys;
    let mut children: BTreeMap<String, Vec<&SessionSummary>> = BTreeMap::new();
    for row in rows
        .iter()
        .filter(|row| row.runtime_kind.as_deref() == Some("subagent"))
    {
        for key in get_parent_keys(&linkage_row_for_roster_summary(row)) {
            children.entry(key).or_default().push(row);
        }
    }
    let mut pending: VecDeque<_> = parent_keys(parent).into();
    let mut selected = BTreeMap::new();
    if let Some(root) = rows
        .iter()
        .find(|row| Some(&row.session_id) == parent.session_id.as_ref())
    {
        pending.extend(summary_keys(root));
        selected.insert(root.session_id.clone(), root);
    }
    let mut seen = BTreeSet::new();
    while let Some(key) = pending.pop_front() {
        if !seen.insert(key.clone()) {
            continue;
        }
        for row in children.remove(&key).unwrap_or_default() {
            if !row.session_id.is_empty() && !selected.contains_key(&row.session_id) {
                pending.extend(summary_keys(row));
                selected.insert(row.session_id.clone(), row);
            }
        }
    }
    let mut result: Vec<_> = selected.into_values().collect();
    result.sort_by_key(|row| {
        (
            Some(&row.session_id) != parent.session_id.as_ref(),
            row.session_id.clone(),
        )
    });
    result
}

impl Monitor {
    pub(super) fn start(&self, agent_dir: String) {
        if self.started.get() {
            return;
        }
        let weak = Arc::downgrade(&self.state);
        self.started.set(
            std::thread::Builder::new()
                .name("agent-resource-status".into())
                .spawn(move || {
                    let mut system = System::new();
                    let mut previous_pids = Vec::new();
                    let mut verified = BTreeMap::new();
                    loop {
                        let Some(state) = weak.upgrade() else {
                            break;
                        };
                        let request = state
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .request
                            .clone();
                        drop(state);
                        let pids: Vec<_> = request
                            .targets
                            .iter()
                            .filter(|t| t.pid > 0)
                            .map(|t| Pid::from_u32(t.pid))
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect();
                        if pids != previous_pids {
                            // sysinfo retains unrequested processes; bound its cache to this family.
                            system = System::new();
                            verified.clear();
                            previous_pids = pids.clone();
                        }
                        let old_starts: BTreeMap<_, _> = pids
                            .iter()
                            .filter_map(|pid| system.process(*pid).map(|p| (*pid, p.start_time())))
                            .collect();
                        if !pids.is_empty() {
                            system.refresh_processes_specifics(
                                ProcessesToUpdate::Some(&pids),
                                true,
                                ProcessRefreshKind::new().with_cpu().with_memory(),
                            );
                        }
                        let mut readings = BTreeMap::new();
                        for pid in &pids {
                            let Some(process) = system.process(*pid) else {
                                verified.remove(pid);
                                continue;
                            };
                            let identity = (
                                process.start_time(),
                                request
                                    .owners
                                    .iter()
                                    .filter(|t| t.pid == pid.as_u32())
                                    .cloned()
                                    .collect::<Vec<_>>(),
                            );
                            if verified.get(pid) != Some(&identity) {
                                if !verify_local_worker(&agent_dir, pid.as_u32(), &identity.1) {
                                    continue;
                                }
                                verified.insert(*pid, identity);
                            }
                            let cpu = process.cpu_usage();
                            readings.insert(
                                pid.as_u32(),
                                Reading {
                                    cpu: (old_starts.get(pid) == Some(&process.start_time())
                                        && cpu.is_finite()
                                        && cpu >= 0.0)
                                        .then_some(cpu),
                                    ram: (process.memory() > 0).then_some(process.memory()),
                                },
                            );
                        }
                        let Some(state) = weak.upgrade() else {
                            break;
                        };
                        {
                            let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                            if state.request == request {
                                state.readings = readings;
                                state.measured_request = request;
                                state.measured_at = Some(Instant::now());
                            }
                        }
                        drop(state);
                        std::thread::park_timeout(Duration::from_secs(1));
                    }
                })
                .is_ok(),
        );
    }

    pub(super) fn observe(
        &mut self,
        rows: &[SessionSummary],
        parent: &RosterParent,
        throughput: &Throughput,
    ) {
        let root = parent.session_id.clone().unwrap_or_default();
        let selected = family(rows, parent);
        self.omitted = selected.len().saturating_sub(MAX_AGENTS);
        let mut counts = BTreeMap::<u32, usize>::new();
        let mut owners: Vec<_> = rows.iter().map(target).filter(|t| t.pid > 0).collect();
        owners.sort_by(|a, b| (&a.session, &a.active, a.pid).cmp(&(&b.session, &b.active, b.pid)));
        owners.dedup();
        for owner in &owners {
            *counts.entry(owner.pid).or_default() += 1;
        }
        self.agents = selected
            .into_iter()
            .take(MAX_AGENTS)
            .map(|row| {
                let target = target(row);
                Agent {
                    label: if row.session_id == root {
                        "MAIN".into()
                    } else {
                        let name = row
                            .session_name
                            .as_deref()
                            .filter(|s| !s.trim().is_empty())
                            .or(row.rlm_child_id.as_deref())
                            .unwrap_or(&row.session_id);
                        clean_label(name)
                    },
                    rate: throughput.session_rate(&row.session_id),
                    shared: counts.get(&target.pid).is_some_and(|count| *count > 1),
                    target,
                }
            })
            .collect();
        if !self.agents.iter().any(|agent| agent.target.session == root) {
            self.agents.insert(
                0,
                Agent {
                    target: Target {
                        session: root.clone(),
                        ..Default::default()
                    },
                    label: "MAIN".into(),
                    rate: None,
                    shared: false,
                },
            );
        }
        let request = Request {
            root,
            targets: self.agents.iter().map(|a| a.target.clone()).collect(),
            owners,
        };
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.request.root != request.root {
            self.selected_at = Some(Instant::now());
        }
        state.request = request;
    }

    pub(super) fn render(
        &self,
        root: &str,
        width: usize,
        budget: usize,
        now: Instant,
    ) -> Vec<String> {
        if width == 0 || budget == 0 {
            return Vec::new();
        }
        let state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.request.root != root {
            return layout(&[], 0, width, budget, 0);
        }
        let fresh = state.request == state.measured_request
            && state
                .measured_at
                .is_some_and(|at| now.saturating_duration_since(at) <= FRESH_FOR);
        let labels: Vec<_> = self
            .agents
            .iter()
            .map(|agent| {
                let reading = fresh
                    .then(|| state.readings.get(&agent.target.pid))
                    .flatten();
                agent_label(agent, reading)
            })
            .collect();
        let page = self
            .selected_at
            .map(|at| now.saturating_duration_since(at).as_secs() / 4)
            .unwrap_or(0);
        layout(&labels, self.omitted, width, budget, page as usize)
    }
}

fn clean_label(label: &str) -> String {
    let clean: String = strip_ansi(label)
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    truncate_to_width(&clean, 16.0, "…", false)
}

fn agent_label(agent: &Agent, reading: Option<&Reading>) -> String {
    let cpu = reading
        .and_then(|r| r.cpu)
        .map(|n| format!("{n:.0}%"))
        .unwrap_or("—".into());
    let ram = reading
        .and_then(|r| r.ram)
        .map(|bytes| {
            if bytes >= 1024 * 1024 * 1024 {
                format!("{:.1}GiB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
            } else {
                format!("{:.0}MiB", bytes as f64 / (1024.0 * 1024.0))
            }
        })
        .unwrap_or("—".into());
    let tps = agent
        .rate
        .map(|(rate, estimated)| format!("{}{rate:.1}", if estimated { "~" } else { "" }))
        .unwrap_or("—".into());
    format!(
        "{}{} CPU {cpu} RAM {ram} TPS {tps}",
        agent.label,
        if agent.shared { " [shared]" } else { "" }
    )
}

fn layout(
    labels: &[String],
    omitted: usize,
    width: usize,
    budget: usize,
    page: usize,
) -> Vec<String> {
    if width == 0 || budget == 0 {
        return Vec::new();
    }
    let fallback = "MAIN CPU — RAM — TPS —".to_string();
    let main = labels.first().unwrap_or(&fallback);
    let children = &labels[labels.len().min(1)..];
    let mut pages = vec![vec![main.clone()]];
    for label in children {
        let rows = pages.last_mut().unwrap();
        let last = rows.last_mut().unwrap();
        if visible_width(last) + 3 + visible_width(label) <= width {
            last.push_str(" │ ");
            last.push_str(label);
        } else if rows.len() < budget {
            rows.push(label.clone());
        } else {
            // One-row terminals rotate workers with MAIN; otherwise pin MAIN.
            pages.push(if budget == 1 {
                vec![label.clone()]
            } else {
                vec![main.clone(), label.clone()]
            });
        }
    }
    let count = pages.len();
    let selected = page % count;
    let mut rows = pages.swap_remove(selected);
    if count > 1 || omitted > 0 {
        let suffix = format!(
            " [{}/{}{}]",
            selected + 1,
            count,
            if omitted > 0 {
                format!(" +{omitted}")
            } else {
                String::new()
            }
        );
        let last = rows.last_mut().unwrap();
        *last = format!(
            "{}{}",
            truncate_to_width(
                last,
                width.saturating_sub(visible_width(&suffix)) as f64,
                "…",
                false
            ),
            suffix
        );
    }
    rows.into_iter()
        .take(budget)
        .map(|row| {
            format!(
                "{BLUE}{}\x1b[22;39m",
                truncate_to_width(&row, width as f64, "…", false)
            )
        })
        .collect()
}

// Deserialize only identity fields; credentials and session content are never used.
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkerIdentity {
    pid: u32,
    process_start_id: Option<String>,
    root_active_session_id: String,
}

fn verify_local_worker(agent_dir: &str, pid: u32, owners: &[Target]) -> bool {
    use std::io::Read;
    let root = Path::new(agent_dir).join("daemon-workers");
    let Ok(dirs) = std::fs::read_dir(root) else {
        return false;
    };
    for dir in dirs.flatten().take(MAX_AGENTS) {
        let Ok(files) = std::fs::read_dir(dir.path()) else {
            continue;
        };
        for file in files.flatten().take(4096) {
            if file.path().extension().is_none_or(|ext| ext != "json") {
                continue;
            }
            let Ok(file) = std::fs::File::open(file.path()) else {
                continue;
            };
            let mut bytes = Vec::new();
            if file.take(64 * 1024).read_to_end(&mut bytes).is_err() {
                continue;
            }
            let Ok(identity) = serde_json::from_slice::<WorkerIdentity>(&bytes) else {
                continue;
            };
            if identity.pid == pid
                && owners
                    .iter()
                    .any(|owner| owner.active == identity.root_active_session_id)
                && identity.process_start_id.is_some()
                && identity.process_start_id
                    == crate::core::session_lease::get_process_start_id(i64::from(pid))
            {
                return true;
            }
        }
    }
    false
}

#[cfg(test)]
#[path = "resource_status_tests.rs"]
mod tests;
