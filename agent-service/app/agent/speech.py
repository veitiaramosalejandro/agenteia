import os
import uuid
from gtts import gTTS

def text_to_speech(text: str, output_dir: str = "/tmp") -> str:
    """Convierte texto en audio MP3 y devuelve la ruta del archivo generado."""
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, f"response_{uuid.uuid4().hex[:8]}.mp3")
    
    tts = gTTS(text=text, lang="es", slow=False)
    tts.save(file_path)
    
    return file_path