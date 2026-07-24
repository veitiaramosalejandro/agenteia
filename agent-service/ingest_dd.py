import os
import sys

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.rag.dd_ingest import ingest_data_dictionary, check_db_knowledge


if __name__ == "__main__":
    print("\n" + "="*60)
    print("📚 INGESTA DE DATA DICTIONARY")
    print("="*60)
    
    # Ejecutar ingesta
    total = ingest_data_dictionary()
    
    # Verificar
    check_db_knowledge()
    
    print(f"\n✅ Ingesta completada. Total: {total} puntos indexados")