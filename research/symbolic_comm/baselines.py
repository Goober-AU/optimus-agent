"""OCC-0 / OCC-1 / JSON baseline encodings for comparison.

- OCC-0: the corpus plain_english rendering (human-readable prose).
- OCC-1: the standard encoding via the repo codec (contract -> OCC-1 wire).
- JSON: the typed payload serialized compactly.
"""

import json

from contract import AgentCommMessage, repo_occ1


def occ0_text(item):
    return item["occ0"]


def occ1_text(message):
    return message.to_occ1().encode()


def json_text(message):
    return json.dumps(message.to_payload(), separators=(",", ":"))


def all_baselines(item):
    message = item["message"]
    return {
        "OCC-0": occ0_text(item),
        "OCC-1": occ1_text(message),
        "JSON": json_text(message),
    }
