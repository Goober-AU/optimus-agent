
"""Receiver continuous-prefix placement: marker resolution and span arithmetic.

The receiver's ordinary prompt is never rewritten. A marker line in the
rendered prompt tells the adapter where the sender's continuous message
belongs; the worker reserves that many positions and overwrites exactly
those rows with the authorised native prefix.

An absent marker keeps the legacy layout (prefix prepended at absolute row 0)
so an existing client keeps working and the receipt says which layout ran.
Everything here is integer arithmetic over token counts; no prompt text is
retained and no token id is logged.
"""
from __future__ import annotations

from dataclasses import dataclass

from .latent_types import require

#: Ordinary ASCII marker placed by the receiver prompt. Not a model token.
PREFIX_MARKER = "<<occ3-statebegin>>"

LAYOUT_MARKER = "marker"
LAYOUT_PREPEND = "prepend"


@dataclass(frozen=True)
class PrefixLayout:
    """Where the continuous prefix lives in the receiver sequence."""

    layout: str
    offset: int

    def __post_init__(self):
        require(self.layout in (LAYOUT_MARKER, LAYOUT_PREPEND), "profile")
        require(type(self.offset) is int and 0 <= self.offset <= 2**20, "profile")
        require((self.layout == LAYOUT_MARKER) == (self.offset > 0), "profile")


def _encoder(tokenizer, add_special_tokens):
    def encode(text):
        ids = tokenizer.encode(text, add_special_tokens=add_special_tokens)
        ids = list(ids) if isinstance(ids, (list, tuple)) else list(ids.tolist())
        require(all(type(value) is int for value in ids), "profile")
        return ids
    return encode


def _find_last(haystack, needle):
    """Last exact token-id occurrence, or -1. Token matching, never text."""
    if not needle or len(needle) > len(haystack):
        return -1
    for start in range(len(haystack) - len(needle), -1, -1):
        if haystack[start:start + len(needle)] == needle:
            return start
    return -1


def resolve_prefix_layout(text, tokenizer, *, marker=PREFIX_MARKER,
                          add_special_tokens=False):
    """Resolve the insertion point of the continuous prefix in ``text``.

    The whole rendered prompt and its strict prefix must agree on the token
    ids before the marker; otherwise the tokenizer is not prefix-stable here
    and the offset cannot be attested. Refuse rather than guess.
    """
    require(type(text) is str and text, "profile")
    if marker not in text:
        return PrefixLayout(LAYOUT_PREPEND, 0)
    require(text.count(marker) == 1, "profile")
    encode = _encoder(tokenizer, add_special_tokens)
    ids, mark = encode(text), encode(marker)
    require(bool(mark), "profile")
    start = _find_last(ids, mark)
    require(start > 0, "profile")
    offset = start + len(mark)
    require(offset < len(ids), "profile")
    cut = text.index(marker) + len(marker)
    head = encode(text[:cut])
    require(len(head) == offset and ids[:offset] == head, "profile")
    return PrefixLayout(LAYOUT_MARKER, offset)


def prefix_span(tokens, offset, prefix_len, extend_len, shift=0):
    """Slice of one extend chunk covered by the continuous prefix.

    Returns ``(local_start, count, prefix_start)``: receiver rows
    ``local_start .. local_start+count-1`` of this chunk take prefix rows
    ``prefix_start .. prefix_start+count-1``. ``shift`` rotates the window by
    one row for the draft batch that replays the last accepted token.
    """
    for value in (tokens, offset, prefix_len, extend_len, shift):
        require(type(value) is int and value >= 0, "profile")
    require(tokens >= 1, "profile")
    chunk_start = prefix_len + shift
    low = max(chunk_start, offset)
    high = min(chunk_start + extend_len, offset + tokens)
    if high <= low:
        return 0, 0, 0
    return low - chunk_start, high - low, low - offset
