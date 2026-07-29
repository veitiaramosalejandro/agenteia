"""
Script para ingestar la estructura completa del sistema en Qdrant.
Ejecutar una vez para que el agente aprenda toda la configuración inicial.
"""

import os
import sys
import pymssql
from app.config import settings
from app.system.learning import SistemaAprendizaje
from app.system.schema import Canal, Actividad

def ingestar_sistema_completo():
    """Ingesta toda la estructura del sistema: canales, recursos y actividades históricas."""
    
    sistema = SistemaAprendizaje()
    
    print("🔄 Ingestando estructura del sistema...")
    
    try:
        conn = pymssql.connect(
            server=settings.SQL_SERVER_HOST,
            user=settings.SQL_SERVER_USER,
            password=settings.SQL_SERVER_PASSWORD,
            database=settings.SQL_SERVER_DB,
            timeout=5,
        )
        cursor = conn.cursor(as_dict=True)
        
        # 1. Ingestar todos los canales
        cursor.execute("""
            SELECT IDCanal, Nombre, Descripcion, Tipo
            FROM dbo.Canales
            WHERE Activo = 1
        """)
        canales = cursor.fetchall()
        
        for c in canales:
            canal = Canal(
                id=c["IDCanal"],
                nombre=c["Nombre"],
                descripcion=c["Descripcion"],
                tipo=c["Tipo"],
                recursos_humanos=[],
                recursos_materiales=[]
            )
            
            # Obtener recursos humanos del canal
            cursor.execute("""
                SELECT IDRecurso FROM dbo.RecursosHumanosCanales
                WHERE IDCanal = %s AND Activo = 1
            """, (c["IDCanal"],))
            canal.recursos_humanos = [r["IDRecurso"] for r in cursor.fetchall()]
            
            # Obtener recursos materiales del canal
            cursor.execute("""
                SELECT IDMaterial FROM dbo.RecursosMateriales
                WHERE IDCanal = %s AND Activo = 1
            """, (c["IDCanal"],))
            canal.recursos_materiales = [r["IDMaterial"] for r in cursor.fetchall()]
            
            sistema.aprender_canal(canal)
            print(f"  ✅ Canal '{c['Nombre']}' indexado")
        
        # 2. Ingestar actividades de los últimos 30 días
        print("\n🔄 Ingestando actividades recientes...")
        cursor.execute("""
            SELECT IDActividad, IDRecurso, IDCanal, Tipo, Descripcion, Fecha, Metadatos
            FROM dbo.Actividades
            WHERE Fecha > DATEADD(day, -30, GETDATE())
            ORDER BY Fecha DESC
        """)
        actividades = cursor.fetchall()
        
        contador = 0
        for a in actividades:
            actividad = Actividad(
                id=a["IDActividad"],
                recurso_humano_id=a["IDRecurso"],
                canal_id=a["IDCanal"],
                tipo=a["Tipo"],
                descripcion=a["Descripcion"],
                timestamp=a["Fecha"],
                metadatos=a["Metadatos"] if a.get("Metadatos") else {}
            )
            if sistema.aprender_actividad(actividad):
                contador += 1
        
        print(f"  ✅ {contador} actividades indexadas")
        
        conn.close()
        
        print("\n✅ ¡Sistema completamente ingerido!")
        print(f"   - {len(canales)} canales")
        print(f"   - {contador} actividades")
        return {
            "canales": len(canales),
            "actividades": contador,
        }
        
    except Exception as e:
        print(f"❌ Error en la ingesta: {e}")
        raise

if __name__ == "__main__":
    try:
        ingestar_sistema_completo()
    except Exception:
        sys.exit(1)