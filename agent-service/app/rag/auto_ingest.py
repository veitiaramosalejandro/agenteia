import os
import time
import logging

from app.rag.embeddings import ingestar_audios_a_qdrant

AUDIO_DIR = os.getenv("AUDIO_DIR", "/app/audio")
INTERVAL = int(os.getenv("INGEST_INTERVAL_SECONDS", "3600"))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.info("Iniciando ingesta automática de audios a Qdrant")
    logging.info("Directorio de audio: %s", AUDIO_DIR)
    logging.info("Intervalo de ingesta: %s segundos", INTERVAL)

    while True:
        try:
            ingestar_audios_a_qdrant(audio_dir=AUDIO_DIR)
        except Exception as exc:
            logging.error("Error en la ingesta automática: %s", exc, exc_info=True)

        logging.info("Esperando %s segundos antes de la próxima iteración.", INTERVAL)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
