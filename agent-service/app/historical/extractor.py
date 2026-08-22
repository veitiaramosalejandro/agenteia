from __future__ import annotations

from typing import Any
from app.config import settings
from app.connectors.solidset_sql import (
    connect as connect_solidset_sql,
    open_current_connection,
)


QUERY = '''
SELECT TOP (%s)
 c.IDChat2, c.IDSender, sender.IDResource AS IDSenderResource,
 c.RawMessage, c.Stamp, c.Kind, c.Importance, c.Status,
 cw.IDWorkRoom, w.Code AS WorkRoomCode, w.Name AS WorkRoomName, w.Kind AS WorkRoomKind,
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


AGENT_CHAT_QUERY = '''
SELECT TOP (%s)
 c.IDChat2, c.IDSender, sender.IDResource AS IDSenderResource,
 c.RawMessage, c.Stamp, c.Kind, c.Importance, c.Status,
 cw.IDWorkRoom, w.Code AS WorkRoomCode, w.Name AS WorkRoomName, w.Kind AS WorkRoomKind,
 l.FullName, r.DisplayName AS ResourceName,
 CASE WHEN agent.IDAgentResource IS NULL THEN 0 ELSE 1 END AS GeneratedByIA,
 __MEETING_EXPRESSION__ AS IDMeeting
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
  AND (
    sender.IDResource=%s
    OR EXISTS (
      SELECT 1 FROM dbo.SysChat2SysResource participant WITH (NOLOCK)
      WHERE participant.IDChat=c.IDChat2 AND participant.IDResource=%s
    )
    %s
  )
ORDER BY c.IDChat2 ASC
'''


def extract_batch(last_id_chat2: int, batch_size: int) -> list[dict[str, Any]]:
    with open_current_connection(as_dict=True) as conn, conn.cursor(as_dict=True) as cur:
        cur.execute(QUERY, (batch_size, last_id_chat2))
        return [dict(row) for row in (cur.fetchall() or [])]


def extract_agent_chat_batch(
    last_id_chat2: int,
    batch_size: int,
    resource_id: str,
    workroom_ids: list[str],
    instance: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reads only chats the active agent owner authored, received or may access."""
    valid_rooms = [str(value) for value in workroom_ids if value]
    room_clause = ""
    parameters: list[Any] = [batch_size, last_id_chat2, resource_id, resource_id]
    if valid_rooms:
        placeholders = ",".join("%s" for _ in valid_rooms)
        room_clause = f" OR cw.IDWorkRoom IN ({placeholders})"
        parameters.extend(valid_rooms)
    with connect_solidset_sql(instance, as_dict=True) as conn, conn.cursor(as_dict=True) as cur:
        cur.execute('''SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
          WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='SysChat' AND COLUMN_NAME='IDMeeting' ''')
        meeting_column = cur.fetchone()
        meeting_expression = "c.IDMeeting" if meeting_column else "NULL"
        query = (
            AGENT_CHAT_QUERY.replace("__MEETING_EXPRESSION__", meeting_expression)
            % ("%s", "%s", "%s", "%s", room_clause)
        )
        cur.execute(query, tuple(parameters))
        return [dict(row) for row in (cur.fetchall() or [])]


def _quoted(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    return f"[{identifier}]"


def extract_agent_task_batch(
    last_id_task: int,
    batch_size: int,
    resource_id: str,
    instance: dict[str, Any],
) -> list[dict[str, Any]]:
    """Discovers the installed SysTask schema and reads only resource-related tasks."""
    with connect_solidset_sql(instance, as_dict=True) as conn, conn.cursor(as_dict=True) as cur:
        cur.execute('''SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
          WHERE TABLE_SCHEMA='dbo' AND (TABLE_NAME='SysTask' OR TABLE_NAME LIKE '%Task%')''')
        schema: dict[str, dict[str, str]] = {}
        schema_types: dict[str, dict[str, str]] = {}
        for item in cur.fetchall() or []:
            table = str(item["TABLE_NAME"])
            column = str(item["COLUMN_NAME"])
            schema.setdefault(table, {})[column.lower()] = column
            schema_types.setdefault(table, {})[column.lower()] = str(item.get("DATA_TYPE") or "").lower()
        task_columns = schema.get("SysTask") or {}
        task_types = schema_types.get("SysTask") or {}
        id_column = task_columns.get("idtask")
        if not id_column:
            print("ℹ️ Ingestão histórica SysTask omitida: IDTask não encontrado", flush=True)
            return []
        if task_types.get("idtask") not in {"tinyint", "smallint", "int", "bigint", "numeric", "decimal"}:
            print("ℹ️ Ingestão histórica SysTask omitida: IDTask não é incremental numérico", flush=True)
            return []

        resource_candidates = (
            "idresource", "idassignedresource", "idownerresource",
            "idcreatorresource", "idresponsibleresource", "resourceid",
        )
        login_candidates = ("idlogin", "idassignedlogin", "idownerlogin", "idcreatorlogin")
        predicates: list[str] = []
        params: list[Any] = [batch_size, last_id_task]
        for key in resource_candidates:
            if key in task_columns and task_types.get(key) == "uniqueidentifier":
                predicates.append(f"t.{_quoted(task_columns[key])}=%s")
                params.append(resource_id)
        for key in login_candidates:
            if key in task_columns and task_types.get(key) == "uniqueidentifier":
                predicates.append(
                    f"EXISTS (SELECT 1 FROM dbo.SysLogin login WITH (NOLOCK) "
                    f"WHERE login.IDLogin=t.{_quoted(task_columns[key])} "
                    "AND login.LastIDResource=%s)"
                )
                params.append(resource_id)

        for table_name, columns in schema.items():
            if table_name == "SysTask" or "idtask" not in columns:
                continue
            relation_predicates: list[str] = []
            relation_params: list[Any] = []
            relation_types = schema_types.get(table_name) or {}
            for key in resource_candidates:
                if key in columns and relation_types.get(key) == "uniqueidentifier":
                    relation_predicates.append(f"rel.{_quoted(columns[key])}=%s")
                    relation_params.append(resource_id)
            for key in login_candidates:
                if key in columns and relation_types.get(key) == "uniqueidentifier":
                    relation_predicates.append(
                        f"EXISTS (SELECT 1 FROM dbo.SysLogin login WITH (NOLOCK) "
                        f"WHERE login.IDLogin=rel.{_quoted(columns[key])} "
                        "AND login.LastIDResource=%s)"
                    )
                    relation_params.append(resource_id)
            if relation_predicates:
                predicates.append(
                    f"EXISTS (SELECT 1 FROM dbo.{_quoted(table_name)} rel WITH (NOLOCK) "
                    f"WHERE rel.{_quoted(columns['idtask'])}=t.{_quoted(id_column)} "
                    f"AND ({' OR '.join(relation_predicates)}))"
                )
                params.extend(relation_params)
        if not predicates:
            print("ℹ️ Ingestão histórica SysTask omitida: relação com recurso não encontrada", flush=True)
            return []

        text_columns = [
            task_columns[key] for key in (
                "code", "title", "name", "description", "rawmessage", "comments", "status"
            ) if key in task_columns
        ]
        text_expression = " + ' | ' + ".join(
            f"COALESCE(CONVERT(nvarchar(max), t.{_quoted(column)}), '')"
            for column in text_columns
        ) or f"CONVERT(nvarchar(max), t.{_quoted(id_column)})"
        stamp_column = next((task_columns[key] for key in (
            "updatedat", "lastmodified", "stamp", "createdat", "creationdate"
        ) if key in task_columns), None)
        room_column = next((task_columns[key] for key in (
            "idworkroom", "idchannel"
        ) if key in task_columns), None)
        query = f'''SELECT TOP (%s)
          t.{_quoted(id_column)} AS IDTask,
          {text_expression} AS RawMessage,
          {f't.{_quoted(stamp_column)}' if stamp_column else 'NULL'} AS Stamp,
          {f't.{_quoted(room_column)}' if room_column else 'NULL'} AS IDWorkRoom
          FROM dbo.SysTask t WITH (NOLOCK)
          WHERE t.{_quoted(id_column)} > %s AND ({' OR '.join(predicates)})
          ORDER BY t.{_quoted(id_column)} ASC'''
        cur.execute(query, tuple(params))
        return [dict(row) for row in (cur.fetchall() or [])]
