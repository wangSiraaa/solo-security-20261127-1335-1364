"""Real read-only query endpoint over the atomically mounted catalog."""

from __future__ import annotations

import csv
import io
import os
from typing import Any, Dict, List

from .canonical import sha256_text
from .receiver import RecoveryTerminal


class QueryError(RuntimeError):
    pass


class TableUnavailable(QueryError):
    pass


class DependencyMismatch(QueryError):
    pass


def parse_csv(body: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body)))


class QueryEndpoint:
    def __init__(self, root: str | os.PathLike[str], terminal: RecoveryTerminal | None = None):
        self.terminal = terminal or RecoveryTerminal(root)
        self.root = self.terminal.root

    def _table(self, kind: str, *, required: bool = True) -> Dict[str, Any] | None:
        manifest = self.terminal.current_manifest()
        entry = manifest.get("artifacts", {}).get(kind)
        if entry is None:
            if required:
                raise TableUnavailable(f"{kind} table is not mounted")
            return None

        path = self.terminal.current / entry["file"]
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise TableUnavailable(f"{kind} table file is missing") from e

        if sha256_text(text) != entry.get("body_sha256"):
            raise QueryError(f"{kind} mounted file failed its body hash check")

        art = self.terminal.get_artifact(entry["digest"])
        if art is None:
            raise QueryError(f"{kind} receipted artifact is absent from the store")
        if text != art.get("body"):
            raise QueryError(f"{kind} mounted file differs from its receipted artifact")
        if entry.get("body_sha256") != art["header"].get("body_sha256"):
            raise QueryError(f"{kind} manifest does not match receipted header")
        if self.terminal.is_revoked(art):
            raise TableUnavailable(f"{kind} generation has been revoked")
        return {"entry": entry, "text": text, "rows": parse_csv(text)}

    @staticmethod
    def _find_contact(rows: List[Dict[str, str]], query: str) -> Dict[str, str] | None:
        q = query.strip().casefold()
        for row in rows:
            if row.get("person_id", "").casefold() == q:
                return row
        for row in rows:
            if row.get("name", "").strip().casefold() == q:
                return row
        return None

    def abbreviation(self, name_or_id: str) -> Dict[str, Any]:
        table = self._table("contacts")
        assert table is not None
        row = self._find_contact(table["rows"], name_or_id)
        if row is None:
            raise QueryError(f"no contact matches {name_or_id!r}")
        abbr = row.get("abbr") or row.get("abbreviation")
        if not abbr:
            raise QueryError("contact row has no abbr column")
        entry = table["entry"]
        return {
            "query": name_or_id,
            "person_id": row.get("person_id"),
            "name": row.get("name", name_or_id),
            "abbreviation": abbr,
            "source_artifact_id": entry["artifact_id"],
            "generation": entry["generation"],
            "batch": entry["batch"],
        }

    def locker_number(self, locker_id: str) -> Dict[str, Any]:
        table = self._table("lockers")
        assert table is not None
        for row in table["rows"]:
            if row.get("locker_id") == locker_id:
                number = row.get("locker_number") or row.get("number")
                return {
                    "locker_id": locker_id,
                    "locker_number": number,
                    "source_artifact_id": table["entry"]["artifact_id"],
                    "generation": table["entry"]["generation"],
                }
        raise QueryError(f"no locker id {locker_id!r}")

    def _checked_duty_pair(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        duty = self._table("duty")
        lockers = self._table("lockers")
        assert duty is not None and lockers is not None

        active_digests = {
            kind: entry["digest"]
            for kind, entry in self.terminal.current_manifest()["artifacts"].items()
        }
        for dep in duty["entry"].get("dependencies", []):
            wanted = dep["digest"]
            actual = active_digests.get(dep["kind"])
            if actual != wanted:
                raise DependencyMismatch(
                    f"duty table requires {dep['kind']} {dep['artifact_id']}, "
                    f"but active digest is {actual}"
                )
        return duty, lockers

    def duty_roster(self, date: str | None = None) -> List[Dict[str, Any]]:
        duty, lockers = self._checked_duty_pair()
        locker_map = {r.get("locker_id"): r for r in lockers["rows"]}
        contacts_table = self._table("contacts", required=False)
        contacts = contacts_table["rows"] if contacts_table else []

        out: List[Dict[str, Any]] = []
        for row in duty["rows"]:
            if date is not None and row.get("date") != date:
                continue
            locker_id = row.get("locker_id", "")
            locker_row = locker_map.get(locker_id)
            if locker_row is None:
                raise DependencyMismatch(
                    f"mounted locker generation has no pin-referenced id {locker_id!r}"
                )
            person_id = row.get("person_id")
            contact = None
            if person_id:
                contact = self._find_contact(contacts, person_id)
            if contact is None:
                contact = self._find_contact(contacts, row.get("name", ""))
            out.append(
                {
                    "date": row.get("date"),
                    "shift": row.get("shift"),
                    "person_id": person_id,
                    "name": (contact or {}).get("name", row.get("name")),
                    "abbreviation": (contact or {}).get("abbr")
                    or (contact or {}).get("abbreviation"),
                    "locker_id": locker_id,
                    "locker_number": locker_row.get("locker_number")
                    or locker_row.get("number"),
                    "duty_artifact_id": duty["entry"]["artifact_id"],
                    "locker_artifact_id": lockers["entry"]["artifact_id"],
                }
            )
        return out

    def person(self, name_or_id: str, date: str | None = None) -> Dict[str, Any]:
        """Return the independently safe abbreviation plus duty locker if safe."""
        result: Dict[str, Any] = {"query": name_or_id}
        try:
            contact = self.abbreviation(name_or_id)
            result.update(contact)
            result["abbreviation_available"] = True
        except TableUnavailable:
            result["abbreviation_available"] = False
            result["abbreviation"] = None

        try:
            roster = self.duty_roster(date)
            q = name_or_id.strip().casefold()
            person_id = result.get("person_id")
            matches = [
                r
                for r in roster
                if (person_id and r.get("person_id") == person_id)
                or (r.get("person_id") or "").strip().casefold() == q
                or (r.get("name") or "").strip().casefold() == q
            ]
            if matches:
                assignment = matches[0]
                result["assignment"] = assignment
                result["locker_number"] = assignment["locker_number"]
                result["duty_available"] = True
            else:
                result["assignment"] = None
                result["locker_number"] = None
                result["duty_available"] = False
                result["duty_reason"] = "no matching duty row"
        except QueryError as e:
            result["assignment"] = None
            result["locker_number"] = None
            result["duty_available"] = False
            result["duty_reason"] = str(e)

        if not result.get("abbreviation_available") and not result.get("duty_available"):
            raise TableUnavailable("neither independent contacts nor a safe duty pair is mounted")
        return result
