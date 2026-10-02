"""备件管理接口：备件余量、状态流转、待采购清单与盘点导出。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.sparepart import (
    ALL_STATUSES,
    PRESENT_FIELDS,
    SparepartService,
)

router = APIRouter(prefix="/api/sparepart", tags=["备件管理"])

service = SparepartService()


def _build_filters(
    keyword: str | None,
    location: str | None,
    status: str | None,
    include_disabled: bool,
) -> dict[str, Any]:
    return {
        "keyword": keyword or None,
        "location": location or None,
        "status": status or None,
        "include_disabled": include_disabled,
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按备件编号或备件名称检索"),
    location: str | None = Query(default=None, description="按选中的存放位置过滤"),
    status: str | None = Query(default=None, description="充足、不足、已停用"),
    include_disabled: bool = Query(default=False, description="是否带出已停用备件"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列表与盘点导出共用同一套筛选与取数口径；默认不返回已停用备件。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"备件状态仅支持：{'、'.join(ALL_STATUSES)}")
    items, total = service.list_entries(
        **_build_filters(keyword, location, status, include_disabled),
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/locations")
def list_locations() -> dict[str, list[str]]:
    """可供筛选的存放位置列表。"""
    return {"items": service.locations()}


@router.get("/purchase-list")
def purchase_list(
    keyword: str | None = Query(default=None, description="按备件编号或备件名称检索"),
    location: str | None = Query(default=None, description="按选中的存放位置过滤"),
) -> dict[str, Any]:
    """待采购清单：当前余量低于最低保有量且未停用的备件。"""
    items = service.purchase_list(keyword=keyword, location=location)
    return {"total": len(items), "items": items}


@router.get("/stats")
def stats() -> dict[str, int]:
    """列表页统计卡片数据：在用备件种类、不足/待采购数量。"""
    return service.stats()


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None),
    location: str | None = Query(default=None, description="按当前选中的存放位置导出"),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """盘点表导出：与页面当前筛选条件一致，含适用设备列；已停用备件不进盘点。

    字段取不到数时后端会重试，重试仍取不到的记录列入 incomplete 并从导出中剔除，
    返回的 total 与 items 条数保持一致，前端按 incomplete 提示用户。
    """
    items, incomplete, disabled_excluded = service.export_rows(
        keyword=keyword, location=location, status=status
    )
    return {
        "module": "sparepart",
        "columns": PRESENT_FIELDS,
        "total": len(items),
        "items": items,
        "incomplete": incomplete,
        "disabled_excluded": disabled_excluded,
    }

@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条备件明细；与列表走同一取数口径，余量必然一致。"""
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
    """办理领用/采购入仓时同步扣减/增加当前余量并即时重算状态；停用备件退出盘点。"""
    action = str(payload.values.get("action") or "").strip()
    quantity = payload.values.get("quantity")
    entry, message = service.run_action(entry_id, action, quantity)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


