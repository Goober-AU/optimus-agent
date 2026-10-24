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
    owner: String,
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
    readings: BTreeMap<String, Reading>,
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
        owner: if live {
            row.active_session_id.clone().unwrap_or_default()
        } else {
            String::new()
        },
        pid: if live {
            row.worker_pid
                .and_then(|pid| u32::try_from(pid).ok())
                .unwrap_or(0)
        } else {
            0
        },
    }
}

fn worker_owner<'a>(
    row: &'a SessionSummary,
    rows: &BTreeMap<String, &'a SessionSummary>,
) -> String {
    use crate::modes::agents_view::agents_view_state::get_parent_keys;
    let mut current = row;
    let mut seen = BTreeSet::new();
    while current.runtime_kind.as_deref() == Some("subagent") {
        if !seen.insert(current.session_id.as_str()) {
            return String::new();
        }
        let keys = get_parent_keys(&linkage_row_for_roster_summary(current));
        let Some(parent) = keys.iter().find_map(|key| rows.get(key).copied()) else {
            break;
        };
        if current
            .worker_pid
            .zip(parent.worker_pid)
            .is_some_and(|(child, parent)| child > 0 && parent > 0 && child != parent)
        {
            break;
        }
        current = parent;
    }
    target(current).owner
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
    pub(super) fn start(&self, agent_dir: String, on_update: Arc<dyn Fn() + Send + Sync>) {
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
                        // Roster pushes omit workerPid. Resolve verified local descriptors
                        // by their root active-session identity, then share the root's PID.
                        let workers = local_worker_pids(&agent_dir, &request.owners);
                        let resolved: Vec<_> = request
                            .targets
                            .iter()
                            .filter_map(|target| {
                                let pid = *workers.get(&target.owner)?;
                                (target.pid == 0 || target.pid == pid)
                                    .then_some((target.session.clone(), pid))
                            })
                            .collect();
                        let pids: Vec<_> = resolved
                            .iter()
                            .map(|(_, pid)| Pid::from_u32(*pid))
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect();
                        if pids != previous_pids {
                            // sysinfo retains unrequested processes; bound its cache to this family.
                            system = System::new();
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
                                ProcessRefreshKind::nothing().with_cpu().with_memory(),
                            );
                        }
                        let mut readings = BTreeMap::new();
                        for pid in &pids {
                            let Some(process) = system.process(*pid) else {
                                continue;
                            };
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
                                state.readings = resolved
                                    .iter()
                                    .filter_map(|(session, pid)| {
                                        readings
                                            .get(pid)
                                            .map(|reading| (session.clone(), reading.clone()))
                                    })
                                    .collect();
                                state.measured_request = request.clone();
                                state.measured_at = Some(Instant::now());
                            }
                        }
                        drop(state);
                        if request
                            .targets
                            .iter()
                            .any(|target| !target.owner.is_empty())
                        {
                            on_update();
                        }
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
        let mut counts = BTreeMap::<String, usize>::new();
        let mut indexed = BTreeMap::new();
        for row in rows {
            for key in summary_keys(row) {
                indexed.entry(key).or_insert(row);
            }
        }
        let mut owners: Vec<_> = rows
            .iter()
            .map(target)
            .filter(|t| !t.owner.is_empty())
            .collect();
        owners.sort_by(|a, b| (&a.session, &a.active, a.pid).cmp(&(&b.session, &b.active, b.pid)));
        owners.dedup();
        for row in rows {
            if !target(row).owner.is_empty() {
                *counts.entry(worker_owner(row, &indexed)).or_default() += 1;
            }
        }
        self.agents = selected
            .into_iter()
            .take(MAX_AGENTS)
            .map(|row| {
                let mut target = target(row);
                if !target.owner.is_empty() {
                    target.owner = worker_owner(row, &indexed);
                }
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
                    shared: !target.owner.is_empty()
                        && counts.get(&target.owner).is_some_and(|count| *count > 1),
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
                    .then(|| state.readings.get(&agent.target.session))
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
    truncate_to_width(&clean, 12.0, "…", false)
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
                format!("{:.1}G", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
            } else {
                format!("{:.0}M", bytes as f64 / (1024.0 * 1024.0))
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
        if agent.shared { "*" } else { "" }
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
        if rows.len() < budget {
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
    root_session_id: Option<String>,
}

fn local_worker_pids(agent_dir: &str, owners: &[Target]) -> BTreeMap<String, u32> {
    use std::io::Read;
    let mut verified = BTreeMap::new();
    if owners.is_empty() {
        return verified;
    }
    let root = Path::new(agent_dir).join("daemon-workers");
    let Ok(dirs) = std::fs::read_dir(root) else {
        return verified;
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
            if identity.pid > 0
                && owners.iter().any(|owner| {
                    !owner.owner.is_empty()
                        && owner.active == identity.root_active_session_id
                        && (owner.pid == 0 || owner.pid == identity.pid)
                        && identity
                            .root_session_id
                            .as_ref()
                            .is_none_or(|session| session == &owner.session)
                })
                && identity.process_start_id.is_some()
                && identity.process_start_id
                    == crate::core::session_lease::get_process_start_id(i64::from(identity.pid))
            {
                verified.insert(identity.root_active_session_id, identity.pid);
            }
        }
    }
    verified
}

#[cfg(test)]
#[path = "resource_status_tests.rs"]
mod tests;
