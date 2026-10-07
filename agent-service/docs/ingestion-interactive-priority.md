# Prioridad del chat sobre DB_STUDY

Implementación local pendiente de validación. No se han ejecutado pruebas ni actualizado el servidor.

- `DB_STUDY_INTERVAL_SECONDS=0` desactiva el planificador. El valor predeterminado y los archivos locales `.env`, `.env.production` y `.env.example` quedan en cero.
- Si se habilita expresamente un intervalo positivo, DB_STUDY usa un executor exclusivo con un solo hilo, de prioridad reducida en Linux. No usa el pool de hilos del chat.
- Un mutex renovable de Redis impide ejecutar varios ciclos de estudio simultáneos. El chat nunca espera ese mutex: publica actividad y la ingesta cede en sus puntos de control.
- La actividad cubre diálogo HTTP, notificaciones recibidas, captura en el worker, enrutamiento de auto-respuesta y generación de sugerencias. El enrutamiento síncrono y la captura se ejecutan fuera del event loop.
- Las ingestas no reanudan hasta transcurridos al menos 30 segundos desde el fin de la última petición (`INGESTION_INTERACTIVE_IDLE_SECONDS`). Si Redis no permite comprobar la inactividad, la ingesta espera; el chat puede continuar.
- La ingesta de estructura comprueba la prioridad antes de cada consulta, registro, embedding y escritura vectorial. Las ingestas histórica y de conocimiento de sistema utilizan sublotes de 10 documentos y puntos de pausa entre etapas.
- El timeout de DB_STUDY solicita cancelación cooperativa y espera que termine el hilo antes de programar otro ciclo. Las conexiones se cierran también al cancelar o fallar.

La pausa no aborta una operación remota que ya está ejecutándose. DB_STUDY usa timeouts de I/O de 10 segundos para Data API, embeddings y Qdrant; estos son límites de espera de los clientes, no garantías de que el servidor remoto haya cancelado su trabajo. La siguiente operación no se inicia mientras haya actividad interactiva.

## Comprobaciones para el usuario

1. Con intervalo cero, reiniciar y comprobar el mensaje de ciclo desactivado y ausencia de ingestas programadas.
2. En un entorno de prueba, habilitar un intervalo positivo y enviar una notificación durante la ingesta. Verificar que el enrutamiento comienza sin esperar al ciclo, la ingesta pausa y reanuda después de 30 segundos sin actividad.
3. Repetir con un diálogo, una sugerencia encolada y dos peticiones solapadas: la primera finalización no debe permitir reanudar mientras la segunda siga activa.
4. Simular un timeout y comprobar que no empieza otro ciclo mientras termina el anterior. Repetir con varios procesos API y verificar exclusión mediante el mutex.
5. Interrumpir Redis durante la ingesta: comprobar que esta espera y que la señal local sigue protegiendo el chat; recuperar Redis y comprobar la reanudación.
6. Ejecutar los tests de prioridad existentes, adaptados al período de inactividad. Comparar latencias de enrutamiento antes/después; `ejecuciones=0` por sí solo no demuestra bloqueo, también puede indicar que ningún candidato cumple las reglas de enrutamiento.
