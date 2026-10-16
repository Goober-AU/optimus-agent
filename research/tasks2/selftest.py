#!/usr/bin/env python3
"""Deterministic self-test for the occ2opt expanded message corpus (tasks2).

Validates the generated split FILES (not in-memory build output):
  1. presence, JSON parse, meta consistency
  2. schema + contract invariants per message (v1 rules + v2 keys)
  3. typed round trip through the kernel OCC-1 codec for every message
  4. split disjointness: entry ids, message ids, stories; designed-repeat policy
  5. QA gold answers pass their family checker; negative controls rejected
  6. scenario coverage: all 20 mission types in every split; manifest realization
  7. manifest consistency: roster vs generator vs files; split targets
  8. determinism: rebuild via the generator equals the file content
  9. receipts: file hashes recorded after a green run (receipts.json)

Exit 0 with "self-test: N/N checks passed" on success; non-zero otherwise.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in ("research/tasks", "research/symbolic_comm", "research/tasks2"):
    _full = os.path.join(_ROOT, _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

import checkers
import contract as SYM
import gen_corpus2 as G

SPLITS = ("evolve", "dev", "holdout")
REQUIRED_KEYS = {"id", "split", "story", "scenarios", "holdout", "plain_english", "payload", "qa"}


class Ledger:
    def __init__(self):
        self.total = 0
        self.failures = []

    def check(self, ok, label):
        self.total += 1
        if not ok:
            self.failures.append(label)
        return ok


def content_digest(payload):
    body = {k: v for k, v in payload.items() if k not in ("message_id", "sequence", "timestamp")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def main():
    led = Ledger()
    manifest = json.loads(open(os.path.join(_HERE, "manifest.json"), encoding="utf-8").read())
    frozen = manifest["frozen_config"]
    targets = frozen["split_targets"]
    scenario_types = set(frozen["scenario_types"])

    docs = {}
    for split in SPLITS:
        path = os.path.join(_HERE, "msgs_corpus2_%s.json" % split)
        led.check(os.path.isfile(path), "missing split file %s" % split)
        doc = json.loads(open(path, encoding="utf-8").read())
        docs[split] = doc
        led.check(doc["meta"]["split"] == split, "meta split mismatch %s" % split)
        led.check(doc["meta"]["count"] == len(doc["messages"]), "meta count mismatch %s" % split)
        led.check(doc["meta"]["count"] == targets[split],
                  "split %s count %d != manifest target %d" % (split, len(doc["messages"]), targets[split]))
        led.check(doc["meta"]["seed_base"] == (G.SEED_BASE_HO if split == "holdout" else G.SEED_BASE_ED),
                  "seed base mismatch %s" % split)

    # manifest roster vs generator lists
    roster = {row["id"]: row for row in manifest["stories"]}
    gen_lists = {"evolve": G.STORIES_EVOLVE, "dev": G.STORIES_DEV, "holdout": G.STORIES_HOLDOUT}
    for split, stories in gen_lists.items():
        for st in stories:
            row = roster.get(st["task_id"])
            led.check(row is not None, "story %s not in manifest" % st["task_id"])
            if row is None:
                continue
            led.check(row["split"] == split, "story %s split mismatch" % st["task_id"])
            led.check(row["messages"] == len(st["chain"]),
                      "story %s chain %d != manifest %d" % (st["task_id"], len(st["chain"]), row["messages"]))

    # per-message checks
    seen_entry_ids = set()
    seen_message_ids = set()
    story_split = {}
    digest_stories = {}
    qa_negative_pool = []
    for split in SPLITS:
        for record in docs[split]["messages"]:
            rid = record["id"]
            led.check(REQUIRED_KEYS <= set(record), "%s: missing keys %s" % (rid, REQUIRED_KEYS - set(record)))
            led.check(G._ID_RE.match(rid) is not None, "%s: bad id format" % rid)
            led.check(rid not in seen_entry_ids, "%s: duplicate entry id" % rid)
            seen_entry_ids.add(rid)
            led.check(record["split"] == split, "%s: split key mismatch" % rid)
            led.check(record["holdout"] == (split == "holdout"), "%s: holdout flag mismatch" % rid)
            sid = record["story"]
            led.check(sid in roster, "%s: unknown story %s" % (rid, sid))
            led.check(story_split.setdefault(sid, split) == split, "%s: story %s appears in two splits" % (rid, sid))
            unknown = set(record["scenarios"]) - scenario_types
            led.check(not unknown, "%s: unknown scenarios %s" % (rid, sorted(unknown)))
            payload = record["payload"]
            led.check(payload["message_id"] not in seen_message_ids, "%s: duplicate message id" % rid)
            seen_message_ids.add(payload["message_id"])

            # contract invariants (generator validator, applied to the file content)
            problems = G.validate_entry(record, set(), split)
            led.check(not problems, "%s: invariants %s" % (rid, problems))

            # typed round trip through the kernel codec
            try:
                message = SYM.AgentCommMessage.from_payload(payload)
                wire = message.to_occ1().encode()
                back = SYM.AgentCommMessage.from_occ1(SYM.Occ1Message.decode(wire))
                led.check(SYM.equal(message, back), "%s: round trip not equal" % rid)
            except Exception as error:  # noqa: BLE001
                led.check(False, "%s: round trip raised %s" % (rid, error))

            # designed-repeat policy: identical content digests only within one story
            digest = content_digest(payload)
            if digest in digest_stories and digest_stories[digest] != sid:
                led.check(False, "%s: content digest shared across stories %s and %s"
                          % (rid, digest_stories[digest], sid))
            else:
                digest_stories.setdefault(digest, sid)

            # QA golds
            qa = record["qa"]
            led.check(len(qa) >= 2, "%s: fewer than 2 qa items" % rid)
            for item in qa:
                led.check(item["family"] in ("A", "D", "E"), "%s: bad qa family" % rid)
                led.check(rid in item["q"], "%s: qa question does not name the id" % rid)
                led.check(bool(item["a"]), "%s: empty qa answer" % rid)
                led.check(checkers.check_answer({"family": item["family"], "gold_answer": item["a"]}, item["a"]),
                          "%s: qa gold rejected by checker" % rid)
            if qa:
                qa_negative_pool.append((record, qa))

    # scenario coverage per split + manifest story-tag realization
    for split in SPLITS:
        covered = set()
        for record in docs[split]["messages"]:
            covered.update(record["scenarios"])
        missing = scenario_types - covered
        led.check(not missing, "split %s missing scenarios: %s" % (split, sorted(missing)))
        for st in gen_lists[split]:
            realized = set()
            for record in docs[split]["messages"]:
                if record["story"] == st["task_id"]:
                    realized.update(record["scenarios"])
            for tag in roster[st["task_id"]]["scenarios"]:
                led.check(tag in realized, "story %s tag %s not realized" % (st["task_id"], tag))

    # negative controls: wrong answers must be rejected
    def first_with(family):
        for record, qa in qa_negative_pool:
            for item in qa:
                if item["family"] == family:
                    return record, item
        return None, None

    rec, item = first_with("A")
    if rec is not None:
        led.check(not checkers.check_answer({"family": "A", "gold_answer": item["a"]}, item["a"] + " wrong"),
                  "negative control A must fail")
        led.check(not checkers.check_answer({"family": "A", "gold_answer": item["a"]}, rec["payload"]["kind"]
                                            if item["a"] != rec["payload"]["kind"] else "not-a-status"),
                  "negative control A2 must fail")
    rec, item = first_with("D")
    if rec is not None:
        if item["a"] and item["a"] != "none":
            led.check(not checkers.check_answer({"family": "D", "gold_answer": item["a"]}, item["a"] + ", bogus-thing"),
                      "negative control D extra entity must fail")
            led.check(not checkers.check_answer({"family": "D", "gold_answer": item["a"]}, "none"),
                      "negative control D none-for-nonempty must fail")
        else:
            led.check(not checkers.check_answer({"family": "D", "gold_answer": item["a"]}, "bogus-thing"),
                      "negative control D invented entity must fail")
    rec, item = first_with("E")
    if rec is not None:
        led.check(not checkers.check_answer({"family": "E", "gold_answer": item["a"]}, "not a number"),
                  "negative control E non-numeric must fail")
        number = checkers.to_number(item["a"])
        if number is not None:
            led.check(not checkers.check_answer({"family": "E", "gold_answer": item["a"]}, str(number + 1)),
                      "negative control E off-by-one must fail")

    # determinism: rebuild equals file content
    for split in SPLITS:
        rebuilt = G.build_split(split)
        led.check(rebuilt == docs[split]["messages"], "split %s: rebuild differs from file" % split)

    # stats + designed repeats
    repeats = {}
    for split in SPLITS:
        for record in docs[split]["messages"]:
            d = content_digest(record["payload"])
            repeats[d] = repeats.get(d, 0) + 1
    designed = sum(1 for d, n in repeats.items() if n > 1)
    duplicate_count = sum(n - 1 for n in repeats.values() if n > 1)

    if led.failures:
        for failure in led.failures[:50]:
            sys.stderr.write("SELF-TEST FAILURE %s\n" % failure)
        sys.stderr.write("self-test: %d/%d checks failed\n" % (len(led.failures), led.total))
        return 1

    # receipts (written only after a green run)
    receipts = {
        "artifact": "occ2opt msgs_corpus2 generation receipt",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "manifest_sha256": hashlib.sha256(open(os.path.join(_HERE, "manifest.json"), "rb").read()).hexdigest(),
        "generator_sha256": hashlib.sha256(open(os.path.join(_HERE, "gen_corpus2.py"), "rb").read()).hexdigest(),
        "splits": {},
        "checks_total": led.total,
        "designed_repeat_digests": designed,
        "designed_repeat_extra_messages": duplicate_count,
        "notes": [
            "phases: evolvedev written, then holdout written last from its independent seed base",
            "pre-holdout authoring fixes (before any holdout generation): long_history tag rule for long-pace stories; chain-count trims in occ2b-d03, occ2b-h03, occ2b-h07 to match the frozen manifest targets",
            "evolve split byte-identical across regenerations; dev split regenerated once for the d03 trim before holdout generation",
            "nothing was adapted to holdout content after its generation",
        ],
    }
    for split in SPLITS:
        path = os.path.join(_HERE, "msgs_corpus2_%s.json" % split)
        doc = docs[split]
        stats = G.split_stats(doc["messages"])
        receipts["splits"][split] = {
            "path": "research/tasks2/msgs_corpus2_%s.json" % split,
            "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
            "messages": stats["count"],
            "qa_items": stats["qa_total"],
            "scenario_types_covered": len(stats["scenarios"]),
            "kinds": stats["kinds"],
            "statuses": stats["statuses"],
            "evidence_kinds": stats["evidence_kinds"],
            "verification_statuses": stats["verification_statuses"],
            "bytes": {"plain": stats["plain_bytes"], "json": stats["json_bytes"], "occ1": stats["occ1_bytes"]},
            "max_occ1_bytes": stats["max_occ1_bytes"],
        }
    with open(os.path.join(_HERE, "receipts.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(receipts, handle, indent=2, ensure_ascii=True)
        handle.write("\n")

    print("self-test: %d/%d checks passed" % (led.total, led.total))
    print("splits: evolve=%d dev=%d holdout=%d (total %d messages, %d qa items)"
          % (len(docs["evolve"]["messages"]), len(docs["dev"]["messages"]),
             len(docs["holdout"]["messages"]),
             sum(len(docs[s]["messages"]) for s in SPLITS),
             sum(len(r["qa"]) for s in SPLITS for r in docs[s]["messages"])))
    print("scenario types: 20/20 in every split; designed repeat digests: %d (%d extra messages)"
          % (designed, duplicate_count))
    print("receipts written: research/tasks2/receipts.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
