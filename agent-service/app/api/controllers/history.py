from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from langchain_community.chat_message_histories import RedisChatMessageHistory

from app.config import settings


router = APIRouter(tags=["Audio, History and Context"])


@router.get("/api/v1/agent/audio-response")
def get_audio_file(file: str):
    """
    Devuelve un archivo de audio generado previamente.
    """
    file_path = os.path.join("/tmp", file)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="audio/mpeg", filename=file)
    raise HTTPException(status_code=404, detail="Ficheiro de áudio não encontrado.")


@router.get("/api/v1/agent/history/{session_id}")
def get_chat_history(
    session_id: str,
    before: int = Query(
        0,
        ge=0,
        description="Number of recent messages to skip before returning results (backward-scroll cursor).",
    ),
    limit: int = Query(
        10, ge=1, le=100, description="Maximum number of messages returned per page."
    ),
):
    """
    Recupera el historial de conversación de una sesión específica desde Redis.
    Devuelve mensajes paginados para soportar scroll infinito:
    - before=0 trae los mensajes mas recientes.
    - before=N omite los N mas recientes y trae mensajes anteriores.
    """
    try:
        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        messages = []

        for msg in history.messages:
            # Identificar el rol
            role = "user" if msg.type in ["human", "user"] else "assistant"
            messages.append({"role": role, "content": msg.content, "type": msg.type})

        total_messages = len(messages)
        end_index = max(0, total_messages - before)
        start_index = max(0, end_index - limit)
        paged_messages = messages[start_index:end_index]
        has_more = start_index > 0
        next_before = before + len(paged_messages) if has_more else None

        return {
            "session_id": session_id,
            "messages": paged_messages,
            "total_messages": total_messages,
            "returned_messages": len(paged_messages),
            "pagination": {
                "before": before,
                "limit": limit,
                "has_more": has_more,
                "next_before": next_before,
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Não foi possível obter o histórico."
        )


@router.delete("/api/v1/agent/history/{session_id}")
def clear_chat_history(session_id: str):
    """
    Limpia el historial de una sesión específica.
    """
    try:
        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        history.clear()
        return {
            "session_id": session_id,
            "status": "cleared",
            "message": "Histórico eliminado com sucesso",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Não foi possível eliminar o histórico."
        )
