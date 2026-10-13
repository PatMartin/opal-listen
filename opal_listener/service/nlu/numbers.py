"""Spoken number parsing ("twenty one" -> 21, "5" -> 5)."""

from __future__ import annotations

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1000}

NUMBER_WORDS = frozenset(_UNITS) | frozenset(_TENS) | frozenset(_SCALES)


def is_number_token(token: str) -> bool:
    return token.isdigit() or token in NUMBER_WORDS


def parse_number(tokens: list[str]) -> int | None:
    """Parse a run of number tokens. Returns None if the run is not a valid number."""
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0].isdigit():
        return int(tokens[0])
    total = 0
    current = 0
    for token in tokens:
        if token in _UNITS:
            current += _UNITS[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token == "hundred":
            current = max(current, 1) * 100
        elif token == "thousand":
            total += max(current, 1) * 1000
            current = 0
        elif token.isdigit():
            current += int(token)
        else:
            return None
    return total + current


def number_vocabulary() -> list[str]:
    return sorted(NUMBER_WORDS - {"oh"})
