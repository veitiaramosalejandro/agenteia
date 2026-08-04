"""
train_context.py - Script para entrenamiento manual del contexto del agente.

Este script se conecta a la base de datos, extrae información clave sobre
canales de trabajo, actividad de chat y recursos, y la utiliza para
entrenar al Sistema de Aprendizaje del agente.

Esto permite que el agente tenga un conocimiento profundo y actualizado de la
dinámica de trabajo, incluso antes de que un usuario interactúe con él.
"""

import pymssql
import sys
import os
from datetime import datetime

# Añadir el directorio de la aplicación al path para poder importar los módulos
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from system.learning import SistemaAprendizaje
from system.schema import Actividad, Canal
from config import settings


def train_channels(sa: SistemaAprendizaje, cursor: pymssql.Cursor):
    """
    Obtiene los canales de trabajo (WorkRooms) y entrena al sistema con ellos.
    """
    print("--- Iniciando entrenamiento de Canales ---")
    try:
        cursor.execute("""
            SELECT 
                wr.IDWorkRoom,
                wr.Name,
                wr.Description,
                wr.Kind,
                (SELECT STRING_AGG(CONVERT(varchar(36), wrr.IDResource), ',')
                 FROM dbo.SysWorkRoomResource wrr
                 WHERE wrr.IDWorkRoom = wr.IDWorkRoom) AS Members
            FROM dbo.SysWorkRoom wr
            WHERE wr.IsActive = 1
        """)
        
        canales_rows = cursor.fetchall() or []
        print(f"Se encontraron {len(canales_rows)} canales para entrenar.")
        
        count = 0
        for row in canales_rows:
            canal_id = str(row.get("IDWorkRoom"))
            miembros = (row.get("Members") or "").split(',')
            miembros_limpios = [m.strip() for m in miembros if m.strip()]

            canal = Canal(
                id=canal_id,
                nombre=(row.get("Name") or f"Canal {canal_id[:8]}").strip(),
                descripcion=(row.get("Description") or "Canal de trabajo").strip(),
                tipo=f"workroom_{row.get('Kind')}",
                recursos_humanos=miembros_limpios,
                recursos_materiales=[], # Se podrían añadir si hubiera otra tabla
                proyectos_activos=[]
            )
            
            if sa.aprender_canal(canal):
                count += 1
                print(f"  -> Aprendiendo de canal: {canal.nombre}")

        print(f"✅ Entrenamiento de canales finalizado. Se aprendió de {count} canales.")
    except Exception as e:
        print(f"❌ Error durante el entrenamiento de canales: {e}")


def train_chat_activity(sa: SistemaAprendizaje, cursor: pymssql.Cursor, limit: int = 500):
    """
    Obtiene la actividad de chat reciente y la usa para entrenar al sistema.
    """
    print(f"--- Iniciando entrenamiento de Actividad de Chat (últimos {limit} mensajes) ---")
    try:
        cursor.execute(f"""
            SELECT TOP {limit}
                c.IDChat2,
                c.Stamp,
                c.RawMessage,
                c.CreatedBy,
                COALESCE(c.IDWorkRoom, c2w.IDWorkRoom) as IDChannel,
                (SELECT TOP 1 c2rsc.IDResource 
                 FROM dbo.SysChat2SysResource c2rsc 
                 WHERE c2rsc.IDChat = c.IDChat2) as IDResource
            FROM dbo.SysChat c
            LEFT JOIN dbo.SysChat2SysWorkRoom c2w ON c2w.IDChat2 = c.IDChat2
            WHERE c.RawMessage IS NOT NULL AND c.RawMessage != ''
            ORDER BY c.Stamp DESC
        """)
        
        actividades_rows = cursor.fetchall() or []
        print(f"Se encontraron {len(actividades_rows)} actividades de chat para entrenar.")
        
        count = 0
        for row in actividades_rows:
            actividad_id = str(row.get("IDChat2"))
            mensaje = (row.get("RawMessage") or "").strip()
            
            # El recurso humano puede ser el 'CreatedBy' o el que esté ligado al chat
            recurso_id = str(row.get("IDResource") or row.get("CreatedBy") or "sistema").strip()
            
            actividad = Actividad(
                id=actividad_id,
                recurso_humano_id=recurso_id,
                canal_id=str(row.get("IDChannel") or "general"),
                tipo="chat_message",
                descripcion=mensaje,
                timestamp=row.get("Stamp") or datetime.now(),
                metadatos={
                    "source_table": "SysChat",
                    "manual_training": True
                }
            )

            if sa.aprender_actividad(actividad):
                count += 1
                print(f"  -> Aprendiendo de chat en canal {actividad.canal_id}: '{mensaje[:50]}...'")

        print(f"✅ Entrenamiento de actividad finalizado. Se aprendió de {count} actividades.")
    except Exception as e:
        print(f"❌ Error durante el entrenamiento de actividad de chat: {e}")


def main():
    """
    Función principal que orquesta el proceso de entrenamiento.
    """
    print("======================================================")
    print("🚀  INICIANDO SCRIPT DE ENTRENAMIENTO MANUAL DE CONTEXTO 🚀")
    print("======================================================")
    
    sa = None
    conn = None
    
    try:
        # 1. Inicializar el Sistema de Aprendizaje
        print("1. Inicializando Sistema de Aprendizaje...")
        sa = SistemaAprendizaje()
        print("   ✅ Sistema de Aprendizaje listo.")
        
        # 2. Conectar a la base de datos
        print("2. Conectando a la base de datos SQL Server...")
        conn = pymssql.connect(
            server=settings.SQL_SERVER_HOST,
            user=settings.SQL_SERVER_USER,
            password=settings.SQL_SERVER_PASSWORD,
            database=settings.SQL_SERVER_DB,
            timeout=15
        )
        cursor = conn.cursor(as_dict=True)
        print("   ✅ Conexión a la base de datos establecida.")
        
        # 3. Ejecutar los módulos de entrenamiento
        train_channels(sa, cursor)
        train_chat_activity(sa, cursor, limit=1000) # Se puede ajustar el límite de mensajes
        
    except Exception as e:
        print(f"❌ Ha ocurrido un error fatal en el script: {e}")
    finally:
        if conn:
            conn.close()
            print("⏹️  Conexión a la base de datos cerrada.")
    
    print("======================================================")
    print("🏁  ENTRENAMIENTO MANUAL FINALIZADO  🏁")
    print("======================================================")

if __name__ == "__main__":
    main()
