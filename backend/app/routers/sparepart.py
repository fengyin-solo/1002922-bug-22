"""备件管理接口：维护备件，覆盖办理领用、采购入仓、停用备件等动作。

注意路由顺序：/to-purchase、/export 必须注册在 /{entry_id} 之前，
否则会被 {entry_id} 捕获并因无法转成整数而 422，导出永远取不到数。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.sparepart import LIST_FIELDS, STATUS_ORDER, SparepartService

router = APIRouter(prefix="/api/sparepart", tags=["备件管理"])

service = SparepartService()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按备件编号检索"),
    status: str | None = Query(default=None, description="充足、不足、待采购、已停用"),
    location: str | None = Query(default=None, description="按存放位置过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按备件编号、状态与存放位置过滤备件列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, location=location, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/to-purchase")
def to_purchase() -> dict[str, Any]:
    """待采购清单：未停用且余量低于最低保有量的备件，附建议采购量。"""
    items = service.to_purchase()
    return {"module": "sparepart", "total": len(items), "items": items}


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按备件编号检索"),
    status: str | None = Query(default=None, description="充足、不足、待采购、已停用"),
    location: str | None = Query(default=None, description="按存放位置过滤"),
) -> dict[str, Any]:
    """导出盘点表：与列表同一套过滤口径，已停用的备件不进盘点，字段按 LIST_FIELDS 给齐。"""
    items, excluded = service.export_entries(keyword=keyword, status=status, location=location)
    return {
        "module": "sparepart",
        "fields": LIST_FIELDS,
        "total": len(items),
        "excluded": excluded,
        "items": items,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条备件明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"备件 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条备件，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="备件已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条备件执行办理领用、采购入仓、停用备件；出入库会立即改写当前余量。"""
    action = str(payload.values.get("action") or "").strip()
    quantity = payload.values.get("quantity", 1)
    entry, message = service.run_action(entry_id, action, quantity)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
