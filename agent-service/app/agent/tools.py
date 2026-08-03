import json
import os
import re
from typing import Optional, Union
import uuid
import hashlib
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
from langchain_core.tools import tool
from langchain_ollama import OllamaEmbeddings
import pymssql
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams

from app.config import settings
from app.rag.audio_processor import extract_audio_features
from app.rag.retriever import get_rag_context


def _generated_docs_dir() -> Path:
    target = Path(settings.GENERATED_DOCS_DIR).expanduser()
    if not target.is_absolute():
        root = Path(__file__).resolve().parents[2]
        target = (root / target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_file_stem(value: Optional[str], fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = fallback
    clean = re.sub(r"[^A-Za-z0-9_\-\s]", "", raw).strip().replace(" ", "_")
    clean = re.sub(r"_+", "_", clean)
    return clean[:60] or fallback


def _timestamp_suffix() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _normalize_document_lines(content: str) -> list[str]:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    # Compactar bloques vacíos consecutivos para evitar saltos excesivos.
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = is_blank
    return normalized


def _infer_document_kind(title: str, content: str) -> str:
    probe = f"{title} {content}".lower()
    if any(word in probe for word in ["acta", "meeting", "minuta"]):
        return "Acta"
    if any(word in probe for word in ["resumen", "summary"]):
        return "Resumen"
    if any(word in probe for word in ["canal", "incidencia", "diagnost", "informe", "report"]):
        return "Informe"
    return "Documento"


def _extract_cte_names(query: str) -> set[str]:
    cte_names = set()
    for match in re.finditer(r"(?:WITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", query, flags=re.IGNORECASE):
        cte_names.add(match.group(1).lower())
    return cte_names


def _extract_table_references(query: str) -> set[str]:
    refs = set()
    cte_names = _extract_cte_names(query)
    for match in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_\[\]\.]+)", query, flags=re.IGNORECASE):
        token = match.group(1).strip()
        if token.startswith("("):
            continue
        clean = token.replace("[", "").replace("]", "")
        parts = [p for p in clean.split(".") if p]
        if not parts:
            continue
        if len(parts) == 1:
            schema = "dbo"
            table = parts[0]
        else:
            schema = parts[-2]
            table = parts[-1]
        if table.lower() in cte_names:
            continue
        refs.add(f"{schema.lower()}.{table.lower()}")
    return refs


def _load_real_tables(cursor) -> set[str]:
    cursor.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        """
    )
    rows = cursor.fetchall() or []
    return {f"{str(r['TABLE_SCHEMA']).lower()}.{str(r['TABLE_NAME']).lower()}" for r in rows}


# ---------------------------------------------------------------------------
# 1. TOOL: SQL Server Query (MEJORADA con validación de filas)
# ---------------------------------------------------------------------------
@tool
def query_sql_server(query: str) -> str:
    """
    EJECUTA CONSULTAS SELECT EN SQL SERVER.
    
    CUÁNDO USARLA:
    - Cuando el usuario pida datos de clientes (dbo.Account)
    - Cuando el usuario pida historial de actividades (dbo.Activity)
    - Cuando el usuario pida información de máquinas/activos (dbo.Asset)
    - Cuando el usuario pregunte por saldos, deudas o inventarios
    
    CUÁNDO NO USARLA:
    - NO la uses para explorar la estructura de tablas (usa get_db_schema)
    - NO la uses para saludos o conversación general
    
    ADVERTENCIA DE SEGURIDAD:
    - Siempre usa filtros WHERE para evitar consultas masivas
    - Si el usuario no especifica filtros, pregunta antes de ejecutar
    """
    server = settings.SQL_SERVER_HOST
    user = settings.SQL_SERVER_USER
    password = settings.SQL_SERVER_PASSWORD
    database = settings.SQL_SERVER_DB

    clean_query = query.strip()
    if not clean_query.upper().startswith("SELECT") and not clean_query.upper().startswith("WITH"):
        return "Error de seguridad: Solo se permiten consultas de lectura (SELECT / WITH)."

    forbidden_keywords = ["DELETE", "INSERT", "UPDATE", "DROP", "ALTER", "TRUNCATE", "EXEC", "EXECUTE"]
    if any(kw in clean_query.upper() for kw in forbidden_keywords):
        return "Error de seguridad: La consulta contiene comandos no permitidos."

    try:
        conn = pymssql.connect(
            server=server,
            user=user,
            password=password,
            database=database,
            timeout=5,
        )
        cursor = conn.cursor(as_dict=True)

        referenced_tables = _extract_table_references(clean_query)
        real_tables = _load_real_tables(cursor)
        unknown_tables = sorted(t for t in referenced_tables if t not in real_tables)
        if unknown_tables:
            conn.close()
            unknown = ", ".join(unknown_tables)
            sample = ", ".join(sorted(list(real_tables))[:12])
            return (
                "Error de esquema: la consulta usa tablas que no existen en esta base de datos: "
                f"{unknown}. "
                "Consulta primero el esquema real con get_db_schema antes de reintentar. "
                f"Ejemplos de tablas reales: {sample}."
            )

        cursor.execute(clean_query)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "La consulta se ejecutó correctamente pero no devolvió resultados."

        # 🚨 NUEVA VALIDACIÓN: Si son más de 50 filas, advertir
        if len(rows) > 50:
            return f"⚠️ ADVERTENCIA: La consulta devolvió {len(rows)} filas. Mostrando solo las primeras 15.\n\n{json.dumps(rows[:15])}"

        return json.dumps(rows[:15])

    except pymssql.Error as db_err:
        return f"Error SQL Server: {str(db_err)}. Ajusta los campos/tablas y vuelve a intentar."
    except Exception as e:
        return f"Error al conectar o consultar SQL Server: {str(e)}"


@tool
def get_db_schema(table_name: Optional[str] = None) -> str:
    """
    EXPLORA LA ESTRUCTURA DE LA BASE DE DATOS.
    
    CUÁNDO USARLA (SOLO PARA EXPLORACIÓN):
    - El usuario pregunta "¿Qué tablas hay en la base de datos?"
    - El usuario pregunta "¿Qué columnas tiene la tabla X?"
    - El usuario dice "No sé qué tabla contiene los datos de clientes"
    
    CUÁNDO NO USARLA:
    - NO la uses para obtener datos reales de negocio (usa query_sql_server)
    - NO la uses si el usuario ya sabe qué tabla consultar
    """
    server = settings.SQL_SERVER_HOST
    user = settings.SQL_SERVER_USER
    password = settings.SQL_SERVER_PASSWORD
    database = settings.SQL_SERVER_DB

    try:
        conn = pymssql.connect(server=server, user=user, password=password, database=database, timeout=5)
        cursor = conn.cursor(as_dict=True)

        if table_name:
            query = """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = %s
            """
            cursor.execute(query, (table_name,))
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                return f"No se encontró la tabla '{table_name}'."
            # Formatear bonito
            resultado = f"📋 ESTRUCTURA DE LA TABLA '{table_name}':\n\n"
            for row in rows:
                resultado += f"  • {row['COLUMN_NAME']} ({row['DATA_TYPE']}) - {'Puede ser NULL' if row['IS_NULLABLE'] == 'YES' else 'NO NULL'}\n"
            return resultado
        else:
            query = """
                SELECT TABLE_NAME 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            tables = [r['TABLE_NAME'] for r in rows]
            return f"📊 TABLAS DISPONIBLES ({len(tables)} en total):\n\n" + "\n".join([f"  • {t}" for t in tables[:30]])

    except Exception as e:
        return f"Error al consultar el esquema: {str(e)}"


# ---------------------------------------------------------------------------
# 🚨 NUEVA TOOL: Confirmación de consultas pesadas (Human-in-the-loop)
# ---------------------------------------------------------------------------
@tool
def confirm_large_operation(operation_type: str, description: str, estimated_impact: str) -> str:
    """
    SOLICITA CONFIRMACIÓN DEL USUARIO ANTES DE OPERACIONES CRÍTICAS.
    
    CUÁNDO USARLA (SIEMPRE antes de):
    - Ejecutar un SELECT sin filtros WHERE (puede devolver miles de filas)
    - Enviar correos o mensajes automáticos
    - Realizar cambios en configuración de máquinas
    - Cualquier acción que el usuario NO haya pedido explícitamente
    
    CÓMO USARLA:
    1. Describe la operación que quieres hacer
    2. Espera la respuesta del usuario (Sí/No)
    3. Si dice "Sí", ejecuta la acción; si dice "No", cancela
    """
    return f"⚠️ SOLICITUD DE CONFIRMACIÓN:\n\nOperación: {operation_type}\nDescripción: {description}\nImpacto estimado: {estimated_impact}\n\n❓ ¿Confirmas que quieres realizar esta operación? (Responde 'Sí' o 'No')"


# ---------------------------------------------------------------------------
# 2. TOOL: Consumir API Externa (MEJORADA)
# ---------------------------------------------------------------------------
@tool
def fetch_external_api(endpoint_url: str, method: str = "GET", payload: Optional[Union[dict, str]] = None,) -> str:
    """
    CONSULTA APIs EXTERNAS (HTTP/GraphQL).
    
    CUÁNDO USARLA:
    - El usuario comparte una URL y dice "consulta esto"
    - El usuario pide datos de un sistema externo (clima, cotizaciones, etc.)
    
    PRECAUCIÓN:
    - NUNCA la uses para APIs internas de la planta (usa query_sql_server)
    - Siempre valida que la URL sea segura
    """
    try:
        parsed_payload = None
        if payload:
            if isinstance(payload, str):
                try:
                    parsed_payload = json.loads(payload)
                except json.JSONDecodeError:
                    clean_str = payload.replace("'", '"')
                    parsed_payload = json.loads(clean_str)
            else:
                parsed_payload = payload

        with httpx.Client(timeout=15.0) as client:
            if method.upper() == "POST":
                response = client.post(endpoint_url, json=parsed_payload or {})
            else:
                response = client.get(endpoint_url)
                
            response.raise_for_status()
            return str(response.json())
            
    except httpx.HTTPStatusError as exc:
        return f"Error HTTP {exc.response.status_code}: {exc.response.text}"
    except Exception as e:
        return f"Error de conexión con la API: {str(e)}"


# ---------------------------------------------------------------------------
# 3. TOOLS: Telemetría y Diagnóstico CNC (MEJORADAS)
# ---------------------------------------------------------------------------
@tool
def get_cnc_telemetry() -> dict:
    """
    CONSULTA TELEMETRÍA EN TIEMPO REAL DE LA CNC HARTFORD.
    
    CUÁNDO USARLA:
    - El usuario pregunta "¿Cómo está la máquina?"
    - El usuario pregunta "¿Qué alarmas tiene?"
    - El usuario pregunta por RPM, temperatura o velocidad de avance
    
    DEVUELVE:
    - Estado de la máquina (OPERATIONAL/STOPPED/MAINTENANCE)
    - Velocidad del husillo (RPM)
    - Velocidad de avance (mm/min)
    - Potencia del husillo (%)
    - Alarmas activas
    """
    return {
        "status": "OPERATIONAL",
        "spindle_speed_rpm": 3200,
        "feed_rate_mm_min": 450,
        "spindle_power_pct": 88.5,
        "active_alarms": ["ALARM_102: Overload Spindle Warning"],
    }


@tool
def recommend_cnc_action(action: str, parameter: str, value: str) -> str:
    """
    REGISTRA UNA ACCIÓN CORRECTIVA PARA LA CNC.
    
    CUÁNDO USARLA:
    - Cuando el usuario pide cambiar un parámetro de la máquina
    - Cuando el usuario dice "Reduce la velocidad" o "Sube la temperatura"
    - Cuando se detecta una alarma y se necesita ajustar algo
    
    NOTA: Esta herramienta SOLO registra la acción, no la ejecuta físicamente.
    """
    return f"✅ Acción '{action}' con parámetro '{parameter}={value}' registrada en el sistema de mantenimiento."


@tool
def analyze_pcm_audio_diagnostic(file_path: str) -> str:
    """
    ANALIZA ARCHIVOS DE AUDIO .PCM DE LA CNC HARTFORD.
    
    CUÁNDO USARLA:
    - El usuario sube un archivo .pcm y pide diagnóstico
    - El usuario menciona "ruido extraño en la máquina"
    - El usuario pregunta por análisis acústico
    
    DEVUELVE:
    - Energía RMS (indica nivel de vibración)
    - Centroide espectral (frecuencia dominante)
    - Coincidencias con patrones conocidos (RAG)
    """
    try:
        features = extract_audio_features(file_path)
        rag_matches = get_rag_context(features["text_summary"])
        return (
            f"🔊 RESULTADOS DEL ANÁLISIS ACÚSTICO:\n\n"
            f"📁 Archivo: {features['file_name']}\n"
            f"📊 Energía RMS: {features['rms_energy']:.4f} (indica nivel de vibración)\n"
            f"🎵 Centroide Espectral: {features['spectral_centroid']:.2f} Hz\n"
            f"📚 Patrones coincidentes en la base de conocimiento:\n{rag_matches}"
        )
    except Exception as e:
        return f"Error al procesar el archivo de audio: {str(e)}"


@tool
def learn_new_fact(fact_description: str, category: str = "general") -> str:
    """
    GUARDA NUEVO CONOCIMIENTO EN LA BASE VECTORIAL.
    
    CUÁNDO USARLA:
    - El usuario dice "Aprende esto: ..."
    - El usuario comparte una observación importante sobre la máquina
    - El usuario enseña una regla de mantenimiento
    
    REGLAS:
    - SIEMPRE usa 'general' como categoría por defecto
    - Usa 'mantenimiento' si es sobre reparaciones
    - Usa 'operacion' si es sobre procedimientos de trabajo
    """
    try:
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME,
        )

        collections = [c.name for c in client.get_collections().collections]
        if settings.VECTOR_COLLECTION_NAME not in collections:
            client.create_collection(
                collection_name=settings.VECTOR_COLLECTION_NAME,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        vector = embeddings.embed_query(fact_description)

        # 🚀 MEJORA: ID único basado en hash para evitar duplicados
        content_hash = hashlib.md5(fact_description.encode()).hexdigest()
        point_id = str(uuid.UUID(content_hash))

        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "page_content": fact_description,
                "category": category,
                "source": "operator_learning",
                "timestamp": str(uuid.uuid4()),
            },
        )

        client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, points=[point])
        return f"✅ Aprendizaje registrado correctamente: '{fact_description}'"
    except Exception as e:
        return f"Error al registrar el aprendizaje: {str(e)}"


# ---------------------------------------------------------------------------
# 4. TOOLS: Generación de documentos (Word / Excel / PDF)
# ---------------------------------------------------------------------------
@tool
def create_word_document(
    title: str,
    content: str,
    file_name: Optional[str] = None,
    document_kind: Optional[str] = None,
) -> str:
    """
    CREA UN DOCUMENTO WORD (.docx) con un título y contenido libre.

    CUÁNDO USARLA:
    - El usuario pida "hazme un Word" o "genera un informe en Word"
    - Se necesite exportar una respuesta a formato editable
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt

        stem = _safe_file_stem(file_name or title, "documento")
        path = _generated_docs_dir() / f"{stem}_{_timestamp_suffix()}.docx"

        kind = (document_kind or _infer_document_kind(title, content)).strip() or "Documento"
        lines = _normalize_document_lines(content)

        doc = Document()
        header = doc.add_heading(title.strip() or "Documento", level=1)
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER

        meta = doc.add_paragraph()
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
        meta_run = meta.add_run(f"Tipo: {kind} | Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        meta_run.italic = True
        meta_run.font.size = Pt(10)

        doc.add_paragraph("")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                doc.add_paragraph("")
                continue

            # Títulos de sección: "Sección:" o líneas totalmente en mayúsculas.
            is_section_title = stripped.endswith(":") or (stripped.upper() == stripped and len(stripped) <= 60)
            if is_section_title:
                p = doc.add_paragraph(stripped)
                p.runs[0].bold = True
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                continue

            # Viñetas básicas.
            if stripped.startswith(("- ", "• ", "* ")):
                p = doc.add_paragraph(stripped[2:].strip(), style="List Bullet")
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                continue

            p = doc.add_paragraph(stripped)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        doc.save(path)

        return f"✅ Documento Word creado correctamente en: {path}"
    except Exception as e:
        return f"Error creando documento Word: {str(e)}"


@tool
def create_excel_document(
    title: str,
    rows_json: str,
    sheet_name: str = "Datos",
    file_name: Optional[str] = None,
    document_kind: Optional[str] = None,
) -> str:
    """
    CREA UN ARCHIVO EXCEL (.xlsx) a partir de filas en JSON.

    Formato esperado en rows_json:
    - Lista de objetos: [{"columna":"valor"}, {"columna":"valor2"}]
    """
    try:
        from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter

        stem = _safe_file_stem(file_name or title, "reporte")
        safe_sheet = _safe_file_stem(sheet_name, "Datos")[:31]
        path = _generated_docs_dir() / f"{stem}_{_timestamp_suffix()}.xlsx"
        kind = (document_kind or _infer_document_kind(title, rows_json)).strip() or "Documento"

        parsed = json.loads(rows_json) if rows_json else []
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return "Error creando Excel: rows_json debe ser una lista JSON de filas u objeto JSON."

        df = pd.DataFrame(parsed)
        if df.empty:
            df = pd.DataFrame([{"mensaje": "Sin datos para exportar"}])

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            start_row = 3
            df.to_excel(writer, index=False, sheet_name=safe_sheet, startrow=start_row)
            worksheet = writer.sheets[safe_sheet]

            # Encabezado formal.
            worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(df.columns)))
            worksheet["A1"] = title.strip() or "Reporte"
            worksheet["A2"] = f"Tipo: {kind} | Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            worksheet["A1"].font = Font(bold=True, size=14)
            worksheet["A2"].font = Font(italic=True, size=10)

            header_row = start_row + 1
            thin = Side(style="thin", color="D9D9D9")
            for col_idx in range(1, len(df.columns) + 1):
                header_cell = worksheet.cell(row=header_row, column=col_idx)
                header_cell.font = Font(bold=True, color="FFFFFF")
                header_cell.fill = PatternFill("solid", fgColor="1F4E78")
                header_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                header_cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)

            for row_idx in range(header_row + 1, header_row + 1 + len(df.index)):
                for col_idx in range(1, len(df.columns) + 1):
                    cell = worksheet.cell(row=row_idx, column=col_idx)
                    cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                    cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)

            for idx, col in enumerate(df.columns, start=1):
                max_len = max(len(str(col)), *(len(str(v)) for v in df[col].astype(str).tolist()))
                letter = get_column_letter(idx)
                worksheet.column_dimensions[letter].width = min(max(12, max_len + 2), 50)

            worksheet.freeze_panes = worksheet.cell(row=header_row + 1, column=1)
            worksheet.auto_filter.ref = worksheet.dimensions

        return f"✅ Archivo Excel creado correctamente en: {path}"
    except json.JSONDecodeError:
        return "Error creando Excel: rows_json no tiene formato JSON válido."
    except Exception as e:
        return f"Error creando Excel: {str(e)}"


@tool
def create_pdf_document(
    title: str,
    content: str,
    file_name: Optional[str] = None,
    document_kind: Optional[str] = None,
) -> str:
    """
    CREA UN DOCUMENTO PDF (.pdf) con un título y contenido textual.

    CUÁNDO USARLA:
    - El usuario pida "hazme un PDF" o "exporta este resumen en PDF"
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

        stem = _safe_file_stem(file_name or title, "documento")
        path = _generated_docs_dir() / f"{stem}_{_timestamp_suffix()}.pdf"
        kind = (document_kind or _infer_document_kind(title, content)).strip() or "Documento"
        lines = _normalize_document_lines(content)

        doc = SimpleDocTemplate(
            str(path),
            pagesize=letter,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            spaceAfter=6,
        )
        meta_style = ParagraphStyle(
            "DocMeta",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            fontName="Helvetica-Oblique",
            fontSize=9,
            textColor=colors.HexColor("#555555"),
            leading=12,
            spaceAfter=10,
        )
        section_style = ParagraphStyle(
            "DocSection",
            parent=styles["Heading3"],
            alignment=TA_LEFT,
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            spaceBefore=8,
            spaceAfter=4,
        )
        body_style = ParagraphStyle(
            "DocBody",
            parent=styles["Normal"],
            alignment=TA_JUSTIFY,
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            spaceAfter=6,
        )
        bullet_style = ParagraphStyle(
            "DocBullet",
            parent=body_style,
            leftIndent=14,
            bulletIndent=2,
            spaceAfter=4,
        )

        story = [
            Paragraph((title or "Documento").strip(), title_style),
            Paragraph(f"Tipo: {kind} | Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}", meta_style),
            HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D0D7DE"), spaceAfter=10),
        ]

        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                story.append(Spacer(1, 4))
                continue

            safe = stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            is_section_title = safe.endswith(":") or (safe.upper() == safe and len(safe) <= 60)
            if is_section_title:
                story.append(Paragraph(safe, section_style))
                continue

            if safe.startswith(("- ", "• ", "* ")):
                story.append(Paragraph(safe[2:].strip(), bullet_style, bulletText="•"))
                continue

            story.append(Paragraph(safe, body_style))

        doc.build(story)
        return f"✅ Documento PDF creado correctamente en: {path}"
    except Exception as e:
        return f"Error creando PDF: {str(e)}"