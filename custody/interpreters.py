"""两版解释字典（解释器）。

旧版 v1：空白期限解释成「签发当日参照的合同期限」。
新版 v2：直接使用显式日期/期限；空白期限拒绝推断（blank-unresolved）。
两版对显式日期、显式期限、转交起算的算术完全一致，差异只在空白期限。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from .model import Contract, CustodyOrder, Handover, Obligation


class _BaseInterpreter:
    version: str

    # ------------------------------------------------------------ 起算点
    def _term_start(self, order: CustodyOrder,
                    handovers: list[Handover]) -> tuple[Optional[date], Optional[str]]:
        if order.start_trigger == "issue":
            return order.issued_on, None
        transfer = next((h for h in reversed(handovers) if h.kind == "transfer"), None)
        if transfer is None:
            return None, "条款约定遇转交才起算，尚未发生转交"
        return transfer.on, None

    def evaluate(self, order: CustodyOrder, contract: Contract,
                 handovers: list[Handover], as_of) -> Obligation:
        start, pending_note = self._term_start(order, handovers)

        # 1) 显式到期日：两版都直接使用该日期
        if order.until_date is not None:
            return Obligation(
                order.order_id, self.version, start, order.until_date,
                "determined", "显式到期日，直接采用")

        # 2) 显式期限：起算点 + 期限天数
        if order.term_days is not None:
            if start is None:
                return Obligation(order.order_id, self.version, None, None,
                                  "pending-transfer", pending_note)
            end = start + timedelta(days=order.term_days)
            return Obligation(order.order_id, self.version, start, end,
                              "determined", f"显式期限 {order.term_days} 天")

        # 3) 空白期限：两版差异点
        return self._blank(order, contract, start, pending_note)

    def _blank(self, order, contract, start, pending_note) -> Obligation:
        raise NotImplementedError


class LegacyInterpreter(_BaseInterpreter):
    """旧版字典：空白期限 = 签发当日合同的默认期限。"""
    version = "v1"

    def _blank(self, order, contract, start, pending_note) -> Obligation:
        if contract.default_term_days is None:
            return Obligation(order.order_id, self.version, start, None,
                              "blank-unresolved", "合同未给默认期限，旧版亦无法确定")
        if start is None:
            # 转交才起算且转交未发生：规则可知但日期不可知
            return Obligation(
                order.order_id, self.version, None, None, "pending-transfer",
                f"{pending_note}；起算后按合同默认期限 {contract.default_term_days} 天")
        end = start + timedelta(days=contract.default_term_days)
        return Obligation(
            order.order_id, self.version, start, end, "determined",
            f"空白期限按签发参照合同（{contract.contract_id} v{contract.version}）"
            f"补 {contract.default_term_days} 天")


class NewInterpreter(_BaseInterpreter):
    """新版字典：只用日期，空白一律不推断。"""
    version = "v2"

    def _blank(self, order, contract, start, pending_note) -> Obligation:
        return Obligation(
            order.order_id, self.version, start, None, "blank-unresolved",
            "新版字典不推断空白期限，须显式日期或客户改约")
