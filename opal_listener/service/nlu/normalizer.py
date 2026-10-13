"""Transcript normalization and wakeword prefix handling."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

_NON_WORD = re.compile(r"[^\w\s']+")
_SPACES = re.compile(r"\s+")
UNKNOWN_TOKENS = frozenset({"[unk]", "unk", "<unk>"})


def normalize_text(text: str, filler_words: Iterable[str] = ()) -> str:
    """Lowercase, strip punctuation, drop unknown/filler tokens and collapse whitespace."""
    lowered = text.lower().replace("[unk]", " ").replace("-", " ").replace("_", " ")
    cleaned = _NON_WORD.sub(" ", lowered)
    fillers = set(filler_words)
    tokens = [t.strip("'") for t in _SPACES.split(cleaned)]
    return " ".join(t for t in tokens if t and t not in fillers and t not in UNKNOWN_TOKENS)


def tokenize(text: str) -> list[str]:
    return text.split()


def split_wakeword(normalized: str, wakewords: Sequence[str]) -> tuple[str | None, str]:
    """Find the first wakeword occurrence and return (wakeword, text after it).

    Anything before the wakeword is dropped (pre-roll audio may contain noise or partial words).
    Longer wakewords win over shorter ones that share a prefix.
    """
    tokens = normalized.split()
    ordered = sorted(((w, normalize_text(w).split()) for w in wakewords), key=lambda item: len(item[1]), reverse=True)
    for start in range(len(tokens)):
        for name, wtoks in ordered:
            if wtoks and tokens[start : start + len(wtoks)] == wtoks:
                return name, " ".join(tokens[start + len(wtoks) :])
    return None, normalized


def parse_csv(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]
