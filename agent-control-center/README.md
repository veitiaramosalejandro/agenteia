# Agent Control Center

Consola web para administrar instancias SolidSET, agentes especializados, prompts, modelos y pruebas de diálogo.

## Desarrollo

```bash
npm install
npm run dev
```

Vite publica la consola en `http://localhost:4173` y redirige `/api` a `http://localhost:8000`.

## Docker

```bash
docker compose -f docker-compose-dev.yml up -d --build agent-control-center
```

La consola queda disponible en `http://localhost:4173`.

## Alcance de la fase 1

- Selector persistente y obligatorio de instancia.
- Panel de estado del runtime.
- Inventario de instancias y prueba de conectividad.
- Listado de agentes aislado por instancia.
- Generación, previsualización y publicación de prompts.
- Asignación de modelos y capacidades.
- Laboratorio de diálogo sin publicación en SolidSET.

Las credenciales de proveedores nunca se muestran. La autenticación administrativa y auditoría completa pertenecen a la fase de gobierno.
