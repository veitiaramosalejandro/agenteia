import os
import time

from app.rag.embeddings import ingestar_audios_a_qdrant

AUDIO_DIR = os.getenv("AUDIO_DIR", "/app/audio")
INTERVAL = int(os.getenv("INGEST_INTERVAL_SECONDS", "3600"))


def main() -> None:
    pass
    pass
    pass

    while True:
        try:
            ingestar_audios_a_qdrant(audio_dir=AUDIO_DIR)
        except Exception as exc:
            pass

        pass
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
