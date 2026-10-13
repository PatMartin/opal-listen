"""Intent recognition and slot extraction over normalized transcripts.

Matching works per template variant:
1. Delexicalize: find slot values/aliases (exact, then fuzzy) and numbers in the transcript and
   replace them with placeholders.
2. Align the delexicalized tokens with the template tokens (weighted LCS with fuzzy token equality)
   and score with a weighted Jaccard: matched / (template_weight + transcript_weight - matched).
3. intent_confidence = alignment score * mean slot score. Best candidate wins; per-intent threshold
   (floored by the profile threshold) decides matched vs no_match.
Templates containing free-text slots are matched with an anchored regex instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from opal_listener.models.schemas import NO_MATCH, SLOT_TYPES
from opal_listener.service.nlu.normalizer import normalize_text
from opal_listener.service.nlu.numbers import is_number_token, number_vocabulary, parse_number
from opal_listener.service.nlu.templates import SlotRef, TemplateError, Variant, expand_template

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
STOPWORDS = frozenset({"the", "a", "an", "my", "your", "some", "of"})
STOPWORD_WEIGHT = 0.4
LITERAL_WEIGHT = 1.0
SLOT_WEIGHT = 1.5
TEXT_SLOT_SCORE = 0.95


@dataclass(frozen=True)
class IntentDef:
    name: str
    phrases: Sequence[str]
    confidence_threshold: float = 0.7
    enabled: bool = True


@dataclass(frozen=True)
class SlotDef:
    name: str
    slot_type: str = "list"
    values: Sequence[tuple[str, Sequence[str]]] = ()  # (canonical value, aliases)


@dataclass(frozen=True)
class EngineOptions:
    intent_threshold_floor: float = 0.0
    slot_fuzzy_min_score: float = 0.85
    token_fuzzy_min_score: float = 0.8
    max_alternatives: int = 3


@dataclass(frozen=True)
class _Template:
    intent_name: str
    phrase: str
    tokens: Variant
    slot_names: tuple[str, ...]
    regex: re.Pattern[str] | None  # only for templates with text slots


@dataclass
class _Candidate:
    intent_name: str
    phrase: str
    score: float
    slot_values: dict[str, Any]
    missing_slots: bool


@dataclass(frozen=True)
class Alternative:
    intent_name: str
    confidence: float
    phrase: str


@dataclass
class MatchResult:
    intent_name: str
    matched: bool
    confidence: float
    slot_values: dict[str, Any] = field(default_factory=dict)
    matched_phrase: str | None = None
    no_match_reason: str | None = None
    alternatives: list[Alternative] = field(default_factory=list)


@dataclass
class CompileReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class _CompiledSlot:
    name: str
    slot_type: str
    surfaces: tuple[tuple[tuple[str, ...], str], ...]  # (surface tokens, canonical), longest first


class IntentCompileError(ValueError):
    def __init__(self, report: CompileReport) -> None:
        super().__init__("; ".join(report.errors))
        self.report = report


def _weight(token: str | SlotRef) -> float:
    if isinstance(token, SlotRef):
        return SLOT_WEIGHT
    return STOPWORD_WEIGHT if token in STOPWORDS else LITERAL_WEIGHT


def compile_slots(slots: Iterable[SlotDef], report: CompileReport) -> dict[str, _CompiledSlot]:
    compiled: dict[str, _CompiledSlot] = {}
    for slot in slots:
        where = f"slot '{slot.name}'"
        if not NAME_PATTERN.match(slot.name):
            report.errors.append(f"{where}: name must be snake_case")
            continue
        if slot.slot_type not in SLOT_TYPES:
            report.errors.append(f"{where}: slot_type must be one of {', '.join(SLOT_TYPES)}")
            continue
        surface_map: dict[tuple[str, ...], str] = {}
        for value, aliases in slot.values:
            for surface in (value, *aliases):
                tokens = tuple(normalize_text(surface).split())
                if not tokens:
                    report.errors.append(f"{where}: value/alias '{surface}' is empty after normalization")
                    continue
                previous = surface_map.get(tokens)
                if previous is not None and previous != value:
                    report.errors.append(f"{where}: '{surface}' maps to both '{previous}' and '{value}'")
                    continue
                surface_map[tokens] = value
        if slot.slot_type == "list" and not surface_map:
            report.warnings.append(f"{where}: list slot has no values")
        surfaces = tuple(sorted(surface_map.items(), key=lambda kv: len(kv[0]), reverse=True))
        compiled[slot.name] = _CompiledSlot(slot.name, slot.slot_type, surfaces)
    return compiled


def _regex_for(variant: Variant, slots: dict[str, _CompiledSlot]) -> re.Pattern[str]:
    parts: list[str] = []
    for token in variant:
        if isinstance(token, SlotRef):
            slot = slots[token.name]
            if slot.slot_type == "text":
                parts.append(f"(?P<{token.name}>.+?)")
            elif slot.slot_type == "number":
                nums = "|".join(sorted(number_vocabulary(), key=len, reverse=True))
                parts.append(rf"(?P<{token.name}>(?:\d+|{nums})(?:\s+(?:\d+|{nums}))*)")
            else:
                alts = "|".join(re.escape(" ".join(s)) for s, _ in slot.surfaces)
                parts.append(f"(?P<{token.name}>{alts})")
        else:
            parts.append(re.escape(token))
    return re.compile(r"^" + r"\s+".join(parts) + r"$")


class IntentEngine:
    def __init__(
        self,
        templates: list[_Template],
        slots: dict[str, _CompiledSlot],
        thresholds: dict[str, float],
        options: EngineOptions,
    ) -> None:
        self._templates = templates
        self._slots = slots
        self._thresholds = thresholds
        self.options = options

    # --- construction -------------------------------------------------------------------------

    @classmethod
    def compile(
        cls,
        intents: Iterable[IntentDef],
        slots: Iterable[SlotDef],
        options: EngineOptions | None = None,
    ) -> tuple[IntentEngine, CompileReport]:
        report = CompileReport()
        compiled_slots = compile_slots(slots, report)
        templates: list[_Template] = []
        thresholds: dict[str, float] = {}
        seen_variants: dict[Variant, str] = {}
        for intent in intents:
            where = f"intent '{intent.name}'"
            if not NAME_PATTERN.match(intent.name):
                report.errors.append(f"{where}: name must be snake_case")
                continue
            if intent.name == NO_MATCH:
                report.errors.append(f"{where}: '{NO_MATCH}' is reserved")
                continue
            if not intent.enabled:
                continue
            if not intent.phrases:
                report.warnings.append(f"{where}: has no training phrases")
            thresholds[intent.name] = intent.confidence_threshold
            for phrase in intent.phrases:
                try:
                    variants = expand_template(phrase)
                except TemplateError as exc:
                    report.errors.append(f"{where} phrase '{phrase}': {exc}")
                    continue
                for variant in variants:
                    names = tuple(t.name for t in variant if isinstance(t, SlotRef))
                    problem = cls._check_variant(names, compiled_slots)
                    if problem:
                        report.errors.append(f"{where} phrase '{phrase}': {problem}")
                        break
                    owner = seen_variants.setdefault(variant, intent.name)
                    if owner != intent.name:
                        report.warnings.append(f"{where} phrase '{phrase}' is ambiguous with intent '{owner}'")
                    has_text = any(compiled_slots[n].slot_type == "text" for n in names)
                    regex = _regex_for(variant, compiled_slots) if has_text else None
                    templates.append(_Template(intent.name, phrase, variant, names, regex))
        return cls(templates, compiled_slots, thresholds, options or EngineOptions()), report

    @staticmethod
    def _check_variant(names: tuple[str, ...], slots: dict[str, _CompiledSlot]) -> str | None:
        if len(set(names)) != len(names):
            return "a slot may appear only once per phrase"
        for name in names:
            slot = slots.get(name)
            if slot is None:
                return f"unknown slot '{name}'"
            if slot.slot_type == "list" and not slot.surfaces:
                return f"slot '{name}' has no values"
        return None

    # --- introspection ------------------------------------------------------------------------

    @property
    def intent_names(self) -> list[str]:
        return sorted(self._thresholds)

    @property
    def template_count(self) -> int:
        return len(self._templates)

    @property
    def requires_open_vocabulary(self) -> bool:
        return any(t.regex is not None for t in self._templates)

    def vocabulary(self) -> list[str]:
        """All individual words used by enabled phrases and the slots they reference."""
        return sorted({word for phrase in self.grammar_phrases() for word in phrase.split()})

    def grammar_phrases(self) -> list[str]:
        """Phrase fragments for grammar-constrained STT.

        Literal runs between placeholders stay together (e.g. "set a timer for"), which constrains the
        decoder far better than a flat word list, plus every slot surface form and number word.
        """
        phrases: set[str] = set()
        used_slots: set[str] = set()
        for template in self._templates:
            run: list[str] = []
            for token in template.tokens:
                if isinstance(token, SlotRef):
                    used_slots.add(token.name)
                    if run:
                        phrases.add(" ".join(run))
                    run = []
                else:
                    run.append(token)
            if run:
                phrases.add(" ".join(run))
        for name in used_slots:
            slot = self._slots[name]
            if slot.slot_type == "number":
                phrases.update(number_vocabulary())
            phrases.update(" ".join(surface) for surface, _ in slot.surfaces)
        return sorted(phrases)

    def slot_surfaces(self, slot_name: str) -> list[tuple[tuple[str, ...], str]]:
        slot = self._slots.get(slot_name)
        return list(slot.surfaces) if slot else []

    def slot_names_for_intent(self, intent_name: str) -> set[str]:
        return {n for t in self._templates if t.intent_name == intent_name for n in t.slot_names}

    # --- matching -----------------------------------------------------------------------------

    def match(self, normalized: str) -> MatchResult:
        tokens = normalized.split()
        if not tokens:
            return MatchResult(NO_MATCH, False, 0.0, no_match_reason="empty_transcript")
        best_per_intent: dict[str, _Candidate] = {}
        best_missing: _Candidate | None = None
        for template in self._templates:
            candidate = self._score_template(template, tokens, normalized)
            if candidate is None:
                continue
            if candidate.missing_slots:
                if best_missing is None or candidate.score > best_missing.score:
                    best_missing = candidate
                continue
            current = best_per_intent.get(candidate.intent_name)
            if current is None or candidate.score > current.score:
                best_per_intent[candidate.intent_name] = candidate

        ranked = sorted(best_per_intent.values(), key=lambda c: c.score, reverse=True)
        alternatives = [
            Alternative(c.intent_name, round(c.score, 4), c.phrase) for c in ranked[: self.options.max_alternatives]
        ]
        if not ranked:
            reason = "missing_slot" if best_missing and best_missing.score >= 0.5 else "no_candidate"
            return MatchResult(NO_MATCH, False, 0.0, no_match_reason=reason, alternatives=alternatives)

        best = ranked[0]
        threshold = max(self._thresholds.get(best.intent_name, 0.7), self.options.intent_threshold_floor)
        confidence = round(best.score, 4)
        if best.score < threshold:
            missing_wins = best_missing is not None and best_missing.score >= max(0.5, best.score)
            return MatchResult(
                NO_MATCH,
                False,
                confidence,
                no_match_reason="missing_slot" if missing_wins else "below_threshold",
                alternatives=alternatives,
            )
        return MatchResult(
            best.intent_name,
            True,
            confidence,
            slot_values=best.slot_values,
            matched_phrase=best.phrase,
            alternatives=alternatives,
        )

    def _score_template(self, template: _Template, tokens: list[str], normalized: str) -> _Candidate | None:
        if template.regex is not None:
            match = template.regex.match(normalized)
            if not match:
                return None
            values = {name: self._canonical(name, match.group(name)) for name in template.slot_names}
            if any(v is None for v in values.values()):
                return None
            return _Candidate(template.intent_name, template.phrase, TEXT_SLOT_SCORE, values, False)

        delex, values, slot_scores = self._delexicalize(tokens, template.slot_names)
        missing = len(values) != len(template.slot_names)
        score = _alignment_score(delex, template.tokens, self.options.token_fuzzy_min_score)
        if slot_scores:
            score *= sum(slot_scores) / len(slot_scores)
        return _Candidate(template.intent_name, template.phrase, score, values, missing)

    def _canonical(self, slot_name: str, surface: str) -> Any:
        slot = self._slots[slot_name]
        if slot.slot_type == "text":
            return surface
        if slot.slot_type == "number":
            return parse_number(surface.split())
        key = tuple(surface.split())
        for tokens, canonical in slot.surfaces:
            if tokens == key:
                return canonical
        return None

    def _delexicalize(
        self, tokens: list[str], names: tuple[str, ...]
    ) -> tuple[list[str | SlotRef], dict[str, Any], list[float]]:
        claimed: list[tuple[int, int, str]] = []  # (start, end, slot)
        values: dict[str, Any] = {}
        scores: list[float] = []

        def free(start: int, end: int) -> bool:
            return all(end <= s or start >= e for s, e, _ in claimed)

        for name in names:
            slot = self._slots[name]
            found = self._find_slot(slot, tokens, free)
            if found is None:
                continue
            start, end, value, score = found
            claimed.append((start, end, name))
            values[name] = value
            scores.append(score)

        delex: list[str | SlotRef] = []
        i = 0
        by_start = {s: (e, n) for s, e, n in claimed}
        while i < len(tokens):
            if i in by_start:
                end, name = by_start[i]
                delex.append(SlotRef(name))
                i = end
            else:
                delex.append(tokens[i])
                i += 1
        return delex, values, scores

    def _find_slot(self, slot: _CompiledSlot, tokens: list[str], free: Any) -> tuple[int, int, Any, float] | None:
        n = len(tokens)
        if slot.slot_type == "number":
            i = 0
            while i < n:
                if is_number_token(tokens[i]) and free(i, i + 1):
                    j = i
                    while j < n and is_number_token(tokens[j]) and free(j, j + 1):
                        j += 1
                    value = parse_number(tokens[i:j])
                    if value is not None:
                        return i, j, value, 1.0
                    i = j
                else:
                    i += 1
            return None
        if slot.slot_type != "list":
            return None
        for surface, canonical in slot.surfaces:
            k = len(surface)
            for i in range(n - k + 1):
                if tuple(tokens[i : i + k]) == surface and free(i, i + k):
                    return i, i + k, canonical, 1.0
        best: tuple[int, int, Any, float] | None = None
        for surface, canonical in slot.surfaces:
            k = len(surface)
            target = " ".join(surface)
            for i in range(n - k + 1):
                if not free(i, i + k):
                    continue
                score = fuzz.ratio(" ".join(tokens[i : i + k]), target) / 100.0
                if score >= self.options.slot_fuzzy_min_score and (best is None or score > best[3]):
                    best = (i, i + k, canonical, score)
        return best


def _token_similarity(a: str | SlotRef, b: str | SlotRef, fuzzy_min: float) -> float:
    if isinstance(a, SlotRef) or isinstance(b, SlotRef):
        return 1.0 if a == b else 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 4:
        return 0.0
    score = fuzz.ratio(a, b) / 100.0
    return score if score >= fuzzy_min else 0.0


def _alignment_score(transcript: Sequence[str | SlotRef], template: Sequence[str | SlotRef], fuzzy_min: float) -> float:
    n, m = len(transcript), len(template)
    if n == 0 or m == 0:
        return 0.0
    prev = [0.0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        for j in range(1, m + 1):
            sim = _token_similarity(transcript[i - 1], template[j - 1], fuzzy_min)
            diag = prev[j - 1] + sim * _weight(template[j - 1]) if sim else 0.0
            cur[j] = max(prev[j], cur[j - 1], diag)
        prev = cur
    matched = prev[m]
    total_template = sum(_weight(t) for t in template)
    total_transcript = sum(_weight(t) for t in transcript)
    denom = total_template + total_transcript - matched
    return matched / denom if denom > 0 else 0.0
