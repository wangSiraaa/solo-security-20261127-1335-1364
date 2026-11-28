"""Immutable puzzle-library model.

A library is pure data: fields of candidate answers (with alias groups that
collapse to the *same* answer), truthful statements the curator will issue, the
pages that carry those statements, and the public questions (menu) whose
*responses* may differ per truth even though their wording does not.

The model knows nothing about tickets, receipts or branches.  Both the service
(``desk``) and the independent auditor (``verifier``) rebuild everything from a
``Library`` plus a public record, which is what keeps the replay honest.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .util import digest

World = dict[str, str]  # field -> canonical answer


class ContentError(ValueError):
    """The library schema is malformed (not a runtime refusal)."""


# --------------------------------------------------------------------------- #
# fields, canonicalisation, aliases
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Field:
    name: str
    literals: tuple[str, ...]
    answers: tuple[str, ...]          # distinct canonical answers, in order
    canon: Mapping[str, str]          # literal -> canonical answer

    def c(self, literal: str) -> str:
        try:
            return self.canon[literal]
        except KeyError as exc:  # pragma: no cover - guarded by callers
            raise ContentError(f"{self.name}: unknown literal {literal!r}") from exc


def _build_field(name: str, spec: Mapping[str, Any]) -> Field:
    literals = tuple(spec["literals"])
    if len(set(literals)) != len(literals):
        raise ContentError(f"{name}: duplicate literal")
    canon = {lit: lit for lit in literals}
    seen_groups: set[str] = set()
    for group in spec.get("aliases", []):
        group = list(group)
        if not group:
            continue
        rep = group[0]
        if rep not in canon:
            raise ContentError(f"{name}: alias {rep!r} not a literal")
        if rep in seen_groups:
            raise ContentError(f"{name}: literal {rep!r} in two alias groups")
        # every member must be a literal and not already owned by another group
        for member in group:
            if member not in literals:
                raise ContentError(f"{name}: alias {member!r} not a literal")
            if canon[member] != member:
                raise ContentError(f"{name}: alias {member!r} already merged")
        for member in group:
            canon[member] = rep
        seen_groups.add(rep)
    answers: list[str] = []
    for lit in literals:
        c = canon[lit]
        if c not in answers:
            answers.append(c)
    if len(answers) < 1:
        raise ContentError(f"{name}: no answers")
    return Field(name=name, literals=literals, answers=tuple(answers), canon=canon)


# --------------------------------------------------------------------------- #
# statements
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Statement:
    key: str
    kind: str            # eq | neq | or | row | atleast
    text: str
    spec: Mapping[str, Any]
    deps: tuple[str, ...]

    def evaluate(self, world: World) -> bool:
        s = self.spec
        if self.kind == "eq":
            return world[s["var"]] == s["value"]
        if self.kind == "neq":
            return world[s["var"]] != s["value"]
        if self.kind == "or":
            return world[s["var"]] in s["values"]
        if self.kind == "row":
            return all(world[v] == x for v, x in s["assign"])
        if self.kind == "atleast":
            n = s["n"]
            hits = sum(1 for v, x in s["conditions"] if world[v] == x)
            return hits >= n
        raise ContentError(f"{self.key}: unknown kind {self.kind!r}")  # pragma: no cover


def _make_statement(raw: Mapping[str, Any], fields: Mapping[str, Field]) -> Statement:
    key = raw["key"]
    kind = raw["kind"]
    text = raw.get("text", key)

    def field_of(var: str) -> Field:
        if var not in fields:
            raise ContentError(f"{key}: unknown field {var!r}")
        return fields[var]

    if kind == "eq":
        f = field_of(raw["var"])
        val = f.c(raw["value"])
        return Statement(key, kind, text, {"var": f.name, "value": val}, (f.name,))
    if kind == "neq":
        f = field_of(raw["var"])
        val = f.c(raw["value"])
        return Statement(key, kind, text, {"var": f.name, "value": val}, (f.name,))
    if kind == "or":
        f = field_of(raw["var"])
        vals = tuple(dict.fromkeys(f.c(v) for v in raw["values"]))
        if not vals:
            raise ContentError(f"{key}: empty disjunction")
        return Statement(key, kind, text, {"var": f.name, "values": vals}, (f.name,))
    if kind == "row":
        assign = raw["assign"]
        if not assign:
            raise ContentError(f"{key}: empty row")
        pairs: list[tuple[str, str]] = []
        for var, value in assign.items():
            f = field_of(var)
            pairs.append((f.name, f.c(value)))
        deps = tuple(sorted({v for v, _ in pairs}))
        return Statement(key, kind, text, {"assign": tuple(pairs)}, deps)
    if kind == "atleast":
        n = int(raw.get("n", 1))
        conds = raw["conditions"]
        if not conds:
            raise ContentError(f"{key}: no conditions")
        pairs = []
        for cond in conds:
            f = field_of(cond["var"])
            pairs.append((f.name, f.c(cond["value"])))
        deps = tuple(sorted({v for v, _ in pairs}))
        return Statement(key, kind, text, {"n": n, "conditions": tuple(pairs)}, deps)
    raise ContentError(f"{key}: unknown kind {kind!r}")


# --------------------------------------------------------------------------- #
# pages and questions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Page:
    id: str
    text: str
    give: tuple[str, ...]

    def content_digest(self) -> str:
        """Identity of the *content* only -- page ids and wording do not count.

        Two different pages (or two different questions) that put the same
        statements into the reader's hands therefore produce the same record.
        """
        return digest({"give": sorted(self.give)})


@dataclass(frozen=True)
class Question:
    key: str
    text: str
    kind: str                    # "claim" or "final"
    responses: tuple[tuple[tuple[tuple[str, str], ...], str], ...]  # (when, page)
    default: str
    alternatives: tuple[str, ...]   # safe substitute pages, in preference order

    def select(self, world: World) -> str:
        for when, page in self.responses:
            if all(world[v] == x for v, x in when):
                return page
        return self.default


@dataclass
class Library:
    puzzle_id: str
    fields: Mapping[str, Field]
    field_order: tuple[str, ...]
    truth: World
    statements: Mapping[str, Statement]
    pages: Mapping[str, Page]
    questions: Mapping[str, Question]
    final_questions: Mapping[str, Question]
    asks: tuple[str, ...]              # the questions a ticket may open with
    digest: str = ""

    # -- construction -------------------------------------------------------
    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "Library":
        fields_raw = data.get("fields")
        if not fields_raw:
            raise ContentError("library has no fields")
        fields = {name: _build_field(name, spec) for name, spec in fields_raw.items()}

        truth: World = {}
        for name, val in data["truth"].items():
            if name not in fields:
                raise ContentError(f"truth: unknown field {name!r}")
            truth[name] = fields[name].c(val)
        missing = set(fields) - set(truth)
        if missing:
            raise ContentError(f"truth missing fields: {sorted(missing)}")

        statements: dict[str, Statement] = {}
        for raw in data.get("statements", []):
            st = _make_statement(raw, fields)
            if st.key in statements:
                raise ContentError(f"duplicate statement {st.key!r}")
            statements[st.key] = st

        pages: dict[str, Page] = {}
        for pid, spec in data.get("pages", {}).items():
            give = tuple(spec.get("give", ()))
            for k in give:
                if k not in statements:
                    raise ContentError(f"page {pid}: unknown statement {k!r}")
            pages[pid] = Page(pid, spec["text"], give)

        def parse_question(raw: Mapping[str, Any]) -> Question:
            qkey = raw["key"]
            responses = []
            for resp in raw.get("responses", []):
                when = tuple(
                    sorted(
                        (f, fields[f].c(v)) for f, v in resp["when"].items()
                    )
                )
                for f, _ in when:
                    if f not in fields:
                        raise ContentError(f"{qkey}: unknown field {f!r}")
                page = resp["page"]
                if page not in pages:
                    raise ContentError(f"{qkey}: unknown page {page!r}")
                responses.append((when, page))
            default = raw.get("default")
            if default is not None and default not in pages:
                raise ContentError(f"{qkey}: unknown default page {default!r}")
            alts = tuple(raw.get("alternatives", ()))
            for pid in alts:
                if pid not in pages:
                    raise ContentError(f"{qkey}: unknown alternative {pid!r}")
            if default is None:
                if not responses:
                    raise ContentError(f"{qkey}: needs a default or responses")
                default = responses[-1][1]
            return Question(
                key=qkey,
                text=raw.get("text", qkey),
                kind=raw.get("kind", "claim"),
                responses=tuple(responses),
                default=default,
                alternatives=alts,
            )

        questions: dict[str, Question] = {}
        for raw in data.get("questions", []):
            q = parse_question(raw)
            if q.key in questions:
                raise ContentError(f"duplicate question {q.key!r}")
            questions[q.key] = q
        final_questions: dict[str, Question] = {}
        for raw in data.get("final_questions", []):
            q = parse_question(raw)
            final_questions[q.key] = q

        asks = tuple(data.get("asks", list(questions)))
        for a in asks:
            if a not in questions:
                raise ContentError(f"ask {a!r} is not a question")

        lib = Library(
            puzzle_id=data["puzzle_id"],
            fields=fields,
            field_order=tuple(data.get("field_order", list(fields))),
            truth=truth,
            statements=statements,
            pages=pages,
            questions=questions,
            final_questions=final_questions,
            asks=asks,
        )
        lib.digest = digest(data)
        lib._validate_truthful_responses()
        return lib

    @staticmethod
    def from_file(path: str | Path) -> "Library":
        return Library.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- basics -------------------------------------------------------------
    def worlds(self) -> list[World]:
        names = list(self.field_order)
        domains = [self.fields[n].answers for n in names]
        return [dict(zip(names, combo)) for combo in itertools.product(*domains)]

    def answer_of(self, world: World) -> tuple[str, ...]:
        return tuple(world[n] for n in self.field_order)

    def page(self, pid: str) -> Page:
        return self.pages[pid]

    def statements_of(self, page_ids: Iterable[str]) -> list[Statement]:
        out: list[Statement] = []
        for pid in page_ids:
            for key in self.pages[pid].give:
                out.append(self.statements[key])
        return out

    def world_true_for(self, page_ids: Iterable[str], world: World) -> bool:
        """True iff every statement carried by these *pages* holds in ``world``."""
        return all(st.evaluate(world) for st in self.statements_of(page_ids))

    def give_true_for(self, give: Iterable[str], world: World) -> bool:
        """True iff every *statement key* in ``give`` holds in ``world``."""
        return all(self.statements[k].evaluate(world) for k in give)

    def consistent_worlds(
        self, live_statement_keys: Iterable[str], known: Mapping[str, str] | None = None
    ) -> list[World]:
        keys = list(live_statement_keys)
        sts = [self.statements[k] for k in keys]
        known = known or {}
        out = []
        for w in self.worlds():
            if any(w[f] != v for f, v in known.items()):
                continue
            if all(st.evaluate(w) for st in sts):
                out.append(w)
        return out

    def distinct_answers(self, worlds: Iterable[World]) -> int:
        return len({self.answer_of(w) for w in worlds})

    # -- validation ---------------------------------------------------------
    def _validate_truthful_responses(self) -> None:
        """The *primary* page a question hands out must be truthful in every
        world that would select it, or the desk would be issuing a lie.

        Substitute (alternative) pages are not required to be truthful for all
        worlds -- only for the worlds that actually receive them, which the
        engine checks at issue time.  Page *content* must, however, be unique
        within a question across primaries and alternatives, so an observed
        content identifies one page of that question.
        """
        for world in self.worlds():
            for q in list(self.questions.values()) + list(self.final_questions.values()):
                chosen = q.select(world)
                page = self.pages[chosen]
                if not self.give_true_for(page.give, world):
                    raise ContentError(
                        f"{self.puzzle_id}: question {q.key!r} would issue "
                        f"page {chosen!r} whose statements are false under "
                        f"world {world}"
                    )
        for q in list(self.questions.values()) + list(self.final_questions.values()):
            seen: dict[str, str] = {}
            stems = [q.select(w) for w in self.worlds()]
            for pid in list(dict.fromkeys(stems + list(q.alternatives))):
                c = self.pages[pid].content_digest()
                if c in seen and seen[c] != pid:
                    raise ContentError(
                        f"{self.puzzle_id}: question {q.key!r} has two pages with "
                        f"identical content ({seen[c]!r}, {pid!r}); an observed "
                        "content could not tell them apart"
                    )
                seen[c] = pid
