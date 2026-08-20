from __future__ import annotations

from typing import Any
import pymssql

from app.config import settings


QUERY = '''
SELECT TOP (%s)
 c.IDChat2, c.IDSender, sender.IDResource AS IDSenderResource,
 c.RawMessage, c.Stamp, c.Kind, c.Importance, c.Status,
 cw.IDWorkRoom, w.Code AS WorkRoomCode, w.Name AS WorkRoomName,
 l.FullName, r.DisplayName AS ResourceName,
 CASE WHEN agent.IDAgentResource IS NULL THEN 0 ELSE 1 END AS GeneratedByIA
FROM dbo.SysChat c WITH (NOLOCK)
OUTER APPLY (
 SELECT TOP (1) rel.IDResource
 FROM dbo.SysChat2SysResource rel WITH (NOLOCK)
 WHERE rel.IDChat=c.IDChat2 AND rel.IDLogin=c.IDSender
 ORDER BY rel.IDResource
) sender
OUTER APPLY (
 SELECT TOP (1) rel.IDWorkRoom
 FROM dbo.SysChat2SysWorkRoom rel WITH (NOLOCK)
 WHERE rel.IDChat2=c.IDChat2
 ORDER BY rel.IDWorkRoom
) cw
LEFT JOIN dbo.SysWorkRoom w WITH (NOLOCK) ON w.IDWorkRoom=cw.IDWorkRoom
LEFT JOIN dbo.SysResources r WITH (NOLOCK) ON r.ResourceId=sender.IDResource
LEFT JOIN dbo.SysLogin l WITH (NOLOCK) ON l.IDLogin=c.IDSender
LEFT JOIN dbo.SysResource2Agent agent WITH (NOLOCK)
 ON agent.IDAgentResource=sender.IDResource AND agent.Active=1
WHERE c.IDChat2 > %s AND c.RawMessage IS NOT NULL AND LTRIM(RTRIM(c.RawMessage))<>''
ORDER BY c.IDChat2 ASC
'''


def extract_batch(last_id_chat2: int, batch_size: int) -> list[dict[str, Any]]:
    with pymssql.connect(
        **settings.sql_server_connection_options(), user=settings.SQL_SERVER_USER,
        password=settings.SQL_SERVER_PASSWORD, database=settings.SQL_SERVER_DB,
        login_timeout=settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS,
        timeout=settings.DB_INGEST_QUERY_TIMEOUT_SECONDS, as_dict=True,
    ) as conn, conn.cursor(as_dict=True) as cur:
        cur.execute(QUERY, (batch_size, last_id_chat2))
        return [dict(row) for row in (cur.fetchall() or [])]
