"""
app/rag/sql_ingest.py - Ingesta de datos desde SQL Server
"""

import uuid
import hashlib
import pymssql
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings

from app.config import settings


def ingest_sql_knowledge():
    """
    Ingiere conocimiento estructurado desde SQL Server.
    Extrae datos de clientes, actividades, máquinas, etc.
    """
    try:
        conn = pymssql.connect(
            server=settings.SQL_SERVER_HOST,
            port=settings.SQL_SERVER_PORT,
            user=settings.SQL_SERVER_USER,
            password=settings.SQL_SERVER_PASSWORD,
            database=settings.SQL_SERVER_DB,
            timeout=10
        )
        cursor = conn.cursor(as_dict=True)
        
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        
        total_points = 0
        
        # 1. INGESTAR CLIENTES / CUENTAS
        print("📊 Ingestando clientes...")
        cursor.execute("""
            SELECT TOP 100 
                IDAccount, Name, City, Country, 
                TotalValueDebt, TotalValueFinAct,
                Classification
            FROM dbo.Account
            WHERE Active = 1
        """)
        accounts = cursor.fetchall()
        
        for acc in accounts:
            text = f"""
            Cliente: {acc['Name']}
            Ubicación: {acc['City']}, {acc['Country']}
            Deuda: ${acc['TotalValueDebt']:,.2f}
            Financiamiento: ${acc['TotalValueFinAct']:,.2f}
            Clasificación: {acc['Classification']}
            """
            
            vector = embeddings.embed_query(text)
            point_id = f"account_{hashlib.md5(str(acc['IDAccount']).encode()).hexdigest()[:8]}"
            
            point = PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "page_content": text,
                    "entity_type": "account",
                    "account_id": acc['IDAccount'],
                    "name": acc['Name'],
                    "source": "sql_server"
                }
            )
            client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, points=[point])
            total_points += 1
        
        print(f"  ✅ {total_points} clientes indexados")
        
        # 2. INGESTAR MÁQUINAS / ACTIVOS
        print("🔧 Ingestando máquinas...")
        cursor.execute("""
            SELECT TOP 100 
                IDAsset, Name, Model, SerialNumber,
                Status, Location, InstallationDate
            FROM dbo.Asset
            WHERE Active = 1 AND AssetType = 'machine'
        """)
        assets = cursor.fetchall()
        
        machine_count = 0
        for asset in assets:
            text = f"""
            Máquina: {asset['Name']}
            Modelo: {asset['Model']}
            Serial: {asset['SerialNumber']}
            Estado: {asset['Status']}
            Ubicación: {asset['Location']}
            Instalación: {asset['InstallationDate']}
            """
            
            vector = embeddings.embed_query(text)
            point_id = f"asset_{hashlib.md5(str(asset['IDAsset']).encode()).hexdigest()[:8]}"
            
            point = PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "page_content": text,
                    "entity_type": "asset",
                    "asset_id": asset['IDAsset'],
                    "name": asset['Name'],
                    "source": "sql_server"
                }
            )
            client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, points=[point])
            machine_count += 1
        
        print(f"  ✅ {machine_count} máquinas indexadas")
        
        conn.close()
        print(f"\n✅ Total indexado: {total_points + machine_count} entidades")
        
    except Exception as e:
        print(f"❌ Error ingiriendo datos SQL: {e}")
