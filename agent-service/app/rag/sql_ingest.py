import uuid
import pymssql
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings
from app.config import settings

def ingestar_esquema_sql_a_qdrant():
    """Extrae las tablas y columnas principales de ISIFrameIsicom y las vectoriza en Qdrant."""
    query = """
    SELECT 
        t.name AS table_name,
        c.name AS column_name,
        ep.value AS column_description
    FROM sys.tables t
    INNER JOIN sys.columns c ON t.object_id = c.object_id
    LEFT JOIN sys.extended_properties ep 
        ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
    WHERE t.is_ms_shipped = 0;
    """
    
    try:
        conn = pymssql.connect(
            server=settings.SQL_SERVER_HOST,
            user=settings.SQL_SERVER_USER,
            password=settings.SQL_SERVER_PASSWORD,
            database=settings.SQL_SERVER_DB,
            timeout=5
        )
        cursor = conn.cursor(as_dict=True)
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return

        # Agrupar columnas por tabla
        tables = {}
        for row in rows:
            tname = row["table_name"]
            if tname not in tables:
                tables[tname] = []
            tables[tname].append(row["column_name"])

        # Generar texto descriptivo por tabla para el RAG
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )

        points = []
        for tname, cols in tables.items():
            content = f"Estructura SQL de la tabla dbo.{tname}: Contiene las columnas {', '.join(cols)}."
            vector = embeddings.embed_query(content)
            
            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"sql_schema_{tname}")),
                    vector=vector,
                    payload={
                        "page_content": content,
                        "table_name": tname,
                        "source": "sql_schema_metadata"
                    }
                )
            )

        client.upsert(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            points=points
        )
        print(f"✅ Se actualizaron {len(points)} metadatos de tablas SQL en el RAG de Qdrant.")
    except Exception as e:
        print(f"Error al ingestar esquema SQL: {e}")