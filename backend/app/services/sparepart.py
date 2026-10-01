"""备件管理业务规则：出入库、余量推导与盘点取数都收在这里。

列表、详情、待采购清单、盘点导出共用同一套过滤与状态推导，不再各走一套取数，
保证同一编号在任何入口看到的余量都一致。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "sparepart"
REQUIRED_FIELDS = ["备件编号", "备件名称", "规格型号"]
LIST_FIELDS = ["备件编号", "备件名称", "规格型号", "适用设备", "存放位置", "最低保有量", "当前余量", "备件状态"]
STATUS_ORDER = ["充足", "不足", "待采购", "已停用"]
ACTIONS = ["办理领用", "采购入仓", "停用备件"]
DISABLED_STATUS = "已停用"


def _to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


class SparepartService:
    def _sync_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """按余量推导状态：已停用不动；余量见底→待采购；低于最低保有量→不足；其余→充足。

        余量与状态始终一起落库，列表页和详情页读到的才是同一个数。
        """
        if entry.get("status") == DISABLED_STATUS:
            entry["low_stock"] = False
            return entry
        stock = _to_int(entry.get("当前余量"))
        minimum = _to_int(entry.get("最低保有量"))
        if stock <= 0:
            status = "待采购"
        elif stock < minimum:
            status = "不足"
        else:
            status = "充足"
        entry["status"] = status
        entry["备件状态"] = status
        entry["low_stock"] = stock < minimum
        return entry

    def _filtered(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [self._sync_entry(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("备件编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if location:
            rows = [row for row in rows if row.get("存放位置") == location]
        return rows

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        location: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered(keyword=keyword, status=status, location=location)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._sync_entry(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: str(values.get(field) or "").strip() for field in REQUIRED_FIELDS})
        entry["适用设备"] = str(values.get("适用设备") or "").strip()
        entry["存放位置"] = str(values.get("存放位置") or "").strip()
        entry["最低保有量"] = _to_int(values.get("最低保有量"))
        entry["当前余量"] = _to_int(values.get("当前余量"))
        entry["pending"] = True
        entry["abnormal"] = False
        self._sync_entry(entry)
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        quantity: Any = 1,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"备件 {entry_id} 不存在或已归档"
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于备件管理可执行范围"
        if action == "停用备件":
            entry["status"] = DISABLED_STATUS
            entry["备件状态"] = DISABLED_STATUS
            entry["pending"] = False
            entry["abnormal"] = False
            entry["low_stock"] = False
            return entry, "备件已停用，不再进入盘点与待采购清单"
        if entry.get("status") == DISABLED_STATUS:
            return None, "备件已停用，不能再办理出入库"
        amount = _to_int(quantity)
        if amount <= 0:
            return None, "出入库数量必须是大于 0 的整数"
        stock = _to_int(entry.get("当前余量"))
        if action == "办理领用":
            if amount > stock:
                return None, f"当前余量 {stock} 件，不够领用 {amount} 件"
            entry["当前余量"] = stock - amount
        else:  # 采购入仓
            entry["当前余量"] = stock + amount
        self._sync_entry(entry)
        return entry, f"备件已{action} {amount} 件，当前余量 {entry['当前余量']} 件"

    def to_purchase(self) -> list[dict[str, Any]]:
        """待采购清单：未停用且余量低于最低保有量的备件，附建议采购量。"""
        items = []
        for row in self._filtered():
            if not row.get("low_stock"):
                continue
            item = {field: row.get(field) for field in LIST_FIELDS}
            item["id"] = row.get("id")
            item["建议采购量"] = max(_to_int(row.get("最低保有量")) - _to_int(row.get("当前余量")), 1)
            items.append(item)
        return items

    def export_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        location: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """盘点导出：与列表同一套过滤口径，但已停用的备件不进盘点表。

        每行按 LIST_FIELDS 投影，保证适用设备等字段齐全；返回 (条目, 被排除的停用条数)。
        """
        rows = self._filtered(keyword=keyword, status=status, location=location)
        active = [row for row in rows if row.get("status") != DISABLED_STATUS]
        items = [{field: row.get(field) for field in LIST_FIELDS} for row in active]
        return items, len(rows) - len(active)
