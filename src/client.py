"""租户客户端：只在调用时携带本租户身份访问受托进程。

客户端**不接收全局摘要索引**：它拿到的所有回执 / 还原结果都只描述本户，
不包含别家摘要、卷号、引用计数或“别家已有此页”的提示。
"""

from __future__ import annotations

from plant import Plant, PlantError, swatch_bytes


class TenantClient:
    def __init__(self, plant: Plant, tenant: str):
        self._plant = plant
        self.tenant = tenant

    # ---- 账 / 视图（本户） ----
    def balance(self):
        return self._plant.client_balance(self.tenant)

    def ledger(self):
        return self._plant.ledger_rows(self.tenant)

    def volumes(self):
        return self._plant.client_volumes(self.tenant)

    def volume_state(self, volume_id):
        return self._plant.volume_state(self.tenant, volume_id)

    # ---- 预约 / 上载 ----
    def reserve(self, volume_id, specs):
        """specs: [(page_no, spec), ...]；返回本户预约回执。"""
        return self._plant.reserve(self.tenant, volume_id, specs)

    def upload(self, request_id, page_no, spec):
        data = swatch_bytes(spec)
        return self._plant.receive(request_id, page_no, data)

    def upload_all(self, reservation, specs):
        out = []
        for page_no, spec in specs:
            out.append(self.upload(reservation["request_id"], page_no, spec))
        return out

    def retry_pending(self, request_id=None):
        return self._plant.retry_pending(request_id)

    # ---- 还原 / 退库 ----
    def restore(self, volume_id):
        # 只返回本户页字节
        return self._plant.restore(self.tenant, volume_id)

    def delete_page(self, volume_id, page_no):
        return self._plant.delete_page(self.tenant, volume_id, page_no)

    def delete_volume(self, volume_id):
        return self._plant.delete_volume(self.tenant, volume_id)

    def release_retention(self, volume_id):
        return self._plant.release_retention(self.tenant, volume_id)

    def settle_release(self, volume_id):
        return self._plant.settle_release(self.tenant, volume_id)
