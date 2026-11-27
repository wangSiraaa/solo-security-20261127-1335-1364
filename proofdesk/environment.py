from __future__ import annotations

from pathlib import Path

from .actors import AuthorDesk, EditorStation, Person, ProofTerminal
from .audit import AuditLog
from .cabinet import TrustedCabinet
from .library import Library
from .trust import LocalTrust

AUTHOR = Person("alin", "林岚", "作者")
EDITOR = Person("ezhou", "周砚", "责任编辑")
PROOFREADER = Person("chenmo", "陈墨", "外包校录员")
STATION_ID = "editor-station"

BOOK_ID = "qingdengji"
BOOK_TITLE = "青灯集"

CHAPTERS = [
    {
        "id": "ch1",
        "title": "第一章 灯市",
        "text": (
            "青灯亮起时，阿苓穿过灯市。灯市如昼，人声顶沸。"
            "她在桥头停住，听见卖灯人说：“今夜的灯，只照归人。”"
        ),
    },
    {
        "id": "ch2",
        "title": "第二章 雨巷",
        "text": "雨落进青石板的缝里。阿苓撑伞走过巷子，鞋尖还是湿了。",
    },
]


class DeskEnvironment:
    """Wires the author desk, proof terminal, editor station and library."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.trust = LocalTrust(self.root / "trust-keys.json")
        self.cabinet = TrustedCabinet(self.root / "cabinet", self.trust)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.library_path = self.root / "library.json"
        if self.library_path.exists():
            self.library = Library(self.library_path, self.trust, self.audit)
        else:
            self.library = Library.initialize(
                self.library_path,
                self.trust,
                self.audit,
                book_id=BOOK_ID,
                title=BOOK_TITLE,
                owner_id=AUTHOR.id,
                owner_name=AUTHOR.name,
                chapters=CHAPTERS,
            )
        self.author_desk = AuthorDesk(
            person=AUTHOR,
            trust=self.trust,
            cabinet=self.cabinet,
            state_path=self.root / "delivery-choices.json",
        )
        self.station = EditorStation(
            person=EDITOR,
            station_id=STATION_ID,
            trust=self.trust,
            cabinet=self.cabinet,
            state_path=self.root / "editor-station-state.json",
        )
        self.terminal = ProofTerminal(
            person=PROOFREADER,
            trust=self.trust,
            cabinet=self.cabinet,
        )

    def restart(self) -> "DeskEnvironment":
        """Simulate restart while retaining files and local credentials."""
        return DeskEnvironment(self.root)

    def deliver_to_library(self) -> list:
        return self.library.accept_cabinet(self.cabinet)

    def sync_station(self):
        return self.station.sync_cabinet()
