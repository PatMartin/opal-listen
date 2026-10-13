"""Training phrase template parsing.

Syntax (whitespace separated):
    word            literal word
    {slot_name}     slot placeholder
    [words ...]     optional group
    (a | b c)       alternatives
Example: "turn on [the] {room_name} (light | lights)"
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from opal_listener.service.nlu.normalizer import normalize_text

MAX_EXPANSIONS = 128


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class SlotRef:
    name: str

    def __str__(self) -> str:
        return "{" + self.name + "}"


Token = str | SlotRef
Variant = tuple[Token, ...]


def _lex(phrase: str) -> list[str]:
    tokens: list[str] = []
    buf = ""
    i = 0
    while i < len(phrase):
        ch = phrase[i]
        if ch == "{":
            end = phrase.find("}", i)
            if end == -1:
                raise TemplateError("unclosed '{'")
            if buf.strip():
                tokens.append(buf)
            buf = ""
            tokens.append(phrase[i : end + 1])
            i = end + 1
            continue
        if ch in "[]()|":
            if buf.strip():
                tokens.append(buf)
            buf = ""
            tokens.append(ch)
        elif ch.isspace():
            if buf.strip():
                tokens.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    if buf.strip():
        tokens.append(buf)
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def parse_sequence(self, stop: set[str]) -> list[list[Variant]]:
        """Return a list of 'slots', each a list of alternative variants, to be combined in order."""
        parts: list[list[Variant]] = []
        while self.pos < len(self.tokens) and self.tokens[self.pos] not in stop:
            tok = self.tokens[self.pos]
            self.pos += 1
            if tok == "[":
                inner = self._expand(self.parse_sequence({"]"}))
                self._expect("]")
                parts.append([(), *inner])
            elif tok == "(":
                options = self._expand(self.parse_sequence({"|", ")"}))
                while self._peek() == "|":
                    self.pos += 1
                    options.extend(self._expand(self.parse_sequence({"|", ")"})))
                self._expect(")")
                parts.append(options)
            elif tok in {"]", ")", "|"}:
                raise TemplateError(f"unexpected '{tok}'")
            elif tok.startswith("{"):
                name = tok[1:-1].strip()
                if not name:
                    raise TemplateError("empty slot placeholder '{}'")
                parts.append([(SlotRef(name),)])
            else:
                words = normalize_text(tok).split()
                if words:
                    parts.append([tuple(words)])
        return parts

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _expect(self, token: str) -> None:
        if self._peek() != token:
            raise TemplateError(f"expected '{token}'")
        self.pos += 1

    @staticmethod
    def _expand(parts: list[list[Variant]]) -> list[Variant]:
        variants: list[Variant] = [()]
        for options in parts:
            variants = [v + o for v, o in itertools.product(variants, options)]
            if len(variants) > MAX_EXPANSIONS:
                raise TemplateError(f"expands to more than {MAX_EXPANSIONS} variants")
        return variants


def expand_template(phrase: str) -> list[Variant]:
    """Expand a phrase template into concrete token variants (deduplicated, non-empty)."""
    parser = _Parser(_lex(phrase))
    parts = parser.parse_sequence(set())
    if parser.pos != len(parser.tokens):
        raise TemplateError(f"unexpected '{parser.tokens[parser.pos]}'")
    seen: dict[Variant, None] = {}
    for variant in _Parser._expand(parts):
        if variant:
            seen.setdefault(variant, None)
    if not seen:
        raise TemplateError("phrase is empty")
    return list(seen)


def slot_names(phrase: str) -> set[str]:
    return {t.name for v in expand_template(phrase) for t in v if isinstance(t, SlotRef)}


def render_variant(variant: Variant) -> str:
    return " ".join(str(t) for t in variant)
