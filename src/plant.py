"""机房物理仓库 + 受托存储进程（admin / trusted process）。

信任与退款边界（贯穿全文件的设计决策）：

* 库房信任 **受托存储进程**（本类的 Plant）：只有它能看见跨户的内容相等关系，
  也只有它持有“物理占盘 / 待回收空间 / 回收作业”的真实账。
* 租户库只在自己作用域内被信任；对外只经由 client.TenantClient 暴露本户视图。
* 客户端 **不接收任何全局摘要索引**：回单 / 还原结果只含本户页与计费，
  不会出现别家的摘要、卷号或“别家已有此页”。

* **退款承诺保护的是逻辑独占配额**（租户 committed 的逻辑字节），不是共享实块。
  客户删册/撤约后，逻辑配额按真实合同退还；跨户共享实块省下的物理空间
  只记在 admin_ledger，不返还客户。

存储模型：
* 物理：physical_blobs 以内容 SHA 为键，跨户共享，按引用计数存活
  （live -> pending_reclaim -> 回收后删除）。
* 逻辑：logical_blobs 以 (tenant, SHA) 为键，户内去重，作为客户计费口径。
  同一页被别家也存过时，本户仍是“首次入账”，全价计费、回单不提示别家已有。
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid

PAGE_LEN = 400  # 每页色样的字节长度（小文件，便于演示）


class PlantError(Exception):
    def __init__(self, code, message=""):
        self.code = code
        self.message = message or code
        super().__init__(f"{code}: {self.message}")


def swatch_bytes(spec: str, length: int = PAGE_LEN) -> bytes:
    """由色样规格确定性生成的虚构页字节（同 spec 永远得到同一段字节）。"""
    out = bytearray()
    i = 0
    while len(out) < length:
        out.extend(hashlib.sha256(f"swatch|{spec}|{i}".encode()).digest())
        i += 1
    return bytes(out[:length])


def sha_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Plant:
    """受托存储进程：admin 面 + 全部内部实现。"""

    def __init__(self, db_path=":memory:", physical_capacity=None, grace_seconds=60):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.grace_seconds = grace_seconds
        self._create_schema()
        self._init_meta(physical_capacity)

    # ================================================================ schema
    def _create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS quotas(
                tenant TEXT PRIMARY KEY,
                quota_bytes INTEGER NOT NULL,
                reserved INTEGER NOT NULL DEFAULT 0,
                committed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS physical_blobs(
                sha TEXT PRIMARY KEY,
                bytes_len INTEGER NOT NULL,
                data BLOB NOT NULL,
                refcount INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'live',   -- live|pending_reclaim
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS logical_blobs(
                tenant TEXT NOT NULL,
                sha TEXT NOT NULL,
                bytes_len INTEGER NOT NULL,
                refcount INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(tenant, sha)
            );
            CREATE TABLE IF NOT EXISTS volumes(
                tenant TEXT NOT NULL,
                volume_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'live',    -- live|disabled
                PRIMARY KEY(tenant, volume_id)
            );
            CREATE TABLE IF NOT EXISTS pages(
                tenant TEXT NOT NULL,
                volume_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                page_id TEXT NOT NULL,
                sha TEXT NOT NULL,
                bytes_len INTEGER NOT NULL,
                request_id TEXT,
                PRIMARY KEY(tenant, volume_id, page_no)
            );
            CREATE TABLE IF NOT EXISTS requests(
                request_id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                volume_id TEXT NOT NULL,
                declared_bytes INTEGER NOT NULL,
                state TEXT NOT NULL,          -- reserved|partial|received|voided
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_pages(
                request_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                page_id TEXT NOT NULL,
                sha TEXT NOT NULL,
                bytes_len INTEGER NOT NULL,
                PRIMARY KEY(request_id, page_no)
            );
            CREATE TABLE IF NOT EXISTS receipt_pages(
                request_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                page_state TEXT NOT NULL,     -- received|pending_capacity|failed
                PRIMARY KEY(request_id, page_no)
            );
            CREATE TABLE IF NOT EXISTS pending_pages(
                request_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                data BLOB NOT NULL,
                PRIMARY KEY(request_id, page_no)
            );
            CREATE TABLE IF NOT EXISTS contracts(
                contract_id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                volume_id TEXT NOT NULL,
                bytes_held INTEGER NOT NULL,
                released_at INTEGER,             -- NULL = 有效
                quota_released INTEGER NOT NULL DEFAULT 0,
                UNIQUE(tenant, volume_id)
            );
            CREATE TABLE IF NOT EXISTS ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL,
                ts INTEGER NOT NULL,
                kind TEXT NOT NULL,
                amount INTEGER NOT NULL,
                request_id TEXT,
                note TEXT,
                committed_after INTEGER
            );
            CREATE TABLE IF NOT EXISTS admin_ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                kind TEXT NOT NULL,           -- ingest|physical_saving|reclaim
                physical_bytes INTEGER NOT NULL DEFAULT 0,
                logical_bytes INTEGER NOT NULL DEFAULT 0,
                sha TEXT,
                tenant TEXT,
                request_id TEXT,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS reclaim_jobs(
                job_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,          -- open|done
                created_at INTEGER NOT NULL,
                done_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS reclaim_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                sha TEXT NOT NULL,
                request_id TEXT,
                tenant TEXT,
                released_bytes INTEGER NOT NULL,
                state TEXT NOT NULL           -- candidate|pending|freed
            );
            CREATE TABLE IF NOT EXISTS client_demand(
                tenant TEXT NOT NULL,
                volume_id TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(tenant, volume_id)
            );
            """
        )
        self.conn.commit()

    def _init_meta(self, physical_capacity):
        if physical_capacity is None:
            physical_capacity = 1 << 20
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('physical_capacity', ?)",
            (str(physical_capacity),))
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('clock', '1000')")
        self.conn.commit()

    # ================================================================ clock
    def now(self):
        return int(self.conn.execute(
            "SELECT value FROM meta WHERE key='clock'").fetchone()[0])

    def tick(self, seconds=1):
        with self.conn:
            self.conn.execute(
                "UPDATE meta SET value=CAST(CAST(value AS INTEGER)+? AS TEXT)"
                " WHERE key='clock'", (seconds,))
        return self.now()

    # ================================================================ usage
    def physical_used(self):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(bytes_len),0) AS u FROM physical_blobs"
            " WHERE state IN ('live','pending_reclaim')").fetchone()
        return row["u"]

    def physical_capacity(self):
        return int(self.conn.execute(
            "SELECT value FROM meta WHERE key='physical_capacity'").fetchone()[0])

    def set_physical_capacity(self, capacity):
        """机房调整物理仓库总容量（管理员操作）。"""
        with self.conn:
            self.conn.execute(
                "UPDATE meta SET value=? WHERE key='physical_capacity'", (str(capacity),))
        return capacity

    def pending_reclaim_bytes(self):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(bytes_len),0) AS u FROM physical_blobs"
            " WHERE state='pending_reclaim'").fetchone()
        return row["u"]

    def admin_inventory(self):
        blobs = [dict(r) for r in self.conn.execute(
            "SELECT sha, bytes_len, refcount, state FROM physical_blobs ORDER BY sha")]
        logical = self.conn.execute(
            "SELECT COALESCE(SUM(committed),0) AS s FROM quotas").fetchone()["s"]
        return {
            "physical_capacity": self.physical_capacity(),
            "physical_used": self.physical_used(),
            "pending_reclaim_bytes": self.pending_reclaim_bytes(),
            "free_bytes": self.physical_capacity() - self.physical_used(),
            "logical_committed_total": logical,
            "physical_savings_total": self.conn.execute(
                "SELECT COALESCE(SUM(physical_bytes),0) AS s FROM admin_ledger"
                " WHERE kind='physical_saving'").fetchone()["s"],
            "blobs": blobs,
        }

    def admin_ledger_rows(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM admin_ledger ORDER BY id")]

    # ================================================================ tenants
    def register_tenant(self, tenant, quota_bytes):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO quotas(tenant, quota_bytes)"
                " VALUES(?,?)", (tenant, quota_bytes))

    def _quota(self, tenant):
        row = self.conn.execute("SELECT * FROM quotas WHERE tenant=?", (tenant,)).fetchone()
        if not row:
            raise PlantError("unknown_tenant", tenant)
        return row

    def client_balance(self, tenant):
        q = self._quota(tenant)
        return {
            "tenant": tenant,
            "quota_bytes": q["quota_bytes"],
            "reserved": q["reserved"],
            "committed": q["committed"],
            "refundable_bytes": q["quota_bytes"] - q["reserved"] - q["committed"],
        }

    def ledger_rows(self, tenant):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM ledger WHERE tenant=? ORDER BY id", (tenant,))]

    def _ledger(self, tenant, kind, amount, request_id=None, note=""):
        q = self._quota(tenant)
        self.conn.execute(
            "INSERT INTO ledger(tenant, ts, kind, amount, request_id, note,"
            " committed_after) VALUES(?,?,?,?,?,?,?)",
            (tenant, self.now(), kind, amount, request_id, note, q["committed"]))

    def _admin(self, kind, physical_bytes=0, logical_bytes=0, sha=None, tenant=None,
               request_id=None, note=""):
        self.conn.execute(
            "INSERT INTO admin_ledger(ts, kind, physical_bytes, logical_bytes, sha,"
            " tenant, request_id, note) VALUES(?,?,?,?,?,?,?,?)",
            (self.now(), kind, physical_bytes, logical_bytes, sha, tenant,
             request_id, note))

    # ================================================================ 预约
    def reserve(self, tenant, volume_id, specs):
        """获准预约：校验逻辑配额后冻结 declared 额度，生成 request_id。

        specs: [(page_no, spec), ...]，有序页，可重复。
        返回本户视角的预约回执（只含本户页）。
        """
        pages = []
        for page_no, spec in specs:
            data = swatch_bytes(spec)
            pages.append((page_no, "pg-" + uuid.uuid4().hex[:10], sha_hex(data), len(data)))
        declared = sum(p[3] for p in pages)
        q = self._quota(tenant)
        free = q["quota_bytes"] - q["reserved"] - q["committed"]
        if declared > free:
            raise PlantError("quota_exceeded", f"need {declared}, free {free}")
        request_id = "req-" + uuid.uuid4().hex[:12]
        with self.conn:
            self.conn.execute(
                "UPDATE quotas SET reserved=reserved+? WHERE tenant=?", (declared, tenant))
            self.conn.execute(
                "INSERT INTO requests(request_id, tenant, volume_id, declared_bytes,"
                " state, created_at) VALUES(?,?,?,?, 'reserved', ?)",
                (request_id, tenant, volume_id, declared, self.now()))
            self.conn.execute(
                "INSERT OR IGNORE INTO volumes(tenant, volume_id, state)"
                " VALUES(?,?, 'live')", (tenant, volume_id))
            for page_no, page_id, sha, blen in pages:
                self.conn.execute(
                    "INSERT INTO request_pages(request_id, page_no, page_id, sha,"
                    " bytes_len) VALUES(?,?,?,?,?)",
                    (request_id, page_no, page_id, sha, blen))
        self._ledger(tenant, "reserve_hold", -declared, request_id, "预约冻结逻辑额度")
        return {
            "request_id": request_id,
            "volume_id": volume_id,
            "declared_bytes": declared,
            "pages": [{"page_no": p[0], "page_id": p[1], "sha": p[2], "bytes_len": p[3]}
                      for p in pages],
        }

    # ================================================================ 上载
    def receive(self, request_id, page_no, data):
        """按预约上载一页（页身份由 page_id+sha 预约快照固定）。"""
        req = self.conn.execute(
            "SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if not req:
            raise PlantError("unknown_request", request_id)
        if req["state"] == "voided":
            raise PlantError("request_voided", request_id)
        rp = self.conn.execute(
            "SELECT * FROM request_pages WHERE request_id=? AND page_no=?",
            (request_id, page_no)).fetchone()
        if not rp:
            raise PlantError("unknown_page", page_no)
        if sha_hex(data) != rp["sha"] or len(data) != rp["bytes_len"]:
            raise PlantError("page_mismatch", f"page {page_no}")

        tenant = req["tenant"]
        # 幂等：同一预约页已受理过则原样返回，不重复计账 / 不重复加引用
        prev = self.conn.execute(
            "SELECT page_state FROM receipt_pages WHERE request_id=? AND page_no=?",
            (request_id, page_no)).fetchone()
        if prev and prev["page_state"] == "received":
            already_here = self.conn.execute(
                "SELECT 1 FROM logical_blobs WHERE tenant=? AND sha=?",
                (tenant, rp["sha"])).fetchone() is not None
            return self._receipt_view(tenant, request_id, page_no, "received",
                                      already_here)

        # 本户是否已有此页（户内去重；与别家无关）
        already_here = self.conn.execute(
            "SELECT 1 FROM logical_blobs WHERE tenant=? AND sha=?",
            (tenant, rp["sha"])).fetchone() is not None

        outcome = self._place_page(tenant, request_id, req["volume_id"], rp, data)
        self._finalize_if_done(request_id)
        return self._receipt_view(tenant, request_id, page_no, outcome, already_here)

    def _place_page(self, tenant, request_id, volume_id, rp, data):
        """尝试把一页落盘。返回 received / pending_capacity。"""
        sha, blen = rp["sha"], rp["bytes_len"]
        phys = self.conn.execute(
            "SELECT * FROM physical_blobs WHERE sha=?", (sha,)).fetchone()
        with self.conn:
            if phys is not None and phys["state"] == "live":
                self.conn.execute(
                    "UPDATE physical_blobs SET refcount=refcount+1 WHERE sha=?", (sha,))
                self._admin("physical_saving", physical_bytes=blen, logical_bytes=blen,
                            sha=sha, tenant=tenant, request_id=request_id,
                            note="内容相等，复用物理实块（客户按逻辑价照付）")
                placed = "received"
            elif phys is not None and phys["state"] == "pending_reclaim":
                # 回收未完成：此刻不能复用，按容量不足受理
                placed = "pending_capacity"
            else:
                if self.physical_used() + blen > self.physical_capacity():
                    placed = "pending_capacity"
                else:
                    self.conn.execute(
                        "INSERT INTO physical_blobs(sha, bytes_len, data, refcount,"
                        " state, created_at) VALUES(?,?,?,1,'live',?)",
                        (sha, blen, sqlite3.Binary(data), self.now()))
                    self._admin("ingest", physical_bytes=blen, logical_bytes=blen,
                                sha=sha, tenant=tenant, request_id=request_id,
                                note="首次物理占盘")
                    placed = "received"

            self.conn.execute(
                "INSERT INTO receipt_pages(request_id, page_no, page_state)"
                " VALUES(?,?,?) ON CONFLICT(request_id, page_no)"
                " DO UPDATE SET page_state=excluded.page_state",
                (request_id, rp["page_no"], placed))

            if placed == "received":
                self._commit_logical(tenant, request_id, volume_id, rp)
                self.conn.execute(
                    "DELETE FROM pending_pages WHERE request_id=? AND page_no=?",
                    (request_id, rp["page_no"]))
                self.conn.execute(
                    "UPDATE requests SET state='partial' WHERE request_id=?"
                    " AND state='reserved'", (request_id,))
            else:
                self.conn.execute(
                    "INSERT OR REPLACE INTO pending_pages(request_id, page_no, data)"
                    " VALUES(?,?,?)", (request_id, rp["page_no"], sqlite3.Binary(data)))
                self.conn.execute(
                    "UPDATE requests SET state='partial' WHERE request_id=?"
                    " AND state='reserved'", (request_id,))
        return placed

    def _commit_logical(self, tenant, request_id, volume_id, rp):
        sha, blen = rp["sha"], rp["bytes_len"]
        lb = self.conn.execute(
            "SELECT * FROM logical_blobs WHERE tenant=? AND sha=?", (tenant, sha)).fetchone()
        self.conn.execute(
            "INSERT OR REPLACE INTO pages(tenant, volume_id, page_no, page_id, sha,"
            " bytes_len, request_id) VALUES(?,?,?,?,?,?,?)",
            (tenant, volume_id, rp["page_no"], rp["page_id"], sha, blen, request_id))
        if lb is None:
            self.conn.execute(
                "INSERT INTO logical_blobs(tenant, sha, bytes_len, refcount)"
                " VALUES(?,?,?,1)", (tenant, sha, blen))
            q = self._quota(tenant)
            move = min(blen, q["reserved"])
            self.conn.execute(
                "UPDATE quotas SET reserved=reserved-?, committed=committed+?"
                " WHERE tenant=?", (move, blen, tenant))
            self._ledger(tenant, "commit", blen, request_id, "页入账，预约转已用")
        else:
            self.conn.execute(
                "UPDATE logical_blobs SET refcount=refcount+1"
                " WHERE tenant=? AND sha=?", (tenant, sha))
            self._ledger(tenant, "commit_repeat", 0, request_id,
                         "户内重复页，逻辑不重复计费")

    def _finalize_if_done(self, request_id):
        req = self.conn.execute(
            "SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if req["state"] == "received":
            return
        pending = self.conn.execute(
            "SELECT COUNT(*) AS n FROM receipt_pages WHERE request_id=? AND"
            " page_state='pending_capacity'", (request_id,)).fetchone()["n"]
        if pending:
            return
        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM request_pages WHERE request_id=?",
            (request_id,)).fetchone()["n"]
        done = self.conn.execute(
            "SELECT COUNT(*) AS n FROM receipt_pages WHERE request_id=? AND"
            " page_state='received'", (request_id,)).fetchone()["n"]
        if done < total:
            return
        moved = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM ledger WHERE tenant=? AND"
            " request_id=? AND kind='commit'", (req["tenant"], request_id)
        ).fetchone()["s"]
        leftover = max(0, req["declared_bytes"] - moved)
        q = self._quota(req["tenant"])
        rel = min(leftover, q["reserved"])
        with self.conn:
            if rel > 0:
                self.conn.execute(
                    "UPDATE quotas SET reserved=reserved-? WHERE tenant=?",
                    (rel, req["tenant"]))
                self._ledger(req["tenant"], "release_hold", 0, request_id,
                             f"结算退回未用冻结 {rel}")
            self.conn.execute(
                "UPDATE requests SET state='received' WHERE request_id=?", (request_id,))
            self.conn.execute(
                "INSERT OR REPLACE INTO client_demand(tenant, volume_id, status)"
                " VALUES(?,?, 'stored')", (req["tenant"], req["volume_id"]))
            # 保留订约：首次结算为该卷建立有效合同（admin 侧记录字节数）
            held = self.conn.execute(
                "SELECT COALESCE(SUM(bytes_len),0) AS s FROM pages WHERE tenant=? AND"
                " volume_id=?", (req["tenant"], req["volume_id"])).fetchone()["s"]
            if not self.conn.execute(
                    "SELECT 1 FROM contracts WHERE tenant=? AND volume_id=?",
                    (req["tenant"], req["volume_id"])).fetchone():
                self.conn.execute(
                    "INSERT INTO contracts(contract_id, tenant, volume_id, bytes_held)"
                    " VALUES(?,?,?,?)",
                    ("ct-" + uuid.uuid4().hex[:10], req["tenant"],
                     req["volume_id"], held))
            self._ledger(req["tenant"], "refundable_after", 0, request_id,
                         f"可退额度 {self.client_balance(req['tenant'])['refundable_bytes']}")

    def _receipt_view(self, tenant, request_id, page_no, outcome, already_here):
        bal = self.client_balance(tenant)
        return {
            "request_id": request_id,
            "page_no": page_no,
            "state": outcome,
            "already_in_this_tenant": already_here,   # 仅本户事实
            "refundable_bytes_after": bal["refundable_bytes"],
        }

    def retry_pending(self, request_id=None):
        """在容量/回收条件改善后，重试 pending 页。"""
        if request_id:
            rows = self.conn.execute(
                "SELECT * FROM pending_pages WHERE request_id=? ORDER BY page_no",
                (request_id,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM pending_pages ORDER BY request_id, page_no").fetchall()
        results = []
        for row in rows:
            req = self.conn.execute(
                "SELECT * FROM requests WHERE request_id=?", (row["request_id"],)).fetchone()
            rp = self.conn.execute(
                "SELECT * FROM request_pages WHERE request_id=? AND page_no=?",
                (row["request_id"], row["page_no"])).fetchone()
            if req is None or rp is None:
                continue
            outcome = self._place_page(req["tenant"], req["request_id"],
                                       req["volume_id"], rp, bytes(row["data"]))
            results.append({"request_id": req["request_id"],
                            "page_no": row["page_no"], "state": outcome})
            self._finalize_if_done(req["request_id"])
        return results

    # ================================================================ 还原
    def restore(self, tenant, volume_id):
        vol = self.conn.execute(
            "SELECT * FROM volumes WHERE tenant=? AND volume_id=?",
            (tenant, volume_id)).fetchone()
        # 保留订约撤销且宽限期已过 -> 版本不再可恢复（真实合同）
        contract = self.conn.execute(
            "SELECT * FROM contracts WHERE tenant=? AND volume_id=?",
            (tenant, volume_id)).fetchone()
        if (contract is not None and contract["released_at"] is not None
                and self.now() >= contract["released_at"] + self.grace_seconds):
            raise PlantError("retention_expired", volume_id)
        pages = self.conn.execute(
            "SELECT * FROM pages WHERE tenant=? AND volume_id=? ORDER BY page_no",
            (tenant, volume_id)).fetchall()
        if not pages:
            raise PlantError("volume_not_recoverable", volume_id)
        out = []
        for p in pages:
            phys = self.conn.execute(
                "SELECT * FROM physical_blobs WHERE sha=?", (p["sha"],)).fetchone()
            if phys is None or phys["state"] != "live":
                raise PlantError("volume_unavailable", f"page {p['page_no']}")
            data = bytes(phys["data"])
            if sha_hex(data) != p["sha"] or len(data) != p["bytes_len"]:
                raise PlantError("restore_checksum_failed", f"page {p['page_no']}")
            out.append({"page_no": p["page_no"], "page_id": p["page_id"],
                        "sha": p["sha"], "data": data})
        return {
            "volume_id": volume_id,
            "volume_state": vol["state"] if vol else "live",
            "pages": out,
            "logical_bytes": sum(p["bytes_len"] for p in pages),
        }

    # ================================================================ 删除/退库
    def delete_page(self, tenant, volume_id, page_no):
        """从本户一册中移除单页（只改本户）。"""
        p = self.conn.execute(
            "SELECT * FROM pages WHERE tenant=? AND volume_id=? AND page_no=?",
            (tenant, volume_id, page_no)).fetchone()
        if not p:
            raise PlantError("no_such_page", f"{volume_id}:{page_no}")
        refund = self._drop_page(tenant, p)
        return {"volume_id": volume_id, "page_no": page_no,
                "refunded_bytes": refund, "client_balance": self.client_balance(tenant)}

    def delete_volume(self, tenant, volume_id):
        """删册：移除本户该册全部页引用；别家同内容页保持可恢复。"""
        pages = self.conn.execute(
            "SELECT * FROM pages WHERE tenant=? AND volume_id=? ORDER BY page_no",
            (tenant, volume_id)).fetchall()
        if not pages:
            raise PlantError("no_volume", volume_id)
        refund = 0
        for p in pages:
            refund += self._drop_page(tenant, p)
        with self.conn:
            self.conn.execute(
                "UPDATE volumes SET state='disabled' WHERE tenant=? AND volume_id=?",
                (tenant, volume_id))
        return {"volume_id": volume_id, "refunded_bytes": refund,
                "client_balance": self.client_balance(tenant)}

    def _drop_page(self, tenant, p):
        """删除一页：递减逻辑/物理引用，归还逻辑配额，必要时登记回收候选。"""
        sha, blen = p["sha"], p["bytes_len"]
        refund = 0
        with self.conn:
            lb = self.conn.execute(
                "SELECT refcount FROM logical_blobs WHERE tenant=? AND sha=?",
                (tenant, sha)).fetchone()
            if lb:
                new = lb["refcount"] - 1
                if new <= 0:
                    self.conn.execute(
                        "DELETE FROM logical_blobs WHERE tenant=? AND sha=?", (tenant, sha))
                    refund = blen
                else:
                    self.conn.execute(
                        "UPDATE logical_blobs SET refcount=? WHERE tenant=? AND sha=?",
                        (new, tenant, sha))
            if refund:
                q = self._quota(tenant)
                rel = min(refund, q["committed"])
                self.conn.execute(
                    "UPDATE quotas SET committed=committed-? WHERE tenant=?",
                    (rel, tenant))
                self._ledger(tenant, "refund_commit", rel, p["request_id"],
                             f"删除页退还逻辑额度 {rel}")
            # 物理引用
            phys = self.conn.execute(
                "SELECT refcount, bytes_len, state FROM physical_blobs WHERE sha=?",
                (sha,)).fetchone()
            if phys:
                new = phys["refcount"] - 1
                if new <= 0:
                    self.conn.execute(
                        "UPDATE physical_blobs SET refcount=0, state='pending_reclaim'"
                        " WHERE sha=?", (sha,))
                    self.conn.execute(
                        "INSERT INTO reclaim_items(job_id, sha, request_id, tenant,"
                        " released_bytes, state) VALUES(NULL,?,?,?,?, 'candidate')",
                        (sha, p["request_id"], tenant, phys["bytes_len"]))
                else:
                    self.conn.execute(
                        "UPDATE physical_blobs SET refcount=? WHERE sha=?", (new, sha))
            self.conn.execute(
                "DELETE FROM pages WHERE tenant=? AND volume_id=? AND page_no=?",
                (tenant, p["volume_id"], p["page_no"]))
        return refund

    def release_retention(self, tenant, volume_id):
        """撤销保留订约：记 released_at，数据仍在宽限期内可恢复。"""
        now = self.now()
        held = self.conn.execute(
            "SELECT COALESCE(SUM(bytes_len),0) AS s FROM pages WHERE tenant=? AND"
            " volume_id=?", (tenant, volume_id)).fetchone()["s"]
        with self.conn:
            row = self.conn.execute(
                "SELECT * FROM contracts WHERE tenant=? AND volume_id=?",
                (tenant, volume_id)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO contracts(contract_id, tenant, volume_id, bytes_held,"
                    " released_at) VALUES(?,?,?,?,?)",
                    ("ct-" + uuid.uuid4().hex[:10], tenant, volume_id, held, now))
            elif row["released_at"] is None:
                self.conn.execute(
                    "UPDATE contracts SET released_at=? WHERE contract_id=?",
                    (now, row["contract_id"]))
            self.conn.execute(
                "UPDATE volumes SET state='disabled' WHERE tenant=? AND volume_id=?",
                (tenant, volume_id))
            self._ledger(tenant, "release_retention", 0, volume_id,
                         "撤销保留订约（宽限期内仍可恢复）")
        return {"volume_id": volume_id, "released_at": now,
                "grace_deadline": now + self.grace_seconds}

    def settle_release(self, tenant, volume_id):
        """宽限期满后按真实合同结算：释放逻辑配额与物理引用。"""
        contract = self.conn.execute(
            "SELECT * FROM contracts WHERE tenant=? AND volume_id=?",
            (tenant, volume_id)).fetchone()
        if not contract or contract["released_at"] is None:
            raise PlantError("not_released", volume_id)
        if contract["quota_released"]:
            return {"volume_id": volume_id, "already_settled": True,
                    "client_balance": self.client_balance(tenant)}
        deadline = contract["released_at"] + self.grace_seconds
        if self.now() < deadline:
            raise PlantError("grace_not_elapsed", f"wait {deadline - self.now()}s")
        pages = self.conn.execute(
            "SELECT * FROM pages WHERE tenant=? AND volume_id=? ORDER BY page_no",
            (tenant, volume_id)).fetchall()
        refund = 0
        for p in pages:
            refund += self._drop_page(tenant, p)
        with self.conn:
            self.conn.execute(
                "UPDATE contracts SET quota_released=1 WHERE contract_id=?",
                (contract["contract_id"],))
            self._ledger(tenant, "settle_release", 0, volume_id,
                         f"宽限期满按合同退还 {refund}")
        return {"volume_id": volume_id, "refunded_bytes": refund,
                "client_balance": self.client_balance(tenant)}

    def volume_state(self, tenant, volume_id):
        contract = self.conn.execute(
            "SELECT * FROM contracts WHERE tenant=? AND volume_id=?",
            (tenant, volume_id)).fetchone()
        has_pages = self.conn.execute(
            "SELECT 1 FROM pages WHERE tenant=? AND volume_id=?",
            (tenant, volume_id)).fetchone() is not None
        released = contract is not None and contract["released_at"] is not None
        return {
            "tenant": tenant,
            "volume_id": volume_id,
            "contract_released": released,
            "grace_deadline": (contract["released_at"] + self.grace_seconds)
            if released else None,
            "now": self.now(),
            "has_pages": has_pages,
            "restorable": has_pages,
            "client_balance": self.client_balance(tenant),
        }

    def client_volumes(self, tenant):
        out = []
        for r in self.conn.execute(
            "SELECT volume_id, state FROM volumes WHERE tenant=? ORDER BY volume_id",
            (tenant,)):
            n = self.conn.execute(
                "SELECT COUNT(*) AS n FROM pages WHERE tenant=? AND volume_id=?",
                (tenant, r["volume_id"])).fetchone()["n"]
            out.append({"volume_id": r["volume_id"], "state": r["state"], "pages": n})
        return out

    # ================================================================ 回收
    def make_reclaim_job(self, job_id):
        cands = self.conn.execute(
            "SELECT * FROM reclaim_items WHERE state='candidate' ORDER BY id").fetchall()
        if not cands:
            raise PlantError("no_candidates", job_id)
        with self.conn:
            self.conn.execute(
                "INSERT INTO reclaim_jobs(job_id, state, created_at) VALUES(?, 'open', ?)",
                (job_id, self.now()))
            for c in cands:
                self.conn.execute(
                    "UPDATE reclaim_items SET job_id=?, state='pending' WHERE id=?",
                    (job_id, c["id"]))
        return {"job_id": job_id, "items": len(cands)}

    def _free_item(self, item):
        sha = item["sha"]
        phys = self.conn.execute(
            "SELECT state, refcount FROM physical_blobs WHERE sha=?", (sha,)).fetchone()
        if phys is None:
            state = "freed"
        elif phys["refcount"] > 0:
            state = "blocked"
        else:
            with self.conn:
                self.conn.execute("DELETE FROM physical_blobs WHERE sha=?", (sha,))
                self._admin("reclaim", physical_bytes=item["released_bytes"],
                            sha=sha, tenant=item["tenant"],
                            request_id=item["request_id"], note="释放物理实块")
            state = "freed"
        with self.conn:
            self.conn.execute(
                "UPDATE reclaim_items SET state=? WHERE id=?", (state, item["id"]))
        return state

    def reclaim_step(self, job_id, count=1):
        """执行部分回收（可故意不完成，模拟中断）。"""
        items = self.conn.execute(
            "SELECT * FROM reclaim_items WHERE job_id=? AND state='pending'"
            " ORDER BY id LIMIT ?", (job_id, count)).fetchall()
        done = []
        for it in items:
            done.append({"sha": it["sha"], "page_request": it["request_id"],
                         "state": self._free_item(it)})
        remaining = self.conn.execute(
            "SELECT COUNT(*) AS n FROM reclaim_items WHERE job_id=? AND state='pending'",
            (job_id,)).fetchone()["n"]
        if remaining == 0:
            with self.conn:
                self.conn.execute(
                    "UPDATE reclaim_jobs SET state='done', done_at=? WHERE job_id=?",
                    (self.now(), job_id))
        return {"job_id": job_id, "freed": done, "remaining": remaining}

    def reopen_reclaim(self, job_id):
        """重开一次未完成的回收：完成所有 pending 项。"""
        job = self.conn.execute(
            "SELECT * FROM reclaim_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job:
            raise PlantError("unknown_job", job_id)
        res = self.reclaim_step(job_id, count=10 ** 9)
        res["reopened"] = True
        return res

    def reclaim_job_state(self, job_id):
        job = self.conn.execute(
            "SELECT * FROM reclaim_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job:
            raise PlantError("unknown_job", job_id)
        items = [dict(r) for r in self.conn.execute(
            "SELECT sha, request_id, tenant, released_bytes, state FROM reclaim_items"
            " WHERE job_id=? ORDER BY id", (job_id,))]
        return {"job_id": job_id, "state": job["state"], "items": items}

    def pages_available_to_next_tenant(self, request_id):
        """凭请求号查：哪些页已回收、可给下家使用。"""
        rows = self.conn.execute(
            "SELECT sha, released_bytes FROM reclaim_items WHERE request_id=? AND"
            " state='freed' ORDER BY id", (request_id,)).fetchall()
        req = self.conn.execute(
            "SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        evacuated = self.conn.execute(
            "SELECT COUNT(*) AS n FROM reclaim_items WHERE request_id=? AND"
            " state IN ('candidate','pending')", (request_id,)).fetchone()["n"]
        return {
            "request_id": request_id,
            "tenant": req["tenant"] if req else None,
            "volume_id": req["volume_id"] if req else None,
            "freed_pages": [dict(r) for r in rows],
            "freed_count": len(rows),
            "not_yet_freed": evacuated,
        }
