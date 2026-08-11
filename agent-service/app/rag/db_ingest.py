"""
db_ingest.py - Ingestor de datos desde bases de datos SQL
Soporta: SQL Server, PostgreSQL
"""

import os
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings

from app.config import settings
from app.rag.vector_store import ensure_vector_collection


class DatabaseIngestor:
    """
    Ingestor de datos desde bases de datos SQL.
    """
    
    def __init__(self):
        self.embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        self.qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
        self.collection = settings.VECTOR_COLLECTION_NAME
        
        # Verificar que la colección existe
        self._ensure_collection()
    
    def _ensure_collection(self):
        """Asegura que la colección existe en Qdrant."""
        try:
            ensure_vector_collection(self.qdrant, self.collection, self.embeddings)
        except Exception as e:
            print(f"⚠️ Error verificando colección: {e}")
    
    def _create_point(self, text: str, metadata: Dict[str, Any]) -> Optional[PointStruct]:
        """Crea un punto para Qdrant a partir de texto y metadatos."""
        try:
            vector = self.embeddings.embed_query(text)
            payload_metadata = dict(metadata)
            point_id = payload_metadata.pop("_point_id", None) or str(uuid.uuid4())
            
            # Añadir timestamp si no existe
            if "timestamp" not in payload_metadata:
                payload_metadata["timestamp"] = datetime.now().isoformat()
            
            # Añadir texto al payload
            payload = {
                "page_content": text,
                **payload_metadata
            }
            
            return PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
        except Exception as e:
            print(f"   ⚠️ Error creando punto: {e}")
            return None

    def _embed_and_create_points(self, texts: List[str], metadatas: List[Dict[str, Any]]) -> List[PointStruct]:
        """Embeds a batch of texts and creates Qdrant points."""
        points = []
        if not texts:
            return points

        try:
            # 1. Get embeddings for all texts in one go
            vectors = self.embeddings.embed_documents(texts)

            # 2. Create points
            for i, text in enumerate(texts):
                metadata = metadatas[i]
                vector = vectors[i]

                payload_metadata = dict(metadata)
                point_id = payload_metadata.pop("_point_id", None) or str(uuid.uuid4())

                if "timestamp" not in payload_metadata:
                    payload_metadata["timestamp"] = datetime.now().isoformat()

                payload = {
                    "page_content": text,
                    **payload_metadata
                }

                points.append(PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload
                ))
        except Exception as e:
            print(f"   ⚠️ Error creating points in batch: {e}")

        return points

    def _sql_server_connection(self):
        import pymssql

        return pymssql.connect(
            server=settings.SQL_SERVER_HOST,
            user=settings.SQL_SERVER_USER,
            password=settings.SQL_SERVER_PASSWORD,
            database=settings.SQL_SERVER_DB,
            timeout=10,
        )

    def _quote_identifier(self, raw_name: str) -> str:
        return f"[{str(raw_name).replace(']', ']]')}]"

    def _table_fqn(self, schema_name: str, table_name: str) -> str:
        return f"{self._quote_identifier(schema_name)}.{self._quote_identifier(table_name)}"

    def _deterministic_point_id(self, seed: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))

    def _normalize_value(self, value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value).strip()
        if len(text) > 600:
            return text[:597] + "..."
        return text

    def _row_to_text(
        self,
        schema_name: str,
        table_name: str,
        row: Dict[str, Any],
        max_columns: int = 80,
    ) -> str:
        col_lines = []
        for idx, (col_name, col_value) in enumerate(row.items()):
            if idx >= max_columns:
                col_lines.append("__truncated_columns__: true")
                break
            col_lines.append(f"{col_name}: {self._normalize_value(col_value)}")

        header = (
            f"REGISTRO SQL SERVER\n"
            f"Base: {settings.SQL_SERVER_DB}\n"
            f"Tabla: {schema_name}.{table_name}\n"
            f"Columnas:\n"
        )
        return header + "\n".join(col_lines)

    def _schema_to_text(self, schema_name: str, table_name: str, columns: List[Dict[str, Any]]) -> str:
        lines = [
            "DEFINICION DE TABLA SQL SERVER",
            f"Base: {settings.SQL_SERVER_DB}",
            f"Tabla: {schema_name}.{table_name}",
            f"Total columnas: {len(columns)}",
            "Columnas:",
        ]
        for col in columns:
            lines.append(
                "- {name}: {dtype} "
                "({nullable})".format(
                    name=col.get("column_name"),
                    dtype=col.get("data_type"),
                    nullable="NULL" if col.get("is_nullable") else "NOT NULL",
                )
            )
        return "\n".join(lines)

    def _list_sql_server_tables(self, cursor, schema_name: Optional[str]) -> List[Dict[str, str]]:
        query = """
            SELECT TABLE_SCHEMA, TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
        """
        params: tuple = ()
        if schema_name:
            query += " AND TABLE_SCHEMA = %s"
            params = (schema_name,)
        query += " ORDER BY TABLE_SCHEMA, TABLE_NAME"
        cursor.execute(query, params)
        rows = cursor.fetchall() or []
        return [
            {
                "schema": str(row.get("TABLE_SCHEMA") or "dbo"),
                "table": str(row.get("TABLE_NAME") or ""),
            }
            for row in rows
            if row.get("TABLE_NAME")
        ]

    def _get_table_columns(self, cursor, schema_name: str, table_name: str) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                IS_NULLABLE,
                ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (schema_name, table_name),
        )
        rows = cursor.fetchall() or []
        cols: List[Dict[str, Any]] = []
        for row in rows:
            max_len = row.get("CHARACTER_MAXIMUM_LENGTH")
            base_dtype = str(row.get("DATA_TYPE") or "unknown")
            if max_len is not None and isinstance(max_len, int) and max_len > 0:
                dtype = f"{base_dtype}({max_len})"
            else:
                dtype = base_dtype

            cols.append(
                {
                    "column_name": str(row.get("COLUMN_NAME") or ""),
                    "data_type": dtype,
                    "is_nullable": str(row.get("IS_NULLABLE") or "YES").upper() == "YES",
                }
            )
        return cols

    def ingest_sql_server_all_tables(
        self,
        rows_per_table: int = 200,
        schema_name: Optional[str] = None,
        max_tables: int = 0,
        batch_size: int = 20,
        exclude_tables: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Ingiere metadata + filas de todas las tablas de SQL Server.
        """
        try:
            print("\n🗃️ Ingestando TODAS las tablas de SQL Server...")
            print(
                f"   📌 DB={settings.SQL_SERVER_DB} schema={schema_name or '*'} "
                f"rows_per_table={'all' if rows_per_table <= 0 else rows_per_table} max_tables={max_tables or 'all'}"
            )
            conn = self._sql_server_connection()
            cursor = conn.cursor(as_dict=True)

            table_specs = self._list_sql_server_tables(cursor, schema_name=schema_name)
            excluded = {t.strip().lower() for t in (exclude_tables or []) if t and t.strip()}
            if excluded:
                table_specs = [
                    t for t in table_specs if f"{t['schema']}.{t['table']}".lower() not in excluded and t["table"].lower() not in excluded
                ]

            if max_tables > 0:
                table_specs = table_specs[:max_tables]

            if not table_specs:
                conn.close()
                print("   ⚠️ No se encontraron tablas para ingestar")
                return {
                    "tables": 0,
                    "schema_points": 0,
                    "row_points": 0,
                    "errors": 0,
                    "tables_with_error": [],
                }

            print(f"   📋 Tablas a procesar: {len(table_specs)}")

            schema_points = 0
            row_points = 0
            errors = 0
            table_errors: List[str] = []

            for idx, spec in enumerate(table_specs, start=1):
                tbl_schema = spec["schema"]
                tbl_name = spec["table"]
                fqn_text = f"{tbl_schema}.{tbl_name}"
                print(f"\n   [{idx}/{len(table_specs)}] Tabla: {fqn_text}")

                try:
                    columns = self._get_table_columns(cursor, schema_name=tbl_schema, table_name=tbl_name)
                    schema_text = self._schema_to_text(tbl_schema, tbl_name, columns)
                    schema_metadata = {
                        "entity_type": "sql_table_schema",
                        "database": settings.SQL_SERVER_DB,
                        "schema": tbl_schema,
                        "table": tbl_name,
                        "table_fqn": fqn_text,
                        "column_count": len(columns),
                        "source": "sql_server_schema",
                        "_point_id": self._deterministic_point_id(f"schema|{settings.SQL_SERVER_DB}|{fqn_text}"),
                    }
                    schema_point = self._create_point(schema_text, schema_metadata)
                    if schema_point is not None:
                        schema_points += self._ingest_points([schema_point], source=f"Schema {fqn_text}")

                    if int(rows_per_table) <= 0:
                        query = f"SELECT * FROM {self._table_fqn(tbl_schema, tbl_name)}"
                    else:
                        safe_limit = max(1, int(rows_per_table))
                        query = f"SELECT TOP {safe_limit} * FROM {self._table_fqn(tbl_schema, tbl_name)}"
                    cursor.execute(query)
                    rows = cursor.fetchall() or []

                    if not rows:
                        print("      ℹ️ Tabla sin filas (o sin resultados en TOP)")
                        continue

                    texts_batch: List[str] = []
                    metadatas_batch: List[Dict[str, Any]] = []
                    table_indexed = 0
                    for row_number, row in enumerate(rows, start=1):
                        row_dict = dict(row)
                        row_text = self._row_to_text(tbl_schema, tbl_name, row_dict)
                        seed = f"row|{settings.SQL_SERVER_DB}|{fqn_text}|{row_number}|{row_text}"

                        metadata = {
                            "entity_type": "sql_table_row",
                            "database": settings.SQL_SERVER_DB,
                            "schema": tbl_schema,
                            "table": tbl_name,
                            "table_fqn": fqn_text,
                            "source": "sql_server_all_tables",
                            "row_ordinal": row_number,
                            "column_count": len(row_dict),
                            "_point_id": self._deterministic_point_id(seed),
                        }
                        
                        texts_batch.append(row_text)
                        metadatas_batch.append(metadata)

                        if len(texts_batch) >= max(1, batch_size):
                            points_batch = self._embed_and_create_points(texts_batch, metadatas_batch)
                            if points_batch:
                                inserted = self._ingest_points(points_batch, source=f"Rows {fqn_text}")
                                table_indexed += inserted
                                row_points += inserted
                            texts_batch = []
                            metadatas_batch = []

                    if texts_batch:
                        points_batch = self._embed_and_create_points(texts_batch, metadatas_batch)
                        if points_batch:
                            inserted = self._ingest_points(points_batch, source=f"Rows {fqn_text}")
                            table_indexed += inserted
                            row_points += inserted

                    print(f"      ✅ Filas indexadas: {table_indexed}")
                except Exception as table_exc:
                    errors += 1
                    table_errors.append(fqn_text)
                    print(f"      ❌ Error en {fqn_text}: {table_exc}")

            conn.close()

            summary = {
                "tables": len(table_specs),
                "schema_points": schema_points,
                "row_points": row_points,
                "points_indexed_total": schema_points + row_points,
                "errors": errors,
                "tables_with_error": table_errors,
            }
            print("\n✅ Ingesta SQL completa terminada")
            print(f"   - Tablas procesadas: {summary['tables']}")
            print(f"   - Puntos de schema: {summary['schema_points']}")
            print(f"   - Puntos de filas: {summary['row_points']}")
            print(f"   - Total indexado: {summary['points_indexed_total']}")
            print(f"   - Errores de tabla: {summary['errors']}")
            return summary
        except ImportError:
            print("   ❌ pymssql no está instalado. Instala: pip install pymssql")
            return {
                "tables": 0,
                "schema_points": 0,
                "row_points": 0,
                "points_indexed_total": 0,
                "errors": 1,
                "tables_with_error": ["import_pymssql"],
            }
        except Exception as exc:
            print(f"   ❌ Error general en ingesta SQL total: {exc}")
            return {
                "tables": 0,
                "schema_points": 0,
                "row_points": 0,
                "points_indexed_total": 0,
                "errors": 1,
                "tables_with_error": ["global"],
            }
    
    def _ingest_points(self, points: List[PointStruct], source: str) -> int:
        """Ingiere una lista de puntos en Qdrant."""
        if not points:
            return 0
        
        try:
            self.qdrant.upsert(
                collection_name=self.collection,
                points=points
            )
            print(f"   ✅ Indexados {len(points)} registros desde {source}")
            return len(points)
        except Exception as e:
            print(f"   ❌ Error insertando: {e}")
            return 0
    
    # ============================================================
    # INGESTA DESDE SQL SERVER
    # ============================================================
    
    def ingest_sql_server_accounts(self, limit: int = 100) -> int:
        """
        Ingiere datos de clientes/cuentas desde SQL Server.
        Tabla: dbo.Account
        """
        try:
            import pymssql
            print("\n📊 Ingestando clientes desde SQL Server...")
            
            conn = pymssql.connect(
                server=settings.SQL_SERVER_HOST,
                user=settings.SQL_SERVER_USER,
                password=settings.SQL_SERVER_PASSWORD,
                database=settings.SQL_SERVER_DB,
                timeout=10
            )
            cursor = conn.cursor(as_dict=True)
            
            # Consulta para obtener clientes
            query = f"""
                SELECT TOP {limit}
                    IDAccount,
                    Name,
                    City,
                    Country,
                    TotalValueDebt,
                    TotalValueFinAct,
                    Classification,
                    Status,
                    CreatedDate,
                    ModifiedDate
                FROM dbo.Account
                WHERE Active = 1
                ORDER BY IDAccount
            """
            
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                print("   ⚠️ No se encontraron registros")
                return 0
            
            print(f"   📋 Encontrados {len(rows)} registros")
            
            points = []
            for row in rows:
                # Construir texto descriptivo
                text = f"""
                        CLIENTE: {row.get('Name', 'N/A')}
                        Ubicación: {row.get('City', 'N/A')}, {row.get('Country', 'N/A')}
                        Deuda total: ${row.get('TotalValueDebt', 0):,.2f}
                        Financiamiento: ${row.get('TotalValueFinAct', 0):,.2f}
                        Clasificación: {row.get('Classification', 'N/A')}
                        Estado: {row.get('Status', 'N/A')}
                        ID: {row.get('IDAccount', 'N/A')}
                        """
                
                # Metadatos
                metadata = {
                    "entity_type": "account",
                    "account_id": str(row.get('IDAccount', '')),
                    "name": row.get('Name', ''),
                    "city": row.get('City', ''),
                    "country": row.get('Country', ''),
                    "classification": row.get('Classification', ''),
                    "status": row.get('Status', ''),
                    "source": "sql_server_accounts",
                    "database": "ISIFrameIsicom"
                }
                
                point = self._create_point(text, metadata)
                if point:
                    points.append(point)
            
            return self._ingest_points(points, "SQL Server Accounts")
            
        except ImportError:
            print("   ❌ pymssql no está instalado. Instala: pip install pymssql")
            return 0
        except Exception as e:
            print(f"   ❌ Error ingiriendo desde SQL Server: {e}")
            return 0
    
    def ingest_sql_server_assets(self, limit: int = 100) -> int:
        """
        Ingiere datos de activos/máquinas desde SQL Server.
        Tabla: dbo.Asset
        """
        try:
            import pymssql
            print("\n🔧 Ingestando máquinas desde SQL Server...")
            
            conn = pymssql.connect(
                server=settings.SQL_SERVER_HOST,
                user=settings.SQL_SERVER_USER,
                password=settings.SQL_SERVER_PASSWORD,
                database=settings.SQL_SERVER_DB,
                timeout=10
            )
            cursor = conn.cursor(as_dict=True)
            
            query = f"""
                SELECT TOP {limit}
                    IDAsset,
                    Name,
                    Model,
                    SerialNumber,
                    Status,
                    Location,
                    InstallationDate,
                    LastMaintenance,
                    NextMaintenance,
                    Manufacturer
                FROM dbo.Asset
                WHERE Active = 1
                ORDER BY IDAsset
            """
            
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                print("   ⚠️ No se encontraron registros")
                return 0
            
            print(f"   📋 Encontrados {len(rows)} registros")
            
            points = []
            for row in rows:
                text = f"""
                    MÁQUINA: {row.get('Name', 'N/A')}
                    Modelo: {row.get('Model', 'N/A')}
                    Serial: {row.get('SerialNumber', 'N/A')}
                    Fabricante: {row.get('Manufacturer', 'N/A')}
                    Estado: {row.get('Status', 'N/A')}
                    Ubicación: {row.get('Location', 'N/A')}
                    Instalación: {row.get('InstallationDate', 'N/A')}
                    Último mantenimiento: {row.get('LastMaintenance', 'N/A')}
                    Próximo mantenimiento: {row.get('NextMaintenance', 'N/A')}
                    ID: {row.get('IDAsset', 'N/A')}
                    """
                
                metadata = {
                    "entity_type": "asset",
                    "asset_id": str(row.get('IDAsset', '')),
                    "name": row.get('Name', ''),
                    "model": row.get('Model', ''),
                    "status": row.get('Status', ''),
                    "location": row.get('Location', ''),
                    "manufacturer": row.get('Manufacturer', ''),
                    "source": "sql_server_assets",
                    "database": "ISIFrameIsicom"
                }
                
                point = self._create_point(text, metadata)
                if point:
                    points.append(point)
            
            return self._ingest_points(points, "SQL Server Assets")
            
        except ImportError:
            print("   ❌ pymssql no está instalado. Instala: pip install pymssql")
            return 0
        except Exception as e:
            print(f"   ❌ Error ingiriendo desde SQL Server: {e}")
            return 0
    
    def ingest_sql_server_activities(self, limit: int = 100) -> int:
        """
        Ingiere datos de actividades desde SQL Server.
        Tabla: dbo.Activity
        """
        try:
            import pymssql
            print("\n📝 Ingestando actividades desde SQL Server...")
            
            conn = pymssql.connect(
                server=settings.SQL_SERVER_HOST,
                user=settings.SQL_SERVER_USER,
                password=settings.SQL_SERVER_PASSWORD,
                database=settings.SQL_SERVER_DB,
                timeout=10
            )
            cursor = conn.cursor(as_dict=True)
            
            query = f"""
                SELECT TOP {limit}
                    IDActivity,
                    IDAccount,
                    IDAsset,
                    ActivityType,
                    Description,
                    Status,
                    CreatedDate,
                    DueDate,
                    Priority,
                    AssignedTo
                FROM dbo.Activity
                WHERE Active = 1
                ORDER BY CreatedDate DESC
            """
            
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                print("   ⚠️ No se encontraron registros")
                return 0
            
            print(f"   📋 Encontrados {len(rows)} registros")
            
            points = []
            for row in rows:
                text = f"""
                        ACTIVIDAD: {row.get('ActivityType', 'N/A')}
                        Descripción: {row.get('Description', 'N/A')}
                        Estado: {row.get('Status', 'N/A')}
                        Prioridad: {row.get('Priority', 'N/A')}
                        Asignado a: {row.get('AssignedTo', 'N/A')}
                        Fecha: {row.get('CreatedDate', 'N/A')}
                        Fecha límite: {row.get('DueDate', 'N/A')}
                        ID: {row.get('IDActivity', 'N/A')}
                        """
                
                metadata = {
                    "entity_type": "activity",
                    "activity_id": str(row.get('IDActivity', '')),
                    "account_id": str(row.get('IDAccount', '')),
                    "asset_id": str(row.get('IDAsset', '')),
                    "activity_type": row.get('ActivityType', ''),
                    "status": row.get('Status', ''),
                    "priority": row.get('Priority', ''),
                    "source": "sql_server_activities",
                    "database": "ISIFrameIsicom"
                }
                
                point = self._create_point(text, metadata)
                if point:
                    points.append(point)
            
            return self._ingest_points(points, "SQL Server Activities")
            
        except ImportError:
            print("   ❌ pymssql no está instalado. Instala: pip install pymssql")
            return 0
        except Exception as e:
            print(f"   ❌ Error ingiriendo desde SQL Server: {e}")
            return 0


# ============================================================
# FUNCIÓN PRINCIPAL DE INGESTA
# ============================================================

def ingest_from_database():
    """
    Ingiere datos desde todas las bases de datos configuradas.
    """
    print("\n" + "="*60)
    print("🗄️ INGESTA DE DATOS DESDE BASE DE DATOS")
    print("="*60 + "\n")
    
    ingestor = DatabaseIngestor()
    
    raw_rows_per_table = os.getenv("SQL_SERVER_INGEST_ROWS_PER_TABLE", "1000").strip().lower()
    if raw_rows_per_table in {"all", "*", "0", ""}:
        rows_per_table = 0
    else:
        rows_per_table = max(1, int(raw_rows_per_table))
    max_tables = max(0, int(os.getenv("SQL_SERVER_INGEST_MAX_TABLES", "0")))
    batch_size = max(1, int(os.getenv("SQL_SERVER_INGEST_BATCH_SIZE", "100")))
    schema_name = (os.getenv("SQL_SERVER_INGEST_SCHEMA", "").strip() or None)
    raw_excluded = os.getenv("SQL_SERVER_INGEST_EXCLUDE_TABLES", "").strip()
    exclude_tables = [part.strip() for part in raw_excluded.split(",") if part.strip()]

    sql_summary = ingestor.ingest_sql_server_all_tables(
        rows_per_table=rows_per_table,
        schema_name=schema_name,
        max_tables=max_tables,
        batch_size=batch_size,
        exclude_tables=exclude_tables,
    )

    total = int(sql_summary.get("points_indexed_total", 0))

    include_postgres = os.getenv("DB_INGEST_INCLUDE_POSTGRES", "false").strip().lower() in {"1", "true", "yes", "on"}
    if include_postgres:
        total += ingestor.ingest_postgresql_sensors(limit=50)
    
    print("\n" + "="*60)
    print(f"📊 TOTAL INGESTADO: {total} registros")
    print("="*60)
    
    return total


if __name__ == "__main__":
    ingest_from_database()
