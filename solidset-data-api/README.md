# SolidSET Data API

Serviço independente e somente de leitura que deve ser instalado junto ao SQL
Server de cada instância SolidSET. O agente comunica por HTTPS e não precisa de
credenciais nem conectividade direta com SQL Server.

## Execução independente

Desde este diretório, sem utilizar o Compose do agente:

```powershell
Copy-Item .env.example .env
# Editar .env e configurar SQL_SERVER_* e SOLIDSET_DATA_API_KEY.
docker compose up -d --build
docker compose ps
```

Saúde, logs e paragem:

```powershell
Invoke-RestMethod http://localhost:8081/health
docker compose logs -f data-api
docker compose down
```

O Compose cria apenas o serviço `solidset_data_api` e a rede
`solidset-data-network`. Não depende de PostgreSQL, Redis, Qdrant, Ollama nem
dos contentores do agente.

Teste:

```powershell
Invoke-RestMethod http://localhost:8081/api/v1/system/capabilities `
  -Headers @{ "X-SolidSET-Data-Key" = "replace-with-a-long-random-secret" }
```

Endpoints:

- `GET /api/v1/system/capabilities`
- `GET /api/v1/datasets/resources`
- `GET /api/v1/datasets/logins`
- `GET /api/v1/datasets/workrooms`
- `GET /api/v1/datasets/workroom-resources`
- `GET /api/v1/agents/{humanResourceId}`
- `POST /api/v1/query/read`

As consultas estruturais ficam no catálogo interno `app/queries.py`. O endpoint
`query/read` cobre consultas históricas e de aprendizagem que são construídas
dinamicamente conforme a versão do esquema: aceita apenas `SELECT`/CTE,
parâmetros e um limite de linhas. A conta SQL configurada deve ter permissões
exclusivamente de leitura.
