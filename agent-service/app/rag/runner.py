import logging
from app.rag.auto_ingest import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
