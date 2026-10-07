import os
import numpy as np
import librosa

def load_pcm_audio(file_path: str, sample_rate: int = 16000, dtype=np.int16) -> np.ndarray:
    """Lee un archivo .pcm crudo y lo normaliza a valores de float32 [-1.0, 1.0]."""
    with open(file_path, 'rb') as f:
        raw_data = f.read()
    
    # Convertir bytes a arreglo numpy 16-bit
    audio_int16 = np.frombuffer(raw_data, dtype=dtype)
    
    # Normalizar float32 para procesar con librosa
    audio_float32 = audio_int16.astype(np.float32) / 32768.0
    return audio_float32, sample_rate

def extract_audio_features(file_path: str, sample_rate: int = 16000) -> dict:
    """Extrae métricas acústicas de desgaste/anomalías a partir del archivo .pcm."""
    y, sr = load_pcm_audio(file_path, sample_rate=sample_rate)
    
    # 1. Extracción de MFCCs (Firma espectral del sonido)
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_mean = np.mean(mfccs, axis=1)
    
    # 2. Espectro y Ruido (Ancho de banda espectral y energía RMS)
    rms_energy = float(np.mean(librosa.feature.rms(y=y)))
    spectral_centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    
    # Sintetizar un resumen descriptivo en texto para el RAG / LLM
    text_summary = (
        f"Análisis de Audio PCM ({os.path.basename(file_path)}): "
        f"Energía RMS promedio: {rms_energy:.4f}, "
        f"Centroide Espectral (Frecuencia dominante promedio): {spectral_centroid:.2f} Hz. "
        f"Valores base MFCC: {np.array2string(mfcc_mean[:5], precision=2)}."
    )
    
    return {
        "file_name": os.path.basename(file_path),
        "text_summary": text_summary,
        "rms_energy": rms_energy,
        "spectral_centroid": spectral_centroid,
        "mfcc_vector": mfcc_mean.tolist()
    }