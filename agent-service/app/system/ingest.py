"""
Script para ingestar la estructura real del sistema en Qdrant.
Se apoya en las tablas reales de chat, roles, canales, recursos y login.
"""

import os
import sys
from contextlib import suppress
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
        with suppress(Exception):
            cursor.execute("SET LOCK_TIMEOUT 5000")

        # 1. Canales reales del sistema
        print("📚 Ingestando canales (SysWorkRoom)...")
        cursor.execute("""
            SELECT TOP 500 *
            FROM dbo.SysWorkRoom
            ORDER BY Name
        """)
        workrooms = cursor.fetchall() or []

        canales = []
        canales_indexados = 0
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
            if sistema.aprender_canal(canal):
                canales_indexados += 1
            canales.append(canal)

        print(f"  ✅ {len(canales)} canales ingeridos ({canales_indexados} indexados en vector DB)")

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

        # 3. Usuarios asociados a recursos (consulta real compartida)
        print("📚 Ingestando usuarios asociados a recursos (SysLogin + SysResources)...")
        
        # Paso 3.1: Obtener todos los usuarios activos
        cursor.execute("""
            SELECT IDLogin, FullName, Username
            FROM dbo.SysLogin
            WHERE Active = 1
            ORDER BY FullName ASC
        """)
        all_users = cursor.fetchall() or []

        # Paso 3.2: Obtener todos los mapeos de recursos para esos usuarios
        cursor.execute("""
            SELECT
                sl2r.IDLogin,
                sr.ResourceId,
                sr.DisplayName,
                sr.ResourceName
            FROM dbo.SysLogin2SysResource sl2r
            INNER JOIN dbo.SysResources sr ON sl2r.IDResource = sr.ResourceId
            WHERE sl2r.Active = 1
        """)
        resource_mappings = cursor.fetchall() or []

        # Agrupar recursos por usuario en Python
        resources_by_user = {}
        for mapping in resource_mappings:
            user_id = _safe_str(mapping.get("IDLogin"))
            if user_id:
                resources_by_user.setdefault(user_id, []).append(mapping)

        resource_profiles = {}
        for user_row in all_users:
            user_id = _safe_str(user_row.get("IDLogin"))
            if not user_id:
                continue

            user_resources = resources_by_user.get(user_id, [])
            resource_names = sorted([_safe_str(r.get("DisplayName")) for r in user_resources])
            
            # Usamos el ID de usuario como ID principal para el perfil de recurso humano
            resource_profiles[user_id] = {
                "display_name": _safe_str(user_row.get("FullName")) or _safe_str(user_row.get("Username")),
                "resource_names": ", ".join(resource_names),
                "full_name": _safe_str(user_row.get("FullName")),
            }

        recursos_ingestados = 0
        for user_id, profile in resource_profiles.items():
            actividad = Actividad(
                id=f"resource_{user_id}",
                recurso_humano_id=user_id, # El ID del recurso humano es el ID del Login
                canal_id="sysresource",
                tipo="recurso_sistema",
                descripcion=(
                    f"Recurso: {profile['display_name']} | "
                    f"Recursos asociados: {profile['resource_names'] or 'ninguno'} | "
                    f"Usuario: {profile['full_name'] or 'sin_fullname'}"
                ),
                timestamp=datetime.now(),
                metadatos={
                    "source_table": "SysResources",
                    "display_name": profile["display_name"],
                    "resource_names": profile["resource_names"],
                    "full_name": profile["full_name"],
                },
            )
            sistema.aprender_actividad(actividad)
            recursos_ingestados += 1

        print(f"  ✅ {recursos_ingestados} recursos ingeridos")

        # 4. Canales en los que cada recurso tiene permiso (consulta real compartida)
        print("📚 Ingestando permisos de canal por recurso (SysWorkRoom + SysWorkRoomResource + SysResources)...")
        cursor.execute("""
            SELECT TOP 5000
                wrr.IDResource,
                wr.IDWorkRoom,
                wr.Name as WorkRoomName,
                wr.Kind as WorkRoomKind
            FROM dbo.SysWorkRoom wr
            INNER JOIN dbo.SysWorkRoomResource wrr ON wr.IDWorkRoom = wrr.IDWorkRoom
            ORDER BY wrr.IDResource, wr.Name
        """)
        channels_permissions = cursor.fetchall() or []

        # La consulta anterior fue reemplazada por una más eficiente que evita STRING_AGG
                
        channels_by_resource = {}
        member_count_by_channel = {}
        for row in channels_permissions:
            resource_id = _safe_str(_first_value(row, "ResourceId", "IDResource"))
            channel_id = _safe_str(_first_value(row, "IDWorkRoom"))
            if not resource_id or not channel_id:
                continue

            member_count_by_channel[channel_id] = member_count_by_channel.get(channel_id, 0) + 1
            channels_by_resource.setdefault(_safe_str(resource_id), []).append({
                "channel_id": channel_id,
                "channel_name": _safe_str(_first_value(row, "WorkRoomName"), "Canal sin nombre"),
                "channel_kind": _safe_str(_first_value(row, "WorkRoomKind"), "workroom"),
            })

        permisos_canal_ingestados = 0
        for user_id, channels in channels_by_resource.items():
            profile = resource_profiles.get(user_id, {})
            display_name = profile.get("display_name") or user_id
            unique_channel_names = sorted({c["channel_name"] for c in channels if c.get("channel_name")})
            unique_channel_ids = sorted({c["channel_id"] for c in channels if c.get("channel_id")})

            actividad = Actividad(
                id=f"resource_channels_{user_id}",
                recurso_humano_id=user_id,
                canal_id="sysworkroom_permission",
                tipo="permisos_canal",
                descripcion=(
                    f"Permisos de canal de {display_name}: "
                    f"{', '.join(unique_channel_names[:20]) if unique_channel_names else 'sin_canales'}"
                ),
                timestamp=datetime.now(),
                metadatos={
                    "source_table": "SysWorkRoomResource",
                    "display_name": display_name,
                    "channel_count": len(unique_channel_ids),
                    "channel_ids": unique_channel_ids,
                    "channel_names": unique_channel_names,
                },
            )
            sistema.aprender_actividad(actividad)
            permisos_canal_ingestados += 1

        print(f"  ✅ {permisos_canal_ingestados} perfiles de permisos de canal ingeridos")

        # 5. Historial de conversación por recurso (consulta real compartida)
        print("📚 Ingestando historial de conversación por recurso (SysChat + Sys* joins)...")
        chats_ingestados = 0
        try:
            cursor.execute("""
                SELECT TOP 500
                    sc.IDChat2,
                    MAX(sc.Stamp) AS Stamp,
                    sc.RawMessage,
                    sc2w.IDWorkRoom,
                    swr.Name AS WorkRoomName,
                    swr.Kind AS WorkRoomKind,
                    COALESCE(
                        MAX(CONVERT(varchar(36), sr.ResourceId)),
                        MAX(CONVERT(varchar(36), sc2r.IDResource))
                    ) AS ResourceId,
                    COALESCE(
                        MAX(sr.DisplayName),
                        MAX(sl.FullName),
                        MAX(sl.Username)
                    ) AS DisplayName,
                    MAX(sl.FullName) AS FullName
                FROM dbo.SysChat sc
                INNER JOIN dbo.SysChat2SysWorkRoom sc2w
                    ON sc.IDChat2 = sc2w.IDChat2
                INNER JOIN dbo.SysWorkRoom swr
                    ON swr.IDWorkRoom = sc2w.IDWorkRoom
                LEFT JOIN dbo.SysChat2SysResource sc2r
                    ON sc2r.IDChat = sc.IDChat2
                LEFT JOIN dbo.SysResources sr
                    ON sr.ResourceId = sc2r.IDResource
                LEFT JOIN dbo.SysLogin sl
                    ON sl.ActiveIDLogin2Resource = sr.ActiveIDLogin2Resource
                WHERE sc.RawMessage IS NOT NULL
                GROUP BY
                    sc.IDChat2,
                    sc.RawMessage,
                    sc2w.IDWorkRoom,
                    swr.Name,
                    swr.Kind
                ORDER BY MAX(sc.Stamp) DESC
            """)
            chats_by_resource = cursor.fetchall() or []

            for row in chats_by_resource:
                chat_id = _safe_str(_first_value(row, "IDChat2", "IDChat"))
                resource_id = _safe_str(_first_value(row, "ResourceId", "IDResource"), "chat_user")
                channel_id = _safe_str(_first_value(row, "IDWorkRoom"), "canal_general")
                channel_name = _safe_str(_first_value(row, "WorkRoomName"), "Canal sin nombre")
                channel_kind = _safe_str(_first_value(row, "WorkRoomKind"), "workroom")
                raw_message = _safe_str(_first_value(row, "RawMessage", "Message"), "")
                display_name = _safe_str(_first_value(row, "DisplayName"), resource_id)
                full_name = _safe_str(_first_value(row, "FullName"), display_name)

                member_count = member_count_by_channel.get(channel_id, 0)
                kind_lower = channel_kind.lower()
                if member_count <= 2 or "private" in kind_lower or "direct" in kind_lower:
                    chat_scope = "chat_privado"
                else:
                    chat_scope = "canal_publico"

                actividad = Actividad(
                    id=f"chat_{chat_id}_{resource_id}",
                    recurso_humano_id=resource_id,
                    canal_id=channel_id,
                    tipo="chat",
                    descripcion=(
                        f"Chat ({chat_scope}) en {channel_name}: {raw_message} | "
                        f"Recurso: {display_name} | Usuario: {full_name}"
                    ),
                    timestamp=_first_value(row, "Stamp", default=datetime.now()),
                    metadatos={
                        "source_table": "SysChat",
                        "chat_id": chat_id,
                        "workroom_name": channel_name,
                        "workroom_kind": channel_kind,
                        "chat_scope": chat_scope,
                        "display_name": display_name,
                        "full_name": full_name,
                        "member_count": member_count,
                        "raw_message": raw_message,
                    },
                )
                if sistema.aprender_actividad(actividad):
                    chats_ingestados += 1
        except Exception as e:
            print(f"⚠️ Se omite la ingesta de chats por error SQL transitorio: {e}")

        conn.close()

        resumen = {
            "canales": len(canales),
            "canales_indexados": canales_indexados,
            "roles": len(roles),
            "recursos": recursos_ingestados,
            "chats": chats_ingestados,
        }

        print("\n✅ ¡Sistema real ingerido correctamente!")
        print(f"   - Canales: {resumen['canales']}")
        print(f"   - Canales indexados: {resumen['canales_indexados']}")
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