"""
Script para ingestar la estructura real del sistema en Qdrant.
Se apoya en las tablas reales de chat, roles, canales, recursos y login.
"""

import os
import sys
from datetime import datetime

import pymssql

from app.config import settings
from app.system.learning import SistemaAprendizaje
from app.system.schema import Actividad, Canal


def _safe_str(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _first_value(row: dict, *keys, default=None):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def ingestar_sistema_completo():
    """Ingesta la estructura real del sistema: canales, roles, usuarios y chat."""

    sistema = SistemaAprendizaje()

    print("🔄 Ingestando estructura real del sistema...")

    try:
        conn = sistema._connect_sql_with_retry(
            timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
            retries=max(1, settings.DB_INGEST_CONNECT_RETRIES),
            base_delay_seconds=1,
            context="ingesta_sistema",
        )
        cursor = conn.cursor(as_dict=True)

        # 1. Canales reales del sistema
        print("📚 Ingestando canales (SysWorkRoom)...")
        cursor.execute("""
            SELECT TOP 500 *
            FROM dbo.SysWorkRoom
            ORDER BY Name
        """)
        workrooms = cursor.fetchall() or []

        canales = []
        for row in workrooms:
            canal_id = _safe_str(_first_value(row, "IDWorkRoom", "IdWorkRoom", "idWorkRoom"))
            if not canal_id:
                continue

            canal = Canal(
                id=canal_id,
                nombre=_safe_str(_first_value(row, "Name", "DisplayName", "Title"), f"Canal {canal_id[:8]}") or f"Canal {canal_id[:8]}",
                descripcion=_safe_str(_first_value(row, "Description", "Descr", "Notes"), "Canal de trabajo"),
                tipo=_safe_str(_first_value(row, "Kind", "Type"), "workroom"),
                recursos_humanos=[],
                recursos_materiales=[],
                proyectos_activos=[],
            )
            sistema.aprender_canal(canal)
            canales.append(canal)

        print(f"  ✅ {len(canales)} canales ingeridos")

        # 2. Roles del sistema
        print("📚 Ingestando roles (SysRole)...")
        cursor.execute("""
            SELECT TOP 500 *
            FROM dbo.SysRole
            ORDER BY Code
        """)
        roles = cursor.fetchall() or []

        for row in roles:
            rol_code = _safe_str(_first_value(row, "Code", "Name", "RoleCode"), "rol_desconocido")
            rol_texto = " | ".join(
                f"{k}={_safe_str(v)}"
                for k, v in row.items()
                if v not in (None, "")
            )
            actividad = Actividad(
                id=_safe_str(_first_value(row, "IDActivityRole", "IDRole", "IdRole", "ID"), f"role_{rol_code}"),
                recurso_humano_id="sistema",
                canal_id="sysrole",
                tipo="rol_sistema",
                descripcion=f"Rol detectado: {rol_code}. {rol_texto}",
                timestamp=datetime.now(),
                metadatos={"source_table": "SysRole", "role_code": rol_code},
            )
            sistema.aprender_actividad(actividad)

        print(f"  ✅ {len(roles)} roles ingeridos")

        # 3. Usuarios / recursos humanos reales del sistema
        print("📚 Ingestando recursos y logins (SysResources + SysLogin)...")
        cursor.execute("""
            SELECT TOP 1000 *
            FROM dbo.SysResources
            ORDER BY DisplayName
        """)
        resources = cursor.fetchall() or []

        cursor.execute("""
            SELECT TOP 1000 *
            FROM dbo.SysLogin
            ORDER BY Username
        """)
        logins = cursor.fetchall() or []

        login_by_resource = {}
        for login in logins:
            resource_ref = _first_value(login, "IDResource", "ResourceID", "IDSysResource")
            if resource_ref:
                login_by_resource[_safe_str(resource_ref)] = login

        recursos_ingestados = 0
        for row in resources:
            resource_id = _safe_str(_first_value(row, "IDResource", "ID", "ResourceID"))
            if not resource_id:
                continue

            display_name = _safe_str(_first_value(row, "DisplayName", "Name", "FullName"), f"Recurso {resource_id[:8]}")
            login_row = login_by_resource.get(resource_id, {})
            username = _safe_str(_first_value(login_row, "Username", "UserName", "LoginName"), "")

            # Convertimos el recurso en una actividad de aprendizaje para que quede indexado por texto.
            actividad = Actividad(
                id=f"resource_{resource_id}",
                recurso_humano_id=resource_id,
                canal_id=_safe_str(_first_value(row, "IDWorkRoom", "WorkRoomID"), "sysresource"),
                tipo="recurso_sistema",
                descripcion=f"Recurso: {display_name} | Username: {username or 'sin_login'} | Datos: {row}",
                timestamp=datetime.now(),
                metadatos={
                    "source_table": "SysResources",
                    "display_name": display_name,
                    "username": username,
                },
            )
            sistema.aprender_actividad(actividad)
            recursos_ingestados += 1

        print(f"  ✅ {recursos_ingestados} recursos ingeridos")

        # 4. Chat real del sistema, unido a canales y recursos asociados.
        print("📚 Ingestando chat histórico (SysChat)...")
        cursor.execute("""
            SELECT TOP 1000
                c.IDChat2,
                c.Stamp,
                c.RawMessage,
                c.IDWorkRoom,
                wr.Name AS WorkRoomName,
                wr.Description AS WorkRoomDescription,
                wr.Kind AS WorkRoomKind
            FROM dbo.SysChat c
            LEFT JOIN dbo.SysWorkRoom wr ON wr.IDWorkRoom = c.IDWorkRoom
            ORDER BY c.Stamp DESC
        """)
        chats = cursor.fetchall() or []

        chat_relations = {}
        if chats:
            chat_ids = [row.get("IDChat2") for row in chats if row.get("IDChat2")]
            if chat_ids:
                placeholders = ",".join(["%s"] * len(chat_ids))

                cursor.execute(
                    f"""
                    SELECT TOP 2000 *
                    FROM dbo.SysChat2SysResource
                    WHERE IDChat IN ({placeholders})
                    """,
                    tuple(chat_ids),
                )
                chat_resources = cursor.fetchall() or []

                cursor.execute(
                    f"""
                    SELECT TOP 2000 *
                    FROM dbo.SysChat2Record
                    WHERE IDChat IN ({placeholders})
                    """,
                    tuple(chat_ids),
                )
                chat_records = cursor.fetchall() or []

                for row in chat_resources:
                    chat_id = _safe_str(_first_value(row, "IDChat", "IDChat2"))
                    if not chat_id:
                        continue
                    chat_relations.setdefault(chat_id, {"resources": [], "records": []})["resources"].append(row)

                for row in chat_records:
                    chat_id = _safe_str(_first_value(row, "IDChat", "IDChat2"))
                    if not chat_id:
                        continue
                    chat_relations.setdefault(chat_id, {"resources": [], "records": []})["records"].append(row)

        chats_ingestados = 0
        for row in chats:
            chat_id = _safe_str(_first_value(row, "IDChat2", "IDChat", "IdChat"))
            if not chat_id:
                continue

            workroom_id = _safe_str(_first_value(row, "IDWorkRoom"), "canal_general")
            raw_message = _safe_str(_first_value(row, "RawMessage", "Message", "Text"), "")
            workroom_name = _safe_str(_first_value(row, "WorkRoomName", "Name"), "Canal sin nombre")
            workroom_desc = _safe_str(_first_value(row, "WorkRoomDescription", "Description"), "")
            workroom_kind = _safe_str(_first_value(row, "WorkRoomKind", "Kind"), "workroom")

            relaciones = chat_relations.get(chat_id, {})
            recursos_ref = relaciones.get("resources", [])
            registros_ref = relaciones.get("records", [])

            actividad = Actividad(
                id=f"chat_{chat_id}",
                recurso_humano_id=_safe_str(_first_value(row, "IDResource", "IDLogin"), "chat_user"),
                canal_id=workroom_id,
                tipo="chat",
                descripcion=f"Chat: {raw_message} | Canal: {workroom_name} | {workroom_desc} | Recursos: {len(recursos_ref)} | Registros: {len(registros_ref)}",
                timestamp=_first_value(row, "Stamp", default=datetime.now()),
                metadatos={
                    "source_table": "SysChat",
                    "workroom_name": workroom_name,
                    "workroom_kind": workroom_kind,
                    "raw_message": raw_message,
                    "resources_linked": len(recursos_ref),
                    "records_linked": len(registros_ref),
                },
            )
            sistema.aprender_actividad(actividad)
            chats_ingestados += 1

        conn.close()

        resumen = {
            "canales": len(canales),
            "roles": len(roles),
            "recursos": recursos_ingestados,
            "chats": chats_ingestados,
        }

        print("\n✅ ¡Sistema real ingerido correctamente!")
        print(f"   - Canales: {resumen['canales']}")
        print(f"   - Roles: {resumen['roles']}")
        print(f"   - Recursos: {resumen['recursos']}")
        print(f"   - Chats: {resumen['chats']}")
        return resumen

    except Exception as e:
        print(f"❌ Error en la ingesta real: {e}")
        raise


if __name__ == "__main__":
    try:
        ingestar_sistema_completo()
    except Exception:
        sys.exit(1)