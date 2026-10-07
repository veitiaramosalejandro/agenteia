from pydantic import BaseModel
from typing import Any


class SolidSETCatalogPage(BaseModel):
    instanceCode: str
    rows: list[dict[str, Any]]
    rowCount: int
    offset: int
    limit: int
    hasMore: bool
    nextOffset: int | None = None


class _BaseSyncResponse(BaseModel):
    status: str
    sourceRows: int
    synchronized: int
    skipped: int


class SysResourceIAIngestResponse(_BaseSyncResponse):
    inserted: int
    updated: int


class SysChatIAResourceIngestResponse(_BaseSyncResponse):
    inserted: int
    existing: int


class SysAgentIAScopeIngestResponse(_BaseSyncResponse):
    pass


class SysWorkRoomIngestResponse(_BaseSyncResponse):
    inserted: int
    updated: int


class SysLoginIngestResponse(_BaseSyncResponse):
    inserted: int
    updated: int


class SysAgentIAModelSyncResponse(_BaseSyncResponse):
    instanceCode: str
    inserted: int
    promoted: int
    existing: int
    skippedNoProvider: int
