"""备件管理业务规则：余量、状态、低保有量与盘点口径统一在这里计算。

列表、详情、待采购清单与盘点导出都走 :meth:`SparepartService._present` 这一份取数逻辑，
保证同一备件编号在任何页面看到的当前余量、备件状态完全一致；出入库动作只改库存，
状态与标记一律由库存和最低保有量即时推导，不再单独维护一份会过期的文本状态。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.store import store

MODULE = "sparepart"
REQUIRED_FIELDS = ["备件编号", "备件名称", "规格型号"]
OPTIONAL_TEXT_FIELDS = ["适用设备", "存放位置"]

# 对外展示字段（顺序即列表与盘点表列顺序），适用设备必须随盘点表带出
PRESENT_FIELDS = [
    "备件编号",
    "备件名称",
    "规格型号",
    "适用设备",
    "存放位置",
    "最低保有量",
    "当前余量",
    "备件状态",
]

STATUS_OK = "充足"
STATUS_LOW = "不足"
STATUS_DISABLED = "已停用"
ALL_STATUSES = [STATUS_OK, STATUS_LOW, STATUS_DISABLED]

ACTION_ISSUE = "办理领用"
ACTION_RECEIVE = "采购入仓"
ACTION_DISABLE = "停用备件"
ACTIONS = [ACTION_ISSUE, ACTION_RECEIVE, ACTION_DISABLE]


def _to_int(value: Any, default: int = 0) -> int:
    """把种子数据或入参里的数量转成非负整数；无法解析时按默认值处理。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return max(int(float(text)), 0)
    except ValueError:
        return default


def with_retry(
    fetch: Callable[[], Any],
    *,
    attempts: int = 3,
    should_retry: Callable[[Any], bool] | None = None,
    backoff: float = 0.1,
) -> Any:
    """取不到数时按次数重试。

    盘点导出要汇总的字段来自不同来源，瞬时取不到时由这里统一重试；
    重试次数耗尽仍失败则抛出最后一次异常，由调用方决定跳过并登记，
    而不是把 "—" 之类的占位符塞进导出结果。
    """
    last_error: Exception | None = None
    for index in range(attempts):
        try:
            result = fetch()
            if should_retry is None or not should_retry(result):
                return result
        except (KeyError, TypeError, ValueError) as error:
            last_error = error
        if index < attempts - 1 and backoff:
            time.sleep(backoff * (index + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("多次重试后仍未取到数据")


class SparepartService:
    # ---------- 统一取数口径 ----------

    def _is_disabled(self, row: dict[str, Any]) -> bool:
        return bool(row.get("disabled")) or row.get("status") == STATUS_DISABLED

    def _stock(self, row: dict[str, Any]) -> int:
        return _to_int(row.get("当前余量"))

    def _min_stock(self, row: dict[str, Any]) -> int:
        return _to_int(row.get("最低保有量"))

    def _below_min(self, row: dict[str, Any]) -> bool:
        return not self._is_disabled(row) and self._stock(row) < self._min_stock(row)

    def _status(self, row: dict[str, Any]) -> str:
        if self._is_disabled(row):
            return STATUS_DISABLED
        return STATUS_LOW if self._below_min(row) else STATUS_OK

    def _present(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表、详情、导出共用的唯一取数出口。"""
        stock = self._stock(row)
        min_stock = self._min_stock(row)
        below_min = self._below_min(row)
        return {
            "id": int(row.get("id", 0)),
            "备件编号": str(row.get("备件编号") or ""),
            "备件名称": str(row.get("备件名称") or ""),
            "规格型号": str(row.get("规格型号") or ""),
            "适用设备": str(row.get("适用设备") or ""),
            "存放位置": str(row.get("存放位置") or ""),
            "最低保有量": min_stock,
            "当前余量": stock,
            "备件状态": self._status(row),
            "below_min": below_min,
            "建议采购量": max(min_stock - stock, 0) if below_min else 0,
        }

    # ---------- 筛选 ----------

    def _query(
        self,
        *,
        keyword: str | None = None,
        location: str | None = None,
        status: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            text = keyword.strip()
            rows = [
                row
                for row in rows
                if text in str(row.get("备件编号", "")) or text in str(row.get("备件名称", ""))
            ]
        if location:
            place = location.strip()
            rows = [row for row in rows if place in str(row.get("存放位置", ""))]
        if status:
            rows = [row for row in rows if self._status(row) == status]
        elif not include_disabled:
            rows = [row for row in rows if not self._is_disabled(row)]
        return rows

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        location: str | None = None,
        status: str | None = None,
        include_disabled: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._query(
            keyword=keyword,
            location=location,
            status=status,
            include_disabled=include_disabled,
        )
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row) if row is not None else None

    def purchase_list(
        self,
        *,
        keyword: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        """待采购清单：未停用且当前余量低于最低保有量的备件，按缺口大小倒序。"""
        rows = self._query(keyword=keyword, location=location, include_disabled=False)
        short_rows = [row for row in rows if self._below_min(row)]
        short_rows.sort(key=lambda row: self._min_stock(row) - self._stock(row), reverse=True)
        return [self._present(row) for row in short_rows]

    def stats(self) -> dict[str, int]:
        rows = store.rows(MODULE)
        active = [row for row in rows if not self._is_disabled(row)]
        low = [row for row in active if self._below_min(row)]
        return {
            "备件种类": len(active),
            "不足备件": len(low),
            "待采购备件": len(low),
        }

    def locations(self) -> list[str]:
        seen = sorted({
            str(row.get("存放位置") or "").strip()
            for row in store.rows(MODULE)
            if str(row.get("存放位置") or "").strip()
        })
        return seen

    # ---------- 登记与出入库 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS:
            entry[field] = str(values.get(field)).strip()
        for field in OPTIONAL_TEXT_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["最低保有量"] = _to_int(values.get("最低保有量"))
        entry["当前余量"] = _to_int(values.get("当前余量"))
        entry["disabled"] = False
        self._sync_flags(entry)
        rows.append(entry)
        return self._present(entry), []

    def _sync_flags(self, row: dict[str, Any]) -> None:
        """库存变动后把派生状态同步回行数据，供概览统计等复用。"""
        row["status"] = self._status(row)
        row["备件状态"] = self._status(row)
        row["pending"] = self._below_min(row)
        row["abnormal"] = self._is_disabled(row)

    def run_action(
        self, entry_id: int, action: str, quantity: Any = None
    ) -> tuple[dict[str, Any] | None, str]:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None, f"备件 {entry_id} 不存在或已归档"
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于备件管理可执行范围"

        if action == ACTION_DISABLE:
            row["disabled"] = True
            self._sync_flags(row)
            return self._present(row), f"备件（{row.get('备件编号')}）已停用，不再参与盘点"

        amount = _to_int(quantity, default=-1)
        if amount <= 0:
            return None, "请输入大于 0 的出入库数量"
        if self._is_disabled(row):
            return None, "备件已停用，不能办理出入库"

        if action == ACTION_ISSUE:
            stock = self._stock(row)
            if amount > stock:
                return None, f"领用数量超过当前余量（当前余量 {stock}）"
            row["当前余量"] = stock - amount
        else:
            row["当前余量"] = self._stock(row) + amount

        self._sync_flags(row)
        presented = self._present(row)
        if presented["below_min"]:
            return presented, (
                f"备件已{action}，当前余量 {presented['当前余量']}，"
                f"低于最低保有量 {presented['最低保有量']}，已列入待采购清单"
            )
        return presented, f"备件已{action}，当前余量 {presented['当前余量']}"

    # ---------- 盘点导出 ----------

    def export_rows(
        self,
        *,
        keyword: str | None = None,
        location: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """按当前选中的筛选条件取盘点数据，与列表页同口径（含停用件一律剔除）。

        每条记录的字段都经重试取数；重试后仍取不到的记录不进导出，
        登记到 incomplete 里返回，避免导出文件中出现只剩占位符的行。
        """
        # 盘点不包含已停用备件，即使筛选里显式选了已停用也剔除
        rows = self._query(keyword=keyword, location=location, status=None, include_disabled=False)
        if status and status != STATUS_DISABLED:
            rows = [row for row in rows if self._status(row) == status]
        disabled_filtered = len(
            self._query(keyword=keyword, location=location, status=status, include_disabled=True)
        ) - len(
            self._query(keyword=keyword, location=location, status=status, include_disabled=False)
        )

        items: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        for row in rows:
            try:
                presented = with_retry(
                    lambda row=row: self._require_complete(row),
                    should_retry=lambda value: value is None,
                )
            except (KeyError, TypeError, ValueError, RuntimeError):
                presented = None
            if presented is None:
                present = self._present(row)
                missing = self._missing_fields(present)
                incomplete.append(
                    {"id": present["id"], "备件编号": present["备件编号"], "缺少字段": missing}
                )
                continue
            items.append(presented)
        return items, incomplete, disabled_filtered

    def _missing_fields(self, presented: dict[str, Any]) -> list[str]:
        return [field for field in PRESENT_FIELDS if presented.get(field) in (None, "")]

    def _require_complete(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """按盘点要求取一条完整记录；任一展示字段取不到数则返回 None 交给重试。"""
        presented = self._present(row)
        if self._missing_fields(presented):
            return None
        return {field: presented[field] for field in PRESENT_FIELDS}
