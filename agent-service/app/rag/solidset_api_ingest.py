"""
Ingesta de colecciones Postman de SOLIDSET para entrenamiento del agente.

Convierte endpoints (metodo, ruta, query, body, headers) en conocimiento
vectorial para que el agente pueda responder preguntas funcionales sobre la API.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.config import settings
from app.rag.vector_store import ensure_vector_collection


class SolidSetApiIngestor:
    """Ingesta una coleccion Postman de SOLIDSET en Qdrant."""

    def __init__(self) -> None:
        self.client = QdrantClient(url=settings.VECTOR_DB_URL)
        self.collection = settings.VECTOR_COLLECTION_NAME
        self.embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME,
        )
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        ensure_vector_collection(self.client, self.collection, self.embeddings)

    def _flatten_items(self, items: list[dict[str, Any]], prefix: str = "") -> list[dict[str, Any]]:
        flat: list[dict[str, Any]] = []
        for item in items or []:
            name = str(item.get("name") or "sin_nombre").strip()
            qualified_name = f"{prefix}/{name}" if prefix else name

            if isinstance(item.get("request"), dict):
                copy_item = dict(item)
                copy_item["qualified_name"] = qualified_name
                flat.append(copy_item)
                continue

            nested = item.get("item")
            if isinstance(nested, list):
                flat.extend(self._flatten_items(nested, prefix=qualified_name))

        return flat

    def _extract_url_data(self, url_node: Any) -> dict[str, Any]:
        if isinstance(url_node, str):
            return {
                "raw": url_node,
                "path": [],
                "query": [],
            }

        if not isinstance(url_node, dict):
            return {"raw": "", "path": [], "query": []}

        raw = str(url_node.get("raw") or "").strip()
        path = url_node.get("path") if isinstance(url_node.get("path"), list) else []
        query = url_node.get("query") if isinstance(url_node.get("query"), list) else []
        return {
            "raw": raw,
            "path": path,
            "query": query,
        }

    def _extract_form_data(self, request_node: dict[str, Any]) -> list[dict[str, Any]]:
        body = request_node.get("body") if isinstance(request_node.get("body"), dict) else {}
        formdata = body.get("formdata") if isinstance(body.get("formdata"), list) else []
        return [item for item in formdata if isinstance(item, dict)]

    def _format_query(self, query_items: list[dict[str, Any]]) -> str:
        if not query_items:
            return "sin_query"

        parts = []
        for entry in query_items:
            key = str(entry.get("key") or "").strip()
            if not key:
                continue
            value = str(entry.get("value") or "").strip()
            parts.append(f"{key}={value}")

        return ", ".join(parts) if parts else "sin_query"

    def _format_form(self, form_items: list[dict[str, Any]]) -> str:
        if not form_items:
            return "sin_form"

        parts = []
        for entry in form_items:
            if entry.get("disabled"):
                continue
            key = str(entry.get("key") or "").strip()
            if not key:
                continue
            value = str(entry.get("value") or "").strip()
            parts.append(f"{key}={value}")

        return ", ".join(parts) if parts else "sin_form"

    def _format_headers(self, headers: list[dict[str, Any]]) -> str:
        if not headers:
            return "sin_headers"

        allowed = []
        for header in headers:
            if not isinstance(header, dict):
                continue
            if header.get("disabled"):
                continue
            key = str(header.get("key") or "").strip()
            if not key:
                continue
            if key.lower() in {"cookie", "x-requested-with", "content-type", "timezoneid"}:
                allowed.append(f"{key}={str(header.get('value') or '').strip()}")

        return ", ".join(allowed) if allowed else "sin_headers_relevantes"

    def _domain_from_path(self, path_segments: list[Any]) -> str:
        if not path_segments:
            return "general"
        first = str(path_segments[0]).strip()
        return first or "general"

    def _build_learning_text(
        self,
        name: str,
        method: str,
        raw_url: str,
        path_segments: list[Any],
        query_items: list[dict[str, Any]],
        form_items: list[dict[str, Any]],
        header_items: list[dict[str, Any]],
    ) -> str:
        route = "/" + "/".join(str(p).strip() for p in path_segments if str(p).strip())
        route = route if route != "/" else "sin_ruta"

        query_text = self._format_query(query_items)
        form_text = self._format_form(form_items)
        headers_text = self._format_headers(header_items)

        return (
            f"SOLIDSET API endpoint. Nombre: {name}. "
            f"Metodo: {method}. Ruta: {route}. URL raw: {raw_url or 'sin_url'}. "
            f"Query params: {query_text}. "
            f"Body form params: {form_text}. "
            f"Headers relevantes: {headers_text}. "
            "Uso recomendado: autenticar primero y despues invocar segun intencion de negocio."
        )

    def ingest_collection(self, collection_path: str) -> dict[str, Any]:
        path = Path(collection_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"No existe el archivo de coleccion: {path}")

        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)

        info = payload.get("info") if isinstance(payload, dict) else {}
        items = payload.get("item") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise ValueError("La coleccion no contiene 'item' valido")

        flattened = self._flatten_items(items)

        points: list[PointStruct] = []
        skipped = 0
        now_iso = datetime.utcnow().isoformat()

        for endpoint in flattened:
            request_node = endpoint.get("request") if isinstance(endpoint.get("request"), dict) else {}
            method = str(request_node.get("method") or "GET").upper().strip() or "GET"
            url_data = self._extract_url_data(request_node.get("url"))
            raw_url = url_data["raw"]
            path_segments = url_data["path"]
            query_items = url_data["query"]
            form_items = self._extract_form_data(request_node)
            header_items = request_node.get("header") if isinstance(request_node.get("header"), list) else []

            qualified_name = str(endpoint.get("qualified_name") or endpoint.get("name") or "sin_nombre").strip()
            if not qualified_name:
                skipped += 1
                continue

            text = self._build_learning_text(
                name=qualified_name,
                method=method,
                raw_url=raw_url,
                path_segments=path_segments,
                query_items=query_items,
                form_items=form_items,
                header_items=header_items,
            )

            vector = self.embeddings.embed_query(text)
            endpoint_key = f"{method}|{raw_url}|{qualified_name}"
            point_id = str(uuid.UUID(hashlib.md5(endpoint_key.encode("utf-8")).hexdigest()))

            domain = self._domain_from_path(path_segments)
            route = "/" + "/".join(str(p).strip() for p in path_segments if str(p).strip())

            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "source": "solidset_api_collection",
                        "source_file": path.name,
                        "collection_name": str((info or {}).get("name") or "unknown"),
                        "endpoint_name": qualified_name,
                        "method": method,
                        "route": route,
                        "raw_url": raw_url,
                        "domain": domain,
                        "query_params": query_items,
                        "form_params": form_items,
                        "headers": header_items,
                        "ingested_at": now_iso,
                        "page_content": text,
                    },
                )
            )

        if points:
            self.client.upsert(collection_name=self.collection, points=points)

        return {
            "status": "ok",
            "collection": self.collection,
            "source_file": str(path),
            "collection_name": str((info or {}).get("name") or "unknown"),
            "endpoints_found": len(flattened),
            "points_upserted": len(points),
            "skipped": skipped,
        }


def ingest_solidset_api_collection(collection_path: str | None = None) -> dict[str, Any]:
    """Atajo para ejecutar la ingesta de la coleccion Postman de SOLIDSET."""
    if not collection_path:
        root = Path(__file__).resolve().parents[2]
        collection_path = str(root / "doctus-integracion.json")

    ingestor = SolidSetApiIngestor()
    return ingestor.ingest_collection(collection_path)
