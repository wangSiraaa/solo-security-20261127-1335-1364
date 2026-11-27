"""离线发端：申请到封条后持久消耗号段，重启也不能把旧号转给新正文。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import AllocationError, SealConsumedError, SendOrderError
from .models import TRIP_LABELS, normalize_trip
from .security import sha256_hex
from .verifier import body_receipt_value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OfflineTerminal:
    def __init__(self, store: Any, trip: str) -> None:
        self.store = store
        self.trip = normalize_trip(trip)
        self.state_path = f"terminals/{self.trip}.json"
        self.data = store.read_json(self.state_path)

    @classmethod
    def create(
        cls,
        store: Any,
        trip: str,
        seals: List[int],
        credentials: Dict[str, Dict[str, str]],
    ) -> "OfflineTerminal":
        trip = normalize_trip(trip)
        data = {
            "trip": trip,
            "label": TRIP_LABELS[trip],
            "seals": sorted(seals),
            "departed": False,
            "departed_at": None,
            "restart_count": 0,
            "consumed": {},
            "created_at": utc_now(),
            "credentials": credentials,
        }
        store.write_json(f"terminals/{trip}.json", value=data)
        return cls(store, trip)

    def save(self) -> None:
        self.store.write_json(self.state_path, value=self.data)

    def restart(self) -> Dict[str, Any]:
        """显式记录一次重启；CLI 每次新进程本来也会从磁盘重载。"""

        self.data["restart_count"] = int(self.data.get("restart_count", 0)) + 1
        self.data["last_restart_at"] = utc_now()
        self.save()
        return self.data

    @property
    def seals(self) -> List[int]:
        return list(self.data["seals"])

    @property
    def consumed(self) -> Dict[str, Dict[str, Any]]:
        return self.data.setdefault("consumed", {})

    def require_departed(self) -> None:
        if not self.data.get("departed"):
            raise AllocationError(
                f"{TRIP_LABELS[self.trip]}端尚未离站；离站后才能写信发报",
                code="NOT_DEPARTED",
            )

    def depart(self) -> None:
        self.data["departed"] = True
        self.data["departed_at"] = utc_now()
        self.save()

    def is_consumed(self, seal: int) -> bool:
        return str(seal) in self.consumed

    def next_seal(self) -> Optional[int]:
        for seal in self.seals:
            if not self.is_consumed(seal):
                return seal
        return None

    def _check_ascending(self, seal: int) -> None:
        idx = self.seals.index(seal)
        for earlier in self.seals[:idx]:
            if not self.is_consumed(earlier):
                raise SendOrderError(
                    f"{TRIP_LABELS[self.trip]}端必须从小到大发报；"
                    f"{seal} 号之前还有 {earlier} 号未使用",
                    details={"trip": self.trip, "seal": seal, "waiting_for": earlier},
                )

    def write_message(self, body: str, seal: Optional[int] = None) -> Dict[str, Any]:
        """写信并消耗封条。

        同一封条若已绑定另一正文，无论重启多少次都抛出
        :class:`SealConsumedError`，且磁盘上的原绑定不会被覆盖。
        """

        self.require_departed()
        if not isinstance(body, str) or body == "":
            raise AllocationError("电文正文不能为空", code="EMPTY_BODY")

        if seal is None:
            seal = self.next_seal()
            if seal is None:
                raise AllocationError(
                    f"{TRIP_LABELS[self.trip]}端的三枚封条均已耗尽",
                    code="NO_SEALS_LEFT",
                )
        seal = int(seal)
        if seal not in self.seals:
            raise AllocationError(
                f"{seal} 号不归{TRIP_LABELS[self.trip]}端所有（本组号码 {self.seals}）",
                code="SEAL_NOT_OWNED",
            )

        body_hash = sha256_hex(body)
        prior = self.consumed.get(str(seal))
        if prior is not None:
            if prior.get("body_sha256") != body_hash:
                raise SealConsumedError(
                    f"{seal} 号封条已经用于另一封正文；发端重启也不能改给新正文",
                    details={
                        "seal": seal,
                        "original_body_sha256": prior.get("body_sha256"),
                        "new_body_sha256": body_hash,
                    },
                )
            existing_path = prior.get("envelope_path")
            if existing_path and self.store.exists(existing_path):
                envelope = self.store.read_json(existing_path)
                envelope["idempotent_replay"] = True
                return envelope

        self._check_ascending(seal)

        credential = self.data.get("credentials", {}).get(str(seal))
        if not credential:
            raise AllocationError(
                f"缺少 {seal} 号封条的预置回执材料",
                code="MISSING_SEAL_CREDENTIAL",
            )

        # 先持久化“该号已绑此正文”，再产出信封。即使随后进程崩溃，也不会
        # 把已耗号码误当成空号转给下一封正文。
        bound_at = utc_now()
        binding = {
            "seal": seal,
            "owner": self.trip,
            "body_sha256": body_hash,
            "body_length": len(body.encode("utf-8")),
            "bound_at": bound_at,
            "restart_generation": int(self.data.get("restart_count", 0)),
            "envelope_path": f"outbox/{self.trip}/{seal}.json",
        }
        self.consumed[str(seal)] = binding
        self.save()

        receipt = body_receipt_value(
            credential["seal_key_hex"], seal, self.trip, body_hash
        )
        envelope = {
            "format": "seal-channel/envelope-v1",
            "seal": seal,
            "owner": self.trip,
            "owner_label": TRIP_LABELS[self.trip],
            "body": body,
            "body_sha256": body_hash,
            "body_receipt": receipt,
            "grant_digest": credential["grant_digest"],
            "created_at": bound_at,
        }
        self.store.write_json(binding["envelope_path"], value=envelope)
        binding["envelope_written_at"] = utc_now()
        self.save()
        return envelope

    def envelope_path(self, seal: int) -> str:
        return f"outbox/{self.trip}/{int(seal)}.json"

    def load_envelope(self, seal: int) -> Dict[str, Any]:
        return self.store.read_json(self.envelope_path(seal))

    def all_envelopes(self) -> List[Dict[str, Any]]:
        result = []
        for seal in self.seals:
            rel = self.envelope_path(seal)
            if self.store.exists(rel):
                result.append(self.store.read_json(rel))
        return result
