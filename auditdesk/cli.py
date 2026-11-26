"""离线审计取证台命令行。

示例：
  python3 -m auditdesk.cli init runs/demo
  python3 -m auditdesk.cli view runs/demo/case
  python3 -m auditdesk.cli verify-cert runs/demo/case runs/demo/case/certs/C-xxx.json
  python3 -m auditdesk.cli import-bundle runs/demo/case bundle.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .certifier import Certifier
from .chain import Chain
from .models import Bundle
from .render import render_cert
from .store import CaseStore
from .witness import WitnessStation


def cmd_init(root: str, station: str) -> None:
    store = CaseStore(Path(root) / "case")
    st = WitnessStation(station, Path(root) / "stations")
    store.register_pubkey(st.key_id, st.pub_pem.decode())
    print(f"案件库已建：{root}/case；见证站 {station} 钥匙 {st.key_id}")


def cmd_view(root: str) -> None:
    store = CaseStore(root)
    chain = Chain(store)
    print(f"页 {len(store.all_pages())} 张，签收 {len(store.all_receipts())} 份，"
          f"登记公钥 {len(store.pubkeys)} 把")
    for sid, view in chain.station_views().items():
        print(f"  {sid}: 链头页号 {view.tip_nos}，缺页 {view.gaps or '无'}")
    tips = set()
    for v in chain.station_views().values():
        tips.update(v.tips)
    tips = list(tips)
    if len(tips) >= 2:
        lca = chain.last_common_history(tips[0], tips[1])
        print(f"  最后共同历史：第 {lca['last_common_no']} 页")


def cmd_import(root: str, bundle_path: str) -> None:
    store = CaseStore(root)
    bundle = Bundle.from_json(Path(bundle_path).read_text(encoding="utf-8"))
    print(json.dumps(store.import_bundle(bundle), ensure_ascii=False, indent=2))


def cmd_verify_cert(root: str, cert_path: str) -> None:
    store = CaseStore(root)
    cert = json.loads(Path(cert_path).read_text(encoding="utf-8"))
    verdict = Certifier.verify_cert(store, cert)
    print(json.dumps(verdict, ensure_ascii=False))
    if verdict["valid"]:
        print(render_cert(cert))
    sys.exit(0 if verdict["valid"] else 2)


def cmd_verify_entry(root: str, entry_id: str, tip_digest: str) -> None:
    chain = Chain(CaseStore(root))
    proof = chain.inclusion_proof(entry_id, tip_digest)
    print(json.dumps(proof, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    cmd, *args = argv
    if cmd == "init" and len(args) == 2:
        cmd_init(*args)
    elif cmd == "view" and len(args) == 1:
        cmd_view(*args)
    elif cmd == "import-bundle" and len(args) == 2:
        cmd_import(*args)
    elif cmd == "verify-cert" and len(args) == 2:
        cmd_verify_cert(*args)
    elif cmd == "verify-entry" and len(args) == 3:
        cmd_verify_entry(*args)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
