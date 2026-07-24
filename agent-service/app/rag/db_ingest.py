"""
db_ingest.py - Ingestor de datos desde bases de datos SQL
Soporta: SQL Server, PostgreSQL
"""

import os
import sys
import uuid
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings

from app.config import settings


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
            from qdrant_client.models import Distance, VectorParams
            
            collections = self.qdrant.get_collections()
            collection_names = [c.name for c in collections.collections]
            
            if self.collection not in collection_names:
                print(f"📦 Creando colección: {self.collection}")
                self.qdrant.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(
                        size=768,
                        distance=Distance.COSINE
                    )
                )
                print(f"✅ Colección {self.collection} creada")
        except Exception as e:
            print(f"⚠️ Error verificando colección: {e}")
    
    def _create_point(self, text: str, metadata: Dict[str, Any]) -> PointStruct:
        """Crea un punto para Qdrant a partir de texto y metadatos."""
        try:
            vector = self.embeddings.embed_query(text)
            point_id = str(uuid.uuid4())
            
            # Añadir timestamp si no existe
            if "timestamp" not in metadata:
                metadata["timestamp"] = datetime.now().isoformat()
            
            # Añadir texto al payload
            payload = {
                "page_content": text,
                **metadata
            }
            
            return PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
        except Exception as e:
            print(f"   ⚠️ Error creando punto: {e}")
            return None
    
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
    # INGESTA DESDE POSTGRESQL (TimescaleDB)
    # ============================================================
    
    def ingest_postgresql_sensors(self, limit: int = 100) -> int:
        """
        Ingiere datos de sensores desde PostgreSQL.
        """
        try:
            import psycopg2
            import psycopg2.extras
            
            print("\n📊 Ingestando datos de sensores desde PostgreSQL...")
            
            conn = psycopg2.connect(
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                database=settings.POSTGRES_DB
            )
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            # Verificar tablas existentes
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            tables = cursor.fetchall()
            table_names = [t['table_name'] for t in tables]
            print(f"   📋 Tablas disponibles: {', '.join(table_names[:10])}")
            
            # Buscar tabla de sensores o mediciones
            sensor_table = None
            for table in table_names:
                if any(kw in table.lower() for kw in ['sensor', 'measurement', 'telemetry', 'machine']):
                    sensor_table = table
                    break
            
            if not sensor_table:
                print("   ⚠️ No se encontró tabla de sensores/mediciones")
                print("   Creando tabla de ejemplo...")
                self._create_sample_sensor_table(conn, cursor)
                return 0
            
            print(f"   📊 Usando tabla: {sensor_table}")
            
            # Obtener datos
            query = f"""
                SELECT * FROM {sensor_table}
                ORDER BY id DESC
                LIMIT {limit}
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
                # Convertir fila a texto
                row_dict = dict(row)
                text = f"""
REGISTRO DE SENSOR:
"""
                for key, value in row_dict.items():
                    text += f"{key}: {value}\n"
                
                metadata = {
                    "entity_type": "sensor_data",
                    "source": "postgresql",
                    "table": sensor_table,
                    "database": settings.POSTGRES_DB
                }
                
                # Añadir campos clave como metadatos
                if 'id' in row_dict:
                    metadata["record_id"] = str(row_dict['id'])
                if 'machine_id' in row_dict:
                    metadata["machine_id"] = str(row_dict['machine_id'])
                
                point = self._create_point(text, metadata)
                if point:
                    points.append(point)
            
            return self._ingest_points(points, "PostgreSQL Sensors")
            
        except ImportError:
            print("   ❌ psycopg2 no está instalado. Instala: pip install psycopg2-binary")
            return 0
        except Exception as e:
            print(f"   ❌ Error ingiriendo desde PostgreSQL: {e}")
            return 0
    
    def _create_sample_sensor_table(self, conn, cursor):
        """Crea una tabla de ejemplo para sensores."""
        try:
            print("   📝 Creando tabla de ejemplo: sensor_readings")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id SERIAL PRIMARY KEY,
                    machine_id VARCHAR(50),
                    sensor_type VARCHAR(50),
                    value FLOAT,
                    unit VARCHAR(20),
                    status VARCHAR(20),
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Insertar datos de ejemplo
            cursor.execute("""
                INSERT INTO sensor_readings (machine_id, sensor_type, value, unit, status)
                VALUES 
                    ('CNC-001', 'temperature', 45.5, '°C', 'normal'),
                    ('CNC-001', 'vibration', 0.12, 'mm/s', 'normal'),
                    ('CNC-002', 'temperature', 52.3, '°C', 'warning'),
                    ('CNC-002', 'pressure', 78.5, 'bar', 'normal')
            """)
            conn.commit()
            print("   ✅ Tabla de ejemplo creada con datos de prueba")
        except Exception as e:
            print(f"   ⚠️ Error creando tabla de ejemplo: {e}")


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
    
    total = 0
    
    # 1. SQL Server - Clientes
    total += ingestor.ingest_sql_server_accounts(limit=50)
    
    # 2. SQL Server - Máquinas
    total += ingestor.ingest_sql_server_assets(limit=50)
    
    # 3. SQL Server - Actividades
    total += ingestor.ingest_sql_server_activities(limit=50)
    
    # 4. PostgreSQL - Sensores
    total += ingestor.ingest_postgresql_sensors(limit=50)
    
    print("\n" + "="*60)
    print(f"📊 TOTAL INGESTADO: {total} registros")
    print("="*60)
    
    return total


if __name__ == "__main__":
    ingest_from_database()