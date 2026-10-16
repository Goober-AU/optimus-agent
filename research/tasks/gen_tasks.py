#!/usr/bin/env python3
"""Deterministic benchmark task suite generator for the OCC-2/OCC-3 campaign.

Five task families, each a two-agent communication problem: a sender agent
receives `sender_input` and must communicate the facts a receiver agent needs
to answer `receiver_task`. Gold answers are machine-checkable via
checkers.py.

Families:
- A fact handoff: 4-8 key/value facts; the question needs 1-2 of them.
- B state update: a sequence of state-change events; identify the final
  valid state.
- C procedural transfer: a short runbook plus a progress marker; identify
  a dependent step.
- D multi-fact reasoning: combine at least 3 entity facts to select a set.
- E dependent calculation: numbers handed over by the sender; the receiver
  completes the calculation.

Splits: DEV (30 tasks per family) and HOLDOUT (30 tasks per family).

Outputs, written next to this script: tasks_dev.json and tasks_holdout.json
with the shape {"meta": {...}, "tasks": [...]}; each task carries exactly
{task_id, family, sender_input, receiver_task, gold_answer, required_facts,
difficulty}.

Determinism: all randomness flows from sha256-derived seeds; two runs on
the same interpreter produce byte-identical files. No wall-clock input.

Self-test: running `python gen_tasks.py` regenerates the files, then runs
every family checker against every gold answer (must pass 100%), negative
checks that wrong answers are rejected, and a regeneration-equality check.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import checkers

SEED_BASE = "occ23-bench-tasks-v1"
FAMILIES = ("A", "B", "C", "D", "E")
SPLITS = ("dev", "holdout")
SPLIT_SIZES = {"dev": 30, "holdout": 30}
FAMILY_NAMES = {
    "A": "fact handoff",
    "B": "state update",
    "C": "procedural transfer",
    "D": "multi-fact reasoning",
    "E": "dependent calculation",
}


def derive_seed(*parts):
    """Derive a stable integer seed from the seed base and name parts."""
    joined = "|".join((SEED_BASE,) + tuple(parts))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def make_rng(*parts):
    return random.Random(derive_seed(*parts))


def difficulty_from_score(score):
    if score <= 6:
        return "easy"
    if score <= 8:
        return "medium"
    return "hard"


# Shared vocabulary pools (ASCII, plain technical English).
TEAMS = ("atlas", "borealis", "cinder", "delta", "ember", "fjord", "grit", "halcyon")
REGIONS = ("us-east", "us-west", "eu-west", "eu-north", "ap-south", "sa-east")
SERVICE_HEADS = (
    "model", "auth", "billing", "search", "cache", "ingest",
    "notify", "render", "policy", "audit", "vector", "gateway",
)
SERVICE_TAILS = ("gateway", "service", "edge", "hub", "relay", "api", "daemon", "scheduler")
HEX_DIGITS = "0123456789abcdef"
SERVICE_STATUS = ("healthy", "degraded", "failing", "paused")


def rand_hex(rng, n):
    return "".join(rng.choice(HEX_DIGITS) for _ in range(n))


def rand_version(rng):
    return "v%d.%d.%d" % (rng.randint(0, 9), rng.randint(0, 20), rng.randint(0, 30))


def rand_host(rng):
    return "10.%d.%d.%d" % (rng.randint(0, 255), rng.randint(0, 255), rng.randint(2, 254))


def rand_service_name(rng):
    return rng.choice(SERVICE_HEADS) + "-" + rng.choice(SERVICE_TAILS)


# ---------------------------------------------------------------- family A

def domain_facts_a(rng, domain):
    """Build the fact pool for one family A domain.

    Returns (entity_name, facts, composed_allowed) where facts is a list of
    (key, label, value) tuples.
    """
    if domain == "service":
        name = rand_service_name(rng)
        facts = [
            ("service name", "service name", name),
            ("listen port", "listen port", str(rng.randint(1024, 65535))),
            ("host address", "host address", rand_host(rng)),
            ("deployed version", "deployed version", rand_version(rng)),
            ("owner team", "owner team", rng.choice(TEAMS)),
            ("region", "region", rng.choice(REGIONS)),
            ("replica count", "replica count", str(rng.randint(2, 12))),
            ("environment", "environment", rng.choice(("production", "staging"))),
        ]
        return name, facts, True
    if domain == "database":
        name = rand_service_name(rng)
        facts = [
            ("database engine", "database engine", rng.choice(("postgres", "mysql", "mongodb", "redis"))),
            ("host address", "host address", rand_host(rng)),
            ("listen port", "listen port", str(rng.randint(1024, 65535))),
            ("database name", "database name", "db_" + rng.choice(SERVICE_HEADS)),
            ("app user", "app user", "svc_" + rng.choice(SERVICE_HEADS)),
            ("max connections", "max connections", str(rng.randint(20, 800))),
            ("backup window", "backup window", "%02d:00-%02d:00 UTC" % (rng.randint(0, 5), rng.randint(6, 9))),
        ]
        return name, facts, True
    if domain == "release":
        name = "release " + rand_version(rng)
        facts = [
            ("release version", "release version", rand_version(rng)),
            ("source commit", "source commit", rand_hex(rng, 9)),
            ("release channel", "release channel", rng.choice(("stable", "beta", "nightly"))),
            ("signatory", "signatory", "release engineer " + rng.choice(("moreau", "okafor", "lindqvist", "tanaka", "ibarra"))),
            ("artifact digest", "artifact digest", rand_hex(rng, 12)),
            ("release date", "release date", "2026-11-%02d" % rng.randint(1, 28)),
        ]
        return name, facts, False
    if domain == "quota":
        name = "subscription sub-%05d" % rng.randint(1, 99999)
        facts = [
            ("vCPU limit", "vCPU limit", str(rng.choice((64, 128, 256, 512, 1024)))),
            ("vCPU used", "vCPU used", str(rng.randint(4, 96))),
            ("VM family", "VM family", "standard-d%ds" % rng.randint(2, 8)),
            ("region", "region", rng.choice(REGIONS)),
            ("quota expiry", "quota expiry", "2027-02-%02d" % rng.randint(1, 28)),
            ("owner team", "owner team", rng.choice(TEAMS)),
        ]
        return name, facts, False
    # cluster
    name = "nodepool pool-" + rng.choice(SERVICE_HEADS)
    facts = [
        ("node count", "node count", str(rng.randint(3, 40))),
        ("kubernetes version", "kubernetes version", "v1.%d.%d" % (rng.randint(25, 33), rng.randint(0, 9))),
        ("autoscale maximum", "autoscale maximum", str(rng.randint(5, 60))),
        ("region", "region", rng.choice(REGIONS)),
        ("owner team", "owner team", rng.choice(TEAMS)),
        ("container runtime", "container runtime", rng.choice(("containerd", "cri-o"))),
    ]
    return name, facts, False


def gen_task_a(rng, index, split):
    domain = rng.choice(("service", "database", "release", "quota", "cluster"))
    entity, pool, composed_allowed = domain_facts_a(rng, domain)
    composed = composed_allowed and rng.random() < 0.4
    total = len(pool)
    fact_count = rng.randint(min(4, total), min(8, total))
    if composed:
        # host and port facts must both be present for the composed question
        chosen = pool[:fact_count]
        needed_keys = ("host address", "listen port")
        keys = [f[0] for f in chosen]
        for needed in needed_keys:
            if needed not in keys:
                swappable = [i for i, f in enumerate(chosen) if f[0] not in needed_keys]
                swap = rng.choice(swappable)
                chosen[swap] = next(f for f in pool if f[0] == needed)
                keys = [f[0] for f in chosen]
        facts = list(chosen)
        rng.shuffle(facts)
    else:
        facts = rng.sample(pool, fact_count)
    fact_ids = ["F%d" % (i + 1) for i in range(len(facts))]

    if composed:
        host = next(v for k, _, v in facts if k == "host address")
        port = next(v for k, _, v in facts if k == "listen port")
        gold = host + ":" + port
        required = [fid for fid, (k, _, _) in zip(fact_ids, facts) if k in ("host address", "listen port")]
        receiver_task = (
            "Using only the facts the sender communicates, on which host and port does "
            + entity
            + " listen? Answer as host:port."
        )
    else:
        eligible = [i for i, f in enumerate(facts) if f[2] != entity]
        label_index = rng.choice(eligible) if eligible else rng.randrange(len(facts))
        key, label, gold = facts[label_index]
        required = [fact_ids[label_index]]
        receiver_task = (
            "Using only the facts the sender communicates, what is the "
            + label
            + " for "
            + entity
            + "? Answer with the value only."
        )

    lines = ["Configuration briefing for " + entity + " (fact ids in brackets):"]
    for fid, (key, label, value) in zip(fact_ids, facts):
        lines.append("  [" + fid + "] " + label + ": " + value)
    score = len(facts) + (2 if composed else 0)
    if score <= 5:
        difficulty = "easy"
    elif score <= 7:
        difficulty = "medium"
    else:
        difficulty = "hard"
    return {
        "task_id": "A-%s-%03d" % (split, index),
        "family": "A",
        "sender_input": "\n".join(lines),
        "receiver_task": receiver_task,
        "gold_answer": gold,
        "required_facts": required,
        "difficulty": difficulty,
    }


# ---------------------------------------------------------------- family B

STATE_MACHINES = (
    {
        "label": "deployment",
        "prefix": "dep",
        "description": "a service rollout",
        "states": ("pending", "building", "built", "testing", "tested", "deployed", "rolled_back"),
        "start": "pending",
        "transitions": (
            ("pending", "building"), ("building", "built"), ("built", "testing"),
            ("testing", "tested"), ("tested", "deployed"), ("deployed", "rolled_back"),
        ),
    },
    {
        "label": "virtual machine",
        "prefix": "vm",
        "description": "a compute instance",
        "states": ("provisioning", "running", "stopping", "stopped", "terminated"),
        "start": "provisioning",
        "transitions": (
            ("provisioning", "running"), ("running", "stopping"), ("stopping", "stopped"),
            ("stopped", "running"), ("running", "terminated"), ("stopped", "terminated"),
        ),
    },
    {
        "label": "order",
        "prefix": "ord",
        "description": "a customer order",
        "states": ("placed", "confirmed", "packed", "shipped", "delivered", "cancelled"),
        "start": "placed",
        "transitions": (
            ("placed", "confirmed"), ("confirmed", "packed"), ("packed", "shipped"),
            ("shipped", "delivered"), ("placed", "cancelled"), ("confirmed", "cancelled"),
        ),
    },
    {
        "label": "pull request",
        "prefix": "pr",
        "description": "a code change",
        "states": ("draft", "open", "in_review", "approved", "merged", "closed"),
        "start": "draft",
        "transitions": (
            ("draft", "open"), ("open", "in_review"), ("in_review", "approved"),
            ("approved", "merged"), ("open", "closed"), ("in_review", "closed"),
        ),
    },
    {
        "label": "session",
        "prefix": "ses",
        "description": "an agent session",
        "states": ("created", "active", "idle", "closed"),
        "start": "created",
        "transitions": (
            ("created", "active"), ("active", "idle"), ("idle", "active"),
            ("active", "closed"), ("idle", "closed"),
        ),
    },
)


def gen_task_b(rng, index, split):
    machine = rng.choice(STATE_MACHINES)
    split_tag = "d" if split == "dev" else "h"
    entity = "%s-%s%04d" % (machine["prefix"], split_tag, rng.randint(1000, 9899))
    legal = set(machine["transitions"])
    successors = {}
    for src, dst in machine["transitions"]:
        successors.setdefault(src, []).append(dst)
    marked = rng.random() < 0.55
    n_events = rng.randint(3, 7)

    state = machine["start"]
    events = []
    for i in range(n_events):
        eid = "E%d" % (i + 1)
        options = successors.get(state, [])
        accept = bool(options) and (i == 0 or rng.random() < 0.7)
        if accept:
            dst = rng.choice(options)
            events.append((eid, state, dst, True))
            state = dst
        else:
            # Rejected proposal: stale from-state or an illegal transition pair.
            if rng.random() < 0.5:
                # Stale from-state: the pair itself may be perfectly legal.
                frm = rng.choice([s for s in machine["states"] if s != state])
                legal_targets = [s for s in machine["states"] if (frm, s) in legal]
                if legal_targets and rng.random() < 0.5:
                    dst = rng.choice(legal_targets)
                else:
                    dst = rng.choice([s for s in machine["states"] if s != frm])
            else:
                # Illegal transition pair proposed from the current state.
                frm = state
                bad_targets = [s for s in machine["states"] if s != frm and (frm, s) not in legal]
                if not bad_targets:
                    bad_targets = [s for s in machine["states"] if s != frm]
                dst = rng.choice(bad_targets)
            events.append((eid, frm, dst, False))
    final = state

    lines = [
        "Entity under observation: " + machine["label"] + " " + entity + " (" + machine["description"] + ").",
        "Initial state: " + machine["start"] + ".",
        "Legal transitions accepted by the controller:",
    ]
    for src, dst in machine["transitions"]:
        lines.append("  " + src + " -> " + dst)
    if marked:
        lines.append("Event log, in chronological order (each event is marked with the controller decision):")
        for eid, frm, dst, ok in events:
            if ok:
                lines.append("  " + eid + ": " + frm + " -> " + dst + " (accepted)")
            elif (frm, dst) in legal:
                lines.append("  " + eid + ": " + frm + " -> " + dst + " (rejected: stale from-state)")
            else:
                lines.append("  " + eid + ": " + frm + " -> " + dst + " (rejected: transition not legal)")
    else:
        lines.append(
            "The controller applies an event only when the event from-state equals the current "
            "state and the transition is listed as legal; every other event is rejected "
            "automatically."
        )
        lines.append("Event log, in chronological order:")
        for eid, frm, dst, ok in events:
            lines.append("  " + eid + ": " + frm + " -> " + dst)

    required = ["INIT"] + [eid for eid, _, _, ok in events if ok]
    score = n_events + (3 if not marked else 0)
    return {
        "task_id": "B-%s-%03d" % (split, index),
        "family": "B",
        "sender_input": "\n".join(lines),
        "receiver_task": (
            "Using only the state change events the sender communicates, what is the final valid "
            "state of " + machine["label"] + " " + entity + "? Answer with the exact state name."
        ),
        "gold_answer": final,
        "required_facts": required,
        "difficulty": difficulty_from_score(score),
    }


# ---------------------------------------------------------------- family C

RUNBOOKS = (
    (
        "service deploy",
        (
            "announce the maintenance window",
            "drain the load balancer",
            "stop the service",
            "apply the database migration",
            "run the smoke tests",
            "restore traffic to the service",
            "verify the error budget",
            "close the incident channel",
        ),
    ),
    (
        "incident triage",
        (
            "open the incident channel",
            "collect the failing request ids",
            "check the rolling error rate",
            "bisect the last five deploys",
            "write the mitigation note",
            "page the on-call engineer",
            "schedule the postmortem",
            "archive the incident log",
        ),
    ),
    (
        "data migration",
        (
            "snapshot the source database",
            "record the source row counts",
            "start the bulk copy",
            "verify the checksums on the target",
            "update the application config",
            "resume the write traffic",
            "watch the replication lag for one hour",
            "decommission the source database",
        ),
    ),
    (
        "release cut",
        (
            "freeze the main branch",
            "update the version constant",
            "update the changelog",
            "build the release binaries",
            "upload the artifacts to the registry",
            "run the installer smoke test",
            "tag the release commit",
            "announce the release",
        ),
    ),
    (
        "key rotation",
        (
            "generate the new key pair",
            "stage the new public key",
            "flip the serving key",
            "watch the error rate for one hour",
            "revoke the old key",
            "update the key inventory",
            "archive the rotation receipt",
            "notify the security channel",
        ),
    ),
)


def gen_task_c(rng, index, split):
    title, all_steps = rng.choice(RUNBOOKS)
    n = rng.randint(4, min(8, len(all_steps)))
    steps = list(all_steps[:n])
    step_ids = ["S%d" % (i + 1) for i in range(n)]
    qtype = rng.choice(("next", "next", "before", "locate"))

    marker = None
    if qtype == "next":
        k = rng.randint(1, n - 1)
        marker = (1, k)
        gold = steps[k]
        required = ["MARK", step_ids[k]]
        receiver_task = (
            "Using only the procedure the sender communicates, which runbook step must be "
            "performed next? Answer with the step text exactly as written."
        )
    elif qtype == "before":
        j = rng.randint(2, n)
        gold = steps[j - 2]
        required = [step_ids[j - 1], step_ids[j - 2]]
        receiver_task = (
            "Using only the procedure the sender communicates, which step comes immediately "
            "before the step that says '" + steps[j - 1] + "'? Answer with that step's text "
            "exactly as written."
        )
    else:
        j = rng.randint(1, n)
        gold = str(j)
        required = [step_ids[j - 1]]
        receiver_task = (
            "Using only the procedure the sender communicates, at which step number does the "
            "runbook say '" + steps[j - 1] + "'? Answer with the number only."
        )

    lines = [
        "Procedure handoff: the '" + title + "' runbook (step ids in brackets).",
    ]
    for sid, step in zip(step_ids, steps):
        lines.append("  [" + sid + "] " + step)
    if marker is not None:
        first, last = marker
        if first == last:
            lines.append("Progress marker: the operator has completed step S" + str(first) + ".")
        else:
            lines.append(
                "Progress marker: the operator has completed steps S" + str(first)
                + " through S" + str(last) + "."
            )
    score = n + (1 if qtype == "before" else 0)
    return {
        "task_id": "C-%s-%03d" % (split, index),
        "family": "C",
        "sender_input": "\n".join(lines),
        "receiver_task": receiver_task,
        "gold_answer": gold,
        "required_facts": required,
        "difficulty": difficulty_from_score(score),
    }


# ---------------------------------------------------------------- family D

D_ATTRIBUTES = ("team", "status", "region")


def gen_task_d(rng, index, split):
    variant = rng.choice(("team+status", "status+region", "team+region"))
    team = rng.choice(TEAMS)
    status = rng.choice(SERVICE_STATUS)
    region = rng.choice(REGIONS)
    other_teams = [t for t in TEAMS if t != team]
    other_statuses = [s for s in SERVICE_STATUS if s != status]
    other_regions = [r for r in REGIONS if r != region]

    if variant == "team+status":
        def make_match():
            return (team, status, rng.choice(REGIONS))

        def make_near():
            if rng.random() < 0.5:
                return (team, rng.choice(other_statuses), rng.choice(REGIONS))
            return (rng.choice(other_teams), status, rng.choice(REGIONS))

        def make_other():
            return (rng.choice(other_teams), rng.choice(other_statuses), rng.choice(REGIONS))
    elif variant == "status+region":
        def make_match():
            return (rng.choice(TEAMS), status, region)

        def make_near():
            if rng.random() < 0.5:
                return (rng.choice(TEAMS), status, rng.choice(other_regions))
            return (rng.choice(TEAMS), rng.choice(other_statuses), region)

        def make_other():
            return (rng.choice(TEAMS), rng.choice(other_statuses), rng.choice(other_regions))
    else:
        def make_match():
            return (team, rng.choice(SERVICE_STATUS), region)

        def make_near():
            if rng.random() < 0.5:
                return (team, rng.choice(SERVICE_STATUS), rng.choice(other_regions))
            return (rng.choice(other_teams), rng.choice(SERVICE_STATUS), region)

        def make_other():
            return (rng.choice(other_teams), rng.choice(SERVICE_STATUS), rng.choice(other_regions))

    match_count = rng.randint(1, 3) if rng.random() < 0.8 else 0
    near_count = rng.randint(2, 3)
    if match_count + near_count < 3:
        near_count = 3 - match_count
    other_count = rng.randint(1, 3)
    total = match_count + near_count + other_count

    name_pool = [h + "-" + t for h in SERVICE_HEADS for t in SERVICE_TAILS]
    rng.shuffle(name_pool)
    names = name_pool[:total]

    entities = []
    for i in range(match_count):
        ent_team, ent_status, ent_region = make_match()
        entities.append((names[i], ent_team, ent_status, ent_region))
    for i in range(near_count):
        ent_team, ent_status, ent_region = make_near()
        entities.append((names[match_count + i], ent_team, ent_status, ent_region))
    for i in range(other_count):
        ent_team, ent_status, ent_region = make_other()
        entities.append((names[match_count + near_count + i], ent_team, ent_status, ent_region))

    matches = [e[0] for e in entities[:match_count]]
    order = list(range(total))
    rng.shuffle(order)
    fact_ids = ["F%d" % (i + 1) for i in range(total)]
    # Display position i shows entity order[i]; map entity position -> fact id.
    entity_pos_to_fid = {order[i]: fact_ids[i] for i in range(total)}

    lines = ["Service inventory report (fact ids in brackets):"]
    for fid, pos in zip(fact_ids, order):
        name, ent_team, ent_status, ent_region = entities[pos]
        lines.append(
            "  [" + fid + "] service " + name + ", team " + ent_team + ", status " + ent_status
            + ", region " + ent_region + ", tier " + rng.choice(("free", "standard", "premium"))
            + ", replicas " + str(rng.randint(1, 9))
        )

    if variant == "team+status":
        question = (
            "Using only the inventory the sender communicates, which services are owned by team "
            + team + " and currently in status " + status + "? List the service names, comma-separated."
        )
    elif variant == "status+region":
        question = (
            "Using only the inventory the sender communicates, which services are currently in "
            "status " + status + " and located in region " + region + "? List the service names, "
            "comma-separated."
        )
    else:
        question = (
            "Using only the inventory the sender communicates, which services are owned by team "
            + team + " and located in region " + region + "? List the service names, comma-separated."
        )

    gold = ", ".join(sorted(matches)) if matches else "none"
    involved = set(range(match_count)) | set(range(match_count, match_count + near_count))
    required = [entity_pos_to_fid[pos] for pos in sorted(involved)]
    score = total
    return {
        "task_id": "D-%s-%03d" % (split, index),
        "family": "D",
        "sender_input": "\n".join(lines),
        "receiver_task": question,
        "gold_answer": gold,
        "required_facts": required,
        "difficulty": difficulty_from_score(score),
    }


# ---------------------------------------------------------------- solvers
# Independent solvers re-derive the expected answer by parsing the rendered
# sender_input text. The self-test requires solver output == gold_answer,
# which proves the rendered input actually contains the information needed.

_FACT_LINE = re.compile(r"^\s*\[(F\d+)\] ([^:]+): (.+)$")
_STEP_LINE = re.compile(r"^\s*\[(S\d+)\] (.+)$")
_TRANSITION_LINE = re.compile(r"^\s+(\S+) -> (\S+)$")
_EVENT_LINE = re.compile(r"^\s*(E\d+): (\S+) -> (\S+)")


def _parse_facts(text):
    facts = {}
    for line in text.splitlines():
        match = _FACT_LINE.match(line)
        if match:
            facts[match.group(1)] = (match.group(2), match.group(3))
    return facts


def solve_a(task):
    facts = _parse_facts(task["sender_input"])
    question = task["receiver_task"]
    if "on which host and port" in question:
        host = next(v for _, (label, v) in sorted(facts.items()) if label == "host address")
        port = next(v for _, (label, v) in sorted(facts.items()) if label == "listen port")
        return host + ":" + port
    match = re.search(r"what is the (.+?) for .+\? Answer with the value only\.", question)
    if not match:
        raise AssertionError("unhandled A question: " + question)
    label = match.group(1)
    values = [v for lbl, v in facts.values() if lbl == label]
    if len(values) != 1:
        raise AssertionError("label %r matched %d facts" % (label, len(values)))
    return values[0]


def solve_b(task):
    text = task["sender_input"]
    legal = set()
    current = None
    events = []
    for line in text.splitlines():
        transition = _TRANSITION_LINE.match(line)
        if transition:
            legal.add((transition.group(1), transition.group(2)))
            continue
        if line.startswith("Initial state: "):
            current = line[len("Initial state: "):].strip().rstrip(".")
            continue
        event = _EVENT_LINE.match(line)
        if event and line.strip().startswith(event.group(1) + ":"):
            events.append((event.group(2), event.group(3)))
    if current is None or not events:
        raise AssertionError("could not parse family B input")
    for frm, dst in events:
        if frm == current and (frm, dst) in legal:
            current = dst
    return current


def solve_c(task):
    text = task["sender_input"]
    steps = []
    for line in text.splitlines():
        match = _STEP_LINE.match(line)
        if match:
            steps.append(match.group(2))
    question = task["receiver_task"]
    if "which runbook step must be performed next" in question:
        marker = re.search(r"completed steps? S(\d+)(?: through S(\d+))?", text)
        if not marker:
            raise AssertionError("no progress marker found")
        k = int(marker.group(2) or marker.group(1))
        if k >= len(steps):
            raise AssertionError("marker at or past the end of the runbook")
        return steps[k]
    if "which step comes immediately before the step that says" in question:
        quoted = re.search(r"step that says '(.+?)'", question)
        index = steps.index(quoted.group(1))
        if index == 0:
            raise AssertionError("no step before the first step")
        return steps[index - 1]
    if "at which step number does the runbook say" in question:
        quoted = re.search(r"runbook say '(.+?)'", question)
        return str(steps.index(quoted.group(1)) + 1)
    raise AssertionError("unhandled C question: " + question)


def solve_d(task):
    text = task["sender_input"]
    question = task["receiver_task"]
    inventory = []
    for line in text.splitlines():
        match = re.match(r"^\s*\[F\d+\] service ([a-z-]+), team ([a-z-]+), status ([a-z-]+), region ([a-z-]+),", line)
        if match:
            inventory.append(match.groups())
    pair = None
    m1 = re.search(r"owned by team (\S+) and currently in status (\S+?)\?", question)
    m2 = re.search(r"currently in status (\S+?) and located in region (\S+?)\?", question)
    m3 = re.search(r"owned by team (\S+) and located in region (\S+?)\?", question)
    if m1:
        pair = (lambda e: e[1] == m1.group(1) and e[2] == m1.group(2))
    elif m2:
        pair = (lambda e: e[2] == m2.group(1) and e[3] == m2.group(2))
    elif m3:
        pair = (lambda e: e[1] == m3.group(1) and e[3] == m3.group(2))
    else:
        raise AssertionError("unhandled D question: " + question)
    names = sorted(e[0] for e in inventory if pair(e))
    return ", ".join(names) if names else "none"


def solve_e(task):
    facts = _parse_facts(task["sender_input"])
    values = {label: value for label, value in facts.values()}
    question = task["receiver_task"]
    latencies = [int(v) for label, v in sorted(values.items()) if "latency (ms)" in label and "threshold" not in label]
    durations = [int(v) for label, v in sorted(values.items()) if "duration (s)" in label and "timeout" not in label]
    spends = [int(v) for label, v in sorted(values.items()) if "recorded spend" in label]
    arrivals = [int(v) for label, v in sorted(values.items()) if "arrivals" in label]
    served = [int(v) for label, v in sorted(values.items()) if "served" in label]
    if "difference between the highest and the lowest latency" in question:
        return max(latencies) - min(latencies)
    if "highest latency sample" in question:
        return max(latencies)
    if "lowest latency sample" in question:
        return min(latencies)
    if "exceed the alert threshold" in question:
        threshold = int(values["alert threshold (ms)"])
        return sum(1 for x in latencies if x > threshold)
    if "total of the latency samples" in question:
        return sum(latencies)
    if "mean latency" in question:
        return sum(latencies) // len(latencies)
    if "remains after the recorded spends" in question:
        return int(values["total budget (USD)"]) - sum(spends)
    if "total amount spent" in question:
        return sum(spends)
    if "largest single spend" in question:
        return max(spends)
    if "CPU cores" in question:
        return int(values["node count"]) * int(values["cores per node"])
    if "pods" in question and "in total" in question:
        return int(values["node count"]) * int(values["pods per node"])
    if "total number of arrivals" in question:
        return sum(arrivals)
    if "total number of served" in question:
        return sum(served)
    if "accumulated backlog" in question:
        return sum(arrivals) - sum(served)
    if "total wall time" in question:
        return sum(durations)
    if "spread" in question:
        return max(durations) - min(durations)
    if "longest test" in question:
        return max(durations)
    if "exceed the timeout budget" in question:
        timeout = int(values["timeout budget (s)"])
        return sum(1 for x in durations if x > timeout)
    raise AssertionError("unhandled E question: " + question)


SOLVERS = {"A": solve_a, "B": solve_b, "C": solve_c, "D": solve_d, "E": solve_e}


# ---------------------------------------------------------------- family E

def _facts_block(title, pairs):
    lines = ["Measurement handoff: " + title + " (fact ids in brackets):"]
    for fid, (label, value) in pairs:
        lines.append("  [" + fid + "] " + label + ": " + str(value))
    return "\n".join(lines)


def gen_task_e(rng, index, split):
    config = rng.choice(("latency", "budget", "capacity", "queue", "durations"))
    pairs = []  # list of (label, value)
    required = []
    if config == "latency":
        service = rand_service_name(rng)
        n = rng.randint(4, 10)
        samples = [rng.randint(40, 950) for _ in range(n)]
        threshold = rng.randint(150, 900)
        for i, sample in enumerate(samples):
            pairs.append(("sample %d latency (ms)" % (i + 1), sample))
        pairs.append(("alert threshold (ms)", threshold))
        ops = ["max", "min", "count", "sum", "range"]
        if sum(samples) % n == 0:
            ops.append("mean")
        op = rng.choice(ops)
        if op == "max":
            gold, question = max(samples), "What is the highest latency sample (ms) for " + service + "?"
            required = ["F%d" % (i + 1) for i in range(n)]
        elif op == "min":
            gold, question = min(samples), "What is the lowest latency sample (ms) for " + service + "?"
            required = ["F%d" % (i + 1) for i in range(n)]
        elif op == "count":
            gold = sum(1 for s in samples if s > threshold)
            question = "How many latency samples for " + service + " exceed the alert threshold?"
            required = ["F%d" % (i + 1) for i in range(n + 1)]
        elif op == "sum":
            gold, question = sum(samples), "What is the total of the latency samples (ms) for " + service + "?"
            required = ["F%d" % (i + 1) for i in range(n)]
        elif op == "mean":
            gold, question = sum(samples) // n, "What is the mean latency (ms) across the samples for " + service + "?"
            required = ["F%d" % (i + 1) for i in range(n)]
        else:
            gold, question = max(samples) - min(samples), "What is the difference between the highest and the lowest latency sample (ms) for " + service + "?"
            required = ["F%d" % (i + 1) for i in range(n)]
        two_step = op in ("count", "range", "mean")
        title = "latency samples for service " + service
        number_count = n + 1
    elif config == "budget":
        project = rng.choice(("occ-2 prototype", "fidelity extension", "dialect trial", "gate migration"))
        total = rng.randint(50, 400) * 10
        n = rng.randint(3, 6)
        spends = sorted(rng.randint(1, total // 4) // 10 * 10 for _ in range(n))
        spends = [s if s >= 10 else 10 for s in spends]
        pairs.append(("total budget (USD)", total))
        for i, spend in enumerate(spends):
            pairs.append(("recorded spend %d (USD)" % (i + 1), spend))
        op = rng.choice(("remaining", "spent", "largest"))
        if op == "remaining":
            gold = total - sum(spends)
            question = "How much of the budget for the " + project + " remains after the recorded spends (USD)?"
            required = ["F%d" % (i + 1) for i in range(n + 1)]
        elif op == "spent":
            gold = sum(spends)
            question = "What is the total amount spent so far on the " + project + " (USD)?"
            required = ["F%d" % (i + 2) for i in range(n)]
        else:
            gold = max(spends)
            question = "What is the largest single spend on the " + project + " (USD)?"
            required = ["F%d" % (i + 2) for i in range(n)]
        two_step = op == "remaining"
        title = "budget bookkeeping for the " + project
        number_count = n + 1
    elif config == "capacity":
        nodes = rng.randint(2, 40)
        cores = rng.choice((4, 8, 16, 32, 48, 64))
        pods = rng.randint(4, 60)
        pairs.append(("node count", nodes))
        pairs.append(("cores per node", cores))
        pairs.append(("pods per node", pods))
        if rng.random() < 0.5:
            gold = nodes * cores
            question = "How many CPU cores does the cluster have in total?"
            required = ["F1", "F2"]
        else:
            gold = nodes * pods
            question = "How many pods can the cluster run in total?"
            required = ["F1", "F3"]
        two_step = False
        title = "cluster capacity for pool " + rng.choice(SERVICE_HEADS)
        number_count = 3
    elif config == "queue":
        queue = rng.choice(("task-ingest", "result-reports", "evidence-batch", "receipts"))
        n = rng.randint(4, 8)
        arrivals = [rng.randint(20, 400) for _ in range(n)]
        served = [a - rng.randint(0, min(a, 30)) for a in arrivals]
        for i in range(n):
            pairs.append(("interval %d arrivals" % (i + 1), arrivals[i]))
            pairs.append(("interval %d served" % (i + 1), served[i]))
        op = rng.choice(("arrivals", "served", "backlog"))
        if op == "arrivals":
            gold = sum(arrivals)
            question = "What is the total number of arrivals on the " + queue + " queue across the intervals?"
            required = ["F%d" % (2 * i + 1) for i in range(n)]
        elif op == "served":
            gold = sum(served)
            question = "What is the total number of served requests on the " + queue + " queue across the intervals?"
            required = ["F%d" % (2 * i + 2) for i in range(n)]
        else:
            gold = sum(a - s for a, s in zip(arrivals, served))
            question = "What is the accumulated backlog (arrivals minus served) on the " + queue + " queue?"
            required = ["F%d" % (i + 1) for i in range(2 * n)]
        two_step = op == "backlog"
        title = "queue counters for " + queue
        number_count = 2 * n
    else:
        suite = rng.choice(("codec", "fidelity", "adversarial", "regression"))
        n = rng.randint(4, 9)
        durations = [rng.randint(5, 300) for _ in range(n)]
        threshold = rng.randint(60, 280)
        for i, duration in enumerate(durations):
            pairs.append(("test %d duration (s)" % (i + 1), duration))
        pairs.append(("timeout budget (s)", threshold))
        op = rng.choice(("total", "longest", "count", "range"))
        if op == "total":
            gold = sum(durations)
            question = "What is the total wall time (s) of the " + suite + " test run?"
            required = ["F%d" % (i + 1) for i in range(n)]
        elif op == "longest":
            gold = max(durations)
            question = "What is the duration (s) of the longest test in the " + suite + " run?"
            required = ["F%d" % (i + 1) for i in range(n)]
        elif op == "count":
            gold = sum(1 for d in durations if d > threshold)
            question = "How many tests in the " + suite + " run exceed the timeout budget?"
            required = ["F%d" % (i + 1) for i in range(n + 1)]
        else:
            gold = max(durations) - min(durations)
            question = "What is the spread (s) between the longest and the shortest test in the " + suite + " run?"
            required = ["F%d" % (i + 1) for i in range(n)]
        two_step = op in ("count", "range")
        title = "test durations for the " + suite + " suite"
        number_count = n + 1

    fact_ids = ["F%d" % (i + 1) for i in range(len(pairs))]
    sender_input = _facts_block(title, list(zip(fact_ids, pairs)))
    score = number_count + (1 if two_step else 0)
    return {
        "task_id": "E-%s-%03d" % (split, index),
        "family": "E",
        "sender_input": sender_input,
        "receiver_task": "Using only the numbers the sender communicates, " + question[0].lower() + question[1:],
        "gold_answer": gold,
        "required_facts": required,
        "difficulty": difficulty_from_score(score),
    }


# ---------------------------------------------------------------- builder

GENERATORS = {
    "A": gen_task_a,
    "B": gen_task_b,
    "C": gen_task_c,
    "D": gen_task_d,
    "E": gen_task_e,
}


def build_split(family, split, forbidden):
    """Generate one split for one family, deduplicating receiver tasks."""
    rng = make_rng(family, split)
    tasks = []
    used = set()
    target = SPLIT_SIZES[split]
    attempts = 0
    while len(tasks) < target:
        attempts += 1
        if attempts > target * 200:
            raise RuntimeError("family %s split %s: could not reach %d unique tasks" % (family, split, target))
        task = GENERATORS[family](rng, len(tasks) + 1, split)
        signature = task["receiver_task"]
        if signature in used or signature in forbidden:
            continue
        used.add(signature)
        tasks.append(task)
    return tasks


def build_suite():
    """Build both splits for all families with dev/holdout disjointness."""
    suites = {}
    for family in FAMILIES:
        dev = build_split(family, "dev", frozenset())
        holdout = build_split(family, "holdout", frozenset(t["receiver_task"] for t in dev))
        suites[family] = {"dev": dev, "holdout": holdout}
    return suites


def canonical_sha256(tasks):
    payload = json.dumps(tasks, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_self_test(suites):
    """Run every checker against every gold answer plus negative controls."""
    failures = []
    total = 0
    for family in FAMILIES:
        for split in SPLITS:
            for task in suites[family][split]:
                total += 1
                if not checkers.check_answer(task, task["gold_answer"]):
                    failures.append((task["task_id"], "gold answer rejected by its own checker"))
                try:
                    derived = SOLVERS[family](task)
                except Exception as error:
                    failures.append((task["task_id"], "solver failed: %s" % error))
                    continue
                if derived != task["gold_answer"]:
                    failures.append(
                        (
                            task["task_id"],
                            "solver derived %r but gold is %r" % (derived, task["gold_answer"]),
                        )
                    )
    # Negative controls: clearly wrong answers must be rejected.
    def first(family, split="dev"):
        return suites[family][split][0]

    neg = []
    sample = first("A")
    neg.append((sample, sample["gold_answer"] + " x", "A wrong value must fail"))
    neg.append((first("B"), "not-a-state", "B wrong state must fail"))
    neg.append((first("C"), "0", "C wrong step must fail"))
    d_sample = first("D")
    if d_sample["gold_answer"] == "none":
        neg.append((d_sample, "bogus-service", "D invented entity must fail"))
    else:
        neg.append((d_sample, d_sample["gold_answer"] + ", bogus-service", "D extra entity must fail"))
    neg.append((first("E"), "not a number", "E non-numeric must fail"))
    e_sample = first("E")
    if isinstance(e_sample["gold_answer"], int):
        neg.append((e_sample, e_sample["gold_answer"] + 1, "E off-by-one must fail"))
    for task, wrong, why in neg:
        total += 1
        if checkers.check_answer(task, wrong):
            failures.append((task["task_id"], "negative control passed but should fail: " + why))
    return total, failures


def write_split(split, tasks_by_family):
    tasks = []
    for family in FAMILIES:
        tasks.extend(tasks_by_family[family])
    difficulty_counts = {}
    for task in tasks:
        difficulty_counts[task["difficulty"]] = difficulty_counts.get(task["difficulty"], 0) + 1
    meta = {
        "generator": "research/tasks/gen_tasks.py",
        "seed_base": SEED_BASE,
        "split": split,
        "families": list(FAMILIES),
        "family_names": dict(FAMILY_NAMES),
        "count": len(tasks),
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
        "tasks_sha256": canonical_sha256(tasks),
    }
    path = os.path.join(_HERE, "tasks_%s.json" % split)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"meta": meta, "tasks": tasks}, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path, meta


def main():
    suites = build_suite()
    total, failures = run_self_test(suites)
    if failures:
        for task_id, why in failures:
            sys.stderr.write("SELF-TEST FAILURE %s: %s\n" % (task_id, why))
        sys.stderr.write("self-test: %d/%d checks failed\n" % (len(failures), total))
        return 1
    # Determinism control: a second build must be identical.
    if build_suite() != suites:
        sys.stderr.write("SELF-TEST FAILURE: generator is not deterministic across runs\n")
        return 1

    for split in SPLITS:
        tasks_by_family = {family: suites[family][split] for family in FAMILIES}
        path, meta = write_split(split, tasks_by_family)
        counts = ", ".join("%s=%d" % (f, len(suites[f][split])) for f in FAMILIES)
        print("wrote %s (%d tasks: %s; sha256 %s)" % (path, meta["count"], counts, meta["tasks_sha256"][:12]))
    print("self-test: %d/%d checks passed (all gold answers accepted, negatives rejected, determinism verified)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
