"""Objective question generation from contract payloads (comprehension guard).

Per RESEARCH-NOTES S4.3.4: a listener must answer k objective questions about
the decoded content; this guards against syntactically clean but semantically
scrambled decodes. Questions are generated deterministically from the typed
payload and scored with the campaign checkers
(research/tasks/checkers.py): A/B/C exact text, D set, E number.

Each question dict: {"id", "family", "question", "gold_answer"}.
"""

import re

_WS = re.compile(r"\s+")


def _clean(text):
    return _WS.sub(" ", str(text)).strip()


def questions_for(item):
    """Deterministic question set for one corpus item (2-4 questions)."""
    message = item["message"]
    payload = message.to_payload()
    out = []
    out.append({
        "id": item["id"] + "-q1", "family": "A",
        "question": "What is the status of task %s?" % message.task_id,
        "gold_answer": message.status,
    })
    if message.findings:
        first = message.findings[0]
        out.append({
            "id": item["id"] + "-q2", "family": "A",
            "question": "Quote the first finding statement exactly.",
            "gold_answer": _clean(first.statement),
        })
        if len(message.findings) > 1:
            names = ",".join(_clean(f.statement) for f in message.findings)
            out.append({
                "id": item["id"] + "-q3", "family": "D",
                "question": "List all finding statements, comma separated.",
                "gold_answer": names,
            })
    if message.next_actions:
        out.append({
            "id": item["id"] + "-q4", "family": "A",
            "question": "What is the first next action, quoted exactly?",
            "gold_answer": _clean(message.next_actions[0]),
        })
    if message.evidence:
        locators = ",".join(e.locator for e in message.evidence)
        out.append({
            "id": item["id"] + "-q5", "family": "D",
            "question": "List the locator of every evidence reference, comma separated.",
            "gold_answer": locators,
        })
    return out


def answer_prompt(card, wire, questions):
    """Listener prompt: reconstruct answers from one dialect message."""
    rows = [card.card_text(), "", "MESSAGE:", wire, "", "QUESTIONS:"]
    for index, question in enumerate(questions, start=1):
        rows.append("%d. %s" % (index, question["question"]))
    rows.append("")
    rows.append("Answer each question on its own line as 'N. <answer>'. "
                "Quote values exactly as they appear. No other text.")
    return "\n".join(rows)


def parse_answers(text, count):
    """Parses 'N. answer' lines; missing lines become None."""
    answers = {}
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^(\d+)[.):]\s*(.+)$", line)
        if match:
            answers[int(match.group(1))] = _clean(match.group(2))
    return [answers.get(index) for index in range(1, count + 1)]


def load_checkers():
    import pathlib
    ns = {}
    exec(pathlib.Path(__file__).with_name("..", "..", "tasks", "checkers.py")
         .resolve().read_text(encoding="utf-8"), ns)
    return ns["check_answer"]
