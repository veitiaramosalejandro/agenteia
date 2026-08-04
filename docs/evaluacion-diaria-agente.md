# Plantilla de Evaluacion Diaria del Agente

## Objetivo
Medir si el agente mejora en:
- Calidad tecnica de consumo API.
- Calidad de respuesta y aprendizaje operativo en canal/chat.

Usa siempre el mismo set de preguntas y mismo canal para comparar periodos.

## Frecuencia recomendada
- Diario: 1 corrida completa.
- Semanal: consolidado de 7 dias.

## Fuentes de datos
- Endpoint resumen: /api/v1/agent/evaluation/summary
- Endpoint salud: /api/v1/agent/health
- Verificacion de captura: /api/v1/agent/notification/recent-messages?limit=30

## Set fijo de pruebas funcionales
Define 20 preguntas reales y no las cambies durante la semana.

Ejemplo de categorias:
1. Ultimos mensajes del canal.
2. Identificacion de recurso por alias (ej: Dev17).
3. Preguntas sobre relacion usuario-canal.
4. Preguntas sobre historial reciente.
5. Preguntas abiertas de contexto operativo.

## Checklist diario
Fecha: ____/____/______
Canal objetivo: __________________________
Sesion usada: ____________________________

### A. Exactitud funcional
- Total preguntas del set: 20
- Respuestas correctas: ______
- Accuracy diaria: ______ %

Formula:
Accuracy diaria = (Respuestas correctas / Total preguntas) x 100

### B. Rendimiento de dialogo
- Tiempo promedio de respuesta (s): ______
- Tiempo maximo de respuesta (s): ______
- Dialogos lentos detectados: ______

### C. Calidad tecnica de API
Tomar de diagnostico_tecnico.api_runtime y runtime.notification_api_metrics:
- calls_total: ______
- calls_error: ______
- http_4xx: ______
- http_5xx: ______
- rate_limited_429: ______
- timeouts: ______
- avg_latency_ms: ______
- max_latency_ms: ______

### D. Evolucion de aprendizaje
Tomar de metricas_evolucion.learning_runtime:
- cycles: ______
- success_ratio: ______
- avg_learned_per_cycle: ______
- avg_errors_per_cycle: ______
- learning_velocity_per_minute: ______
- recent_trend: ______

### E. Captura real de mensajes de canal/chat
- Se observan mensajes nuevos en recent-messages: Si / No
- Coinciden remitente, canal y texto con UI: Si / No
- Numero de discrepancias detectadas hoy: ______

### F. Incidencias del dia
- Error principal observado: __________________________________________
- Impacto en usuario final: __________________________________________
- Accion correctiva aplicada: _________________________________________

## Semaforo diario
- Verde:
  - Accuracy >= 85%
  - success_ratio >= 0.90
  - timeouts <= 2
  - avg_errors_per_cycle en descenso
- Amarillo:
  - Accuracy entre 70% y 84%
  - o timeouts entre 3 y 8
  - o picos de latencia sin caida funcional grave
- Rojo:
  - Accuracy < 70%
  - o errores de captura en canal/chat
  - o timeouts > 8
  - o dialogos bloqueados/lentitud grave

## Consolidado semanal
Para cada metrica, comparar promedio de la semana actual vs semana anterior:
- Accuracy promedio.
- Tiempo promedio de respuesta.
- success_ratio.
- avg_learned_per_cycle.
- avg_errors_per_cycle.
- timeouts y 429 totales.

Regla de mejora real:
El agente mejora si en la semana actual:
- Accuracy sube,
- errores/timeout bajan,
- latencia no empeora de forma significativa,
- y recent_trend se mantiene en mejorando o estable con mejor exactitud.

## Ejemplo rapido (relleno)
- Accuracy diaria: 88%
- Tiempo promedio: 6.4 s
- timeouts: 1
- success_ratio: 0.94
- avg_learned_per_cycle: 331
- avg_errors_per_cycle: 1.7
- recent_trend: mejorando
- Semaforo: Verde
