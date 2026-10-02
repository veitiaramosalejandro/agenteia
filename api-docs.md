#API do Agente SolidSET

> Ambiente de desenvolvimento Docker: a API corre com o Python 3.11 e o código do serviço mantém compatibilidade sintáctica com esta versão.

O diagnóstico de arranque e o campo `runtime.startup_connectivity` do `GET /api/v1/agent/health` obtêm instalações ativas diretamente do PostgreSQL `SysSolidSETInstance`. Para cada linha verificam `BaseUrl` e `NotificationUrl` e reportam `Code` , `SourceIP` , URL configurado e URL efectivo. Dentro do Docker, é testado um URL configurado com `localhost` utilizando `host.docker.internal` , sem modificar o valor persistido. As variáveis ​​​​históricas `SOLIDSET_RESTAPI_BASE_URL` e `NOTIF_API_BASE_URL` não determinam este diagnóstico multi-instância.

Os endpoints de notificação resolvem a instância utilizando o cabeçalho
 `X-SolidSET-Instance`. O seu valor é o host de destino configurado em
 `SysSolidSETInstance.SourceIP`, e não o `Code`. Host ou host:port é suportado;
a comparação ignora as maiúsculas e um ponto final.  `localhost`, `127.0.0.1` e
 `::1` são considerados equivalentes para esta pesquisa. Se estiver em falta, não corresponder ou
corresponder a várias instâncias ativas, o pedido será rejeitado com HTTP 400.
O IP de entrada, o `X-Forwarded-For` e o host HTTP não são utilizados para identificar o
instância. O processo que envia o `FrameworkMessage` a partir do SolidSET deve
adicionar cabeçalho; o cliente WPF não pode fazer com que apareça nesse
pedido se enviado por outro serviço.
Log `API_REQUEST` regista `solidset_instance_header=present|missing` para
verifique se chegou, sem imprimir o seu valor. O cabeçalho seleciona o
instância e não substitui a autenticação do emissor.
Os endpoints de notificação registam o `INITIAL_REQUEST_IDENTITY` antes
resolver a instância, com `X-SolidSET-Instance` , cabeçalhos de rede permitidos,
 `Sender.session` , `Info.session_id` , identidade do remetente e chat. O recorde
inclui os nomes dos outros cabeçalhos, sem valores de cookies ou palavras-passe.
autorização. Após a sua resolução, `INITIAL_REQUEST_DESTINATION` exibe `Code`,
 `SourceIP`, as fontes dos URLs de resposta e o acesso à API de dados, sem
credenciais ou rotas.

As saudações diretas (`hola`, `hola como estás` e equivalentes) são respondidas
imediatamente, com respeito e utilizando apenas o `FullName` do remetente;
nenhum perfil ou canal é mostrado e nenhuma espera pelo LLM. Quando uma pessoa
fale com o seu próprio agente, incluindo uma conversa em reunião, utilizações de remessa
o UUID interno do `SysResourceIA` como identidade visual do agente para que o
resposta surge como um interlocutor diferente. O log reporta as fases
 `encolada` , `iniciando` , `enrutamiento completado` e o resultado do envio.
O envio para o SolidSET tem prioridade sobre a aprendizagem da interação:
session e Qdrant são atualizados após a publicação, têm tempos máximos e as suas falhas não impedem a publicação do
resposta. Antes do login, o `base=<BaseUrl>` é registado para mostrar qual o URL
 `SysSolidSETInstance` está a ser utilizado. Dentro do Docker, um SolidSET
executado no host deve ser configurado como
 `http://host.docker.internal:52130`, diferente de `http://localhost:52130`.
O método de envio regista a sua entrada antes de validar a reunião. Se receber
um URL localhost no Docker, teste primeiro a sua tradução para
 `host.docker.internal` e evita esperar por um tempo limite no próprio contentor.
A tradução preserva a ligação HTTP original: liga-se via TCP ao
 `host.docker.internal` , mas envia `Host: localhost:52130` . Isso permite que use
 `BaseUrl=http://localhost:52130` em `SysSolidSETInstance` quando o IIS rejeita
outros nomes com `400 Bad Request - Invalid Hostname`.
As tentativas de envio registam a leitura do `SysLogin`, cada chamada para
 `LoginJson` e a resposta HTTP de `/Chat/SendMessageForm`, não impressa
senhas. O perfil de desenvolvimento utiliza `qwen2.5:3b` com contexto 2048
para reduzir o consumo de memória; a produção mantém o seu modelo configurável.
O login contextual envia o hash persistente com o `PasswordEncrypted=true` e o
nome exato `TimezoneId`. Não envia `Resources[0]`: no driver C# que
a coleção é opcional e o recurso atual é selecionado utilizando
 `SysLogin.LastIDResource`. As rejeições HTTP apresentam até 500 caracteres do
body para diagnosticar o ModelState sem registar credenciais.

No Docker, o Nginx publica a API utilizando o `http://android.isicom.pt/` e encaminha internamente para o `http://agent-service:8000`. Portanto, os terminais mantêm as suas rotas; Por exemplo, o Health está disponível em `http://android.isicom.pt/api/v1/agent/health` e o Swagger em `http://android.isicom.pt/docs`. O caminho técnico `GET /nginx-health` verifica apenas o proxy.
Para HTTPS, o `scripts/issue-letsencrypt.ps1 -Email <correo>` executa o Certbot via webroot, emite o certificado de `android.isicom.pt` e ativa o host virtual TLS na porta 443. O desafio `/.well-known/acme-challenge/` continua acessível por HTTP para renovações.  `scripts/renew-letsencrypt.ps1` renova os certificados expirados e recarrega o Nginx. O DNS público deve apontar para o servidor e o NAT/firewall deve suportar a entrada TCP 80 e 443.

Caso o HTTP-01 não consiga atravessar o NAT/firewall, o `scripts/issue-letsencrypt-dns.ps1 -Email <correo>` permite a emissão via DNS-01 manual criando um TXT em `_acme-challenge.android.isicom.pt` . Esta variante não tem renovação autónoma: deve ser repetida antes de expirar ou substituída por um plugin/API do fornecedor de DNS.

Como alternativa apenas interna, o `scripts/issue-internal-certificate.ps1` cria uma CA privada `ISICOM Internal Root CA`, emite um certificado com SAN `android.isicom.pt` e activa o HTTPS no Nginx. Os clientes devem instalar o `certbot/internal/isicom-internal-ca.crt` no seu armazenamento de autoridade raiz. A chave `isicom-internal-ca.key` é sensível, não deve ser distribuída e deve ser mantida fora do servidor após a emissão dos certificados necessários.

A resolução de identidade habitual (`Username`, `FullName`, `IDLogin`, `IDResource`) utiliza exclusivamente a réplica `SysLogin` PostgreSQL. Todas as leituras do SQL Server – sincronização, histórico, validação, aprendizagem e consultas operacionais – são realizadas utilizando a API SolidSET Data independente. O agente não abre ligações TCP com o SQL Server nem utiliza as suas variáveis ​​de ligação. No perfil de CPU de produção, o Ollama utiliza `OLLAMA_KV_CACHE_TYPE=f16` porque um cache V quantizado requer Atenção Flash.

Cada instância configura a sua gateway para o PostgreSQL `SysSolidSETDataAPI` . A URL,
O tempo limite, o limite e a validação do TLS são guardados por instância; a chave API é encriptada e
Nunca é devolvido. As credenciais do SQL Server existem apenas no ficheiro
ambiente do projeto independente `solidset-data-api`, exposto junto ao
servidor de base de dados.

A implementação do `docker-compose-prod.yml` utiliza o Ollama por CPU por predefinição e não requer o tempo de execução NVIDIA. Quando o `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` funciona corretamente, a aceleração é ativada adicionando a sobreposição `docker-compose-prod.gpu.yml`. Na produção, o Uvicorn funciona sem `--reload`.

A implementação do `docker-compose-dev.yml` replica a topologia funcional do
produção, mas publica exclusivamente HTTP nas portas 80 e 8000, não
Não inclui Certbot nem certificados de montagem. Preservar a montagem do código-fonte e
Uvicorn `--reload` para desenvolvimento. Também ansioso pela saúde do PostgreSQL,
Redis, Ollama e Qdrant, utilizam o perfil de CPU seguro e resolvem o SolidSET de
 `SysSolidSETInstance` e as identidades de `SysLogin` no PostgreSQL.

Última atualização: 22 de agosto de 2026.

> Este documento deve ser atualizado na mesma alteração que modifica uma rota, método HTTP, contrato de entrada, resposta ou comportamento observável da API.

Atualmente a API expõe 25 endpoints funcionais. Poderá sempre consultar a documentação interactiva em:

```text
http://localhost:8000/docs
```
## Registo IP por solicitação

Todos os pedidos HTTP, independentemente do endpoint, geram uma linha na consola de serviço com o IP TCP direto, o primeiro IP declarado pelo proxy, método, rota, estado e duração. Exemplo:

```text
🌐 API_REQUEST ip=127.0.0.1 forwarded_ip=- method=POST endpoint=/api/v1/dialogue status=200 duration_ms=84.2
```
`ip` é a ligação observada pelo FastAPI e não pode ser substituída por cabeçalhos.  `forwarded_ip` apresenta o primeiro valor de `X-Forwarded-For` ou `X-Real-IP` em separado; O IP real do cliente só deve ser considerado quando o proxy que configura estes cabeçalhos for fiável. Nenhum corpo ou parâmetro de consulta é registado.

## Fluxo principal recomendado

A operação normal seria:

```text
1. Sincronizar recursos desde SQL Server
2. Sincronizar las cuentas SysLogin
3. Sincronizar el catálogo SysWorkRoom
4. Sincronizar las relaciones recurso–canal
5. Configurar qué recursos son agentes IA
6. Activar agentes dentro de sus canales
7. Cargar conocimiento privado para cada agente
8. SolidSET selecciona uno o varios agentes
9. Ejecutar el diálogo multiagente
10. Cada agente responde con memoria y conocimiento independientes
```
# Configuração multiagente

Em produção, docker-compose-prod.yml carrega .env.production. Este ficheiro
contém apenas o modelo substituto e o segredo mestre de encriptação; o
as alocações dinâmicas são lidas no PostgreSQL utilizando
SysLLMProviderConfiguration e SysAgentIAModel. Deve ser mantido fora
controlo de versão e copiado ao lado do Compose durante a implementação.

## Fornecedores LLM intercambiáveis

A lógica SolidSET depende de uma interface de modelo de chat comum
( `invoke` e `bind_tools` ) e não instancia directamente Ollama. Registo em
 `app/llm/providers.py` inclui os identificadores:

- `ollama` (implementado e ativo por defeito).
-`openai` .
-`azure_openai` .
-`anthropic` .
-`gemini` .
- `openai_compatible` ou `local_openai` para servidores compatíveis com API
  da OpenAI.

As seguintes variáveis são apenas o backup de arranque quando ainda
Não existe configuração ativa no PostgreSQL:

```env
LLM_PROVIDER=ollama
MODEL_NAME=qwen2.5:3b
LLM_BASE_URL=
LLM_API_KEY=
LLM_TEMPERATURE=0.5
LLM_MAX_OUTPUT_TOKENS=1024
LLM_REQUEST_TIMEOUT_SECONDS=900
```
Para o Azure são também utilizados:

```env
AZURE_OPENAI_ENDPOINT=https://<recurso>.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=<deployment>
```
Os fornecedores remotos carregam lentamente as suas integrações. Só tem que
o pacote correspondente será instalado quando ativado: `langchain-openai` ,
 `langchain-anthropic` ou `langchain-google-genai`. Se estiver em falta, inicialize os relatórios
o pacote exato necessário. As chaves nunca são incluídas na integridade ou nos registos.

### Configuração persistente no PostgreSQL

A tabela `SysLLMProviderConfiguration` é a fonte canónica do modelo de chat.
Suporta múltiplas configurações, um padrão global e uma configuração
ativo específico da `SysResourceIA.IDResource`. Em cada conversa o router
Resolve primeiro a configuração do agente solicitado e, caso não exista, utiliza o
globais; só depois recorre a `.env` . As alterações serão aplicadas na próxima
pedido sem reiniciar o contentor.

As chaves API são armazenadas encriptadas com Fernet e nunca aparecem nas respostas da API.
A chave mestra é mantida como segredo de implantação:

```env
LLM_CREDENTIAL_ENCRYPTION_KEY=<clave-fernet>
```
É gerado uma vez com:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Não deve ser alterado após guardar as credenciais.

#### Registe ou atualize um fornecedor

```http
PUT /api/v1/agent/llm/providers/{code}
```
Exemplo global com Ollama:

```json
{
  "Code": "ollama-default",
  "Name": "Ollama coordinador",
  "Provider": "ollama",
  "Model": "qwen2.5:3b",
  "BaseUrl": "http://ollama-llm:11434",
  "Temperature": 0.5,
  "MaxOutputTokens": 1024,
  "TimeoutSeconds": 900,
  "IDResource": null,
  "IsDefault": true,
  "active": true
}
```
Para atribuir outro fornecedor a um agente específico, digite `IDResource`; em
neste caso `IsDefault` está normalizado para `false` . Um `PUT` com `APIKey=null` retém
a credencial existente. Apenas uma configuração global pode existir
padrão ativo e uma configuração ativa por recurso.

```http
GET /api/v1/agent/llm/providers
DELETE /api/v1/agent/llm/providers/{code}
```
A listagem retorna `HasAPIKey` , mas nunca `APIKey` .  `DELETE` faz um levantamento
lógica (`active=false`).

Ollama continua a ser necessário para as incorporações Qdrant embora o
o modelo de conversação é remoto.  `GET /api/v1/agent/health` expõe fornecedor,
modelo, URL e `source=postgresql|environment_fallback` separadamente do
 `ollama_embeddings`.

### Modelo atribuído a cada agente: SysAgentIAModel

A seleção oficial do SolidSET mantém-se `Chat.destiny[].talkWithAgent=true`.
O `IDResource` selecionado pode ter várias linhas ativas em `SysAgentIAModel` .
O router classifica cada mensagem e escolhe uma linha cuja coleção `Capabilities`
contém a capacidade necessária. Se não corresponder, utilize `IsDefault=true` e,
finalmente, o fornecedor global padrão.

```text
talkWithAgent
      -> SysResourceIA.IDResource
      -> clasificación de la pregunta
      -> SysAgentIAModel.Capabilities
      -> IDProviderConfiguration seleccionado
      -> SysLLMProviderConfiguration
      -> modelo de chat
      -> respuesta con identidad del recurso agente
```
Atribua ou consulte um modelo de agente:

```http
PUT /api/v1/agent/solidset/agents/{IDResource}/model
GET /api/v1/agent/solidset/agents/{IDResource}/model
```

```json
{
  "ProviderCode": "ollama-default",
  "Role": "general",
  "LocalExecution": true,
  "TrainingMode": "rag_reinforcement",
  "LearnFromOwner": true,
  "LearnFromSystem": true,
  "LearnFromReactions": true,
  "Capabilities": ["coding", "sql", "technical"],
  "Priority": 20,
  "IsDefault": false,
  "active": true
}
```
Capacidades iniciais:

- `general` e `external_web` → `qwen2.5:3b` .
- `coding`, `sql` e `technical` → `qwen2.5-coder:3b`.
- `reasoning`, `planning` e `analysis` → `llama3.2:3b`.

A identidade, sessão, memória, conhecimento privado e login do SolidSET permanecem
associado ao mesmo `IDResource`; apenas o modelo que gera essa curva se altera.

### Inverso do destinatário ao responder

Quando `Chat.destiny` contém o recurso humano `type=1` e o agente solicitado
 `type=2, talkWithAgent=true` , o primeiro seleciona o destinatário da resposta
e a segunda seleciona o agente emissor. O formulário enviado para o SolidSET é
logicamente invertido como agente → humano:

```text
Destiny.WorkRoom = Chat.destiny[].idChannel
Destiny.Dests[0].Login = Chat.destiny[type=1].idLogin
Destiny.Dests[0].Resource = Chat.destiny[type=1].idResource
Destiny.Dests[0].Kind = 2
Destiny.Dests[0].Type = 2
```
A API utiliza as chaves simples `Destiny.Dests[0].*` porque `/Chat/SendMessageForm`
receber formulário; O model binder do SolidSET converte-o no objeto aninhado
 `Destiny.Dests`.  `talkWithAgent` não é encaminhado na resposta para evitar
a resposta gerada reativa o agente.

As consultas de contagem direta no SQL Server só são ativadas quando o
questão contém explicitamente `recurso(s)` , `usuario(s)` ou os seus equivalentes
Português/Inglês. Um quantificador isolado, por exemplo “quantos Campeões”, não
ativa o SQL e continua através do fornecedor de conhecimento externo correspondente.

Além disso, é enviado o bloco `Chat` que o cliente SolidSET utiliza para pintar.
 `From` e `To`. Numa conversa com o próprio agente é:

```text
Chat.IDSenderResource = SysResourceIA.IDAgentResource
Chat.IDSender = login del propietario del agente, si existe; si no, se omite
Chat.IDWorkRoom = IDWorkRoom
Chat.IDMeeting = meeting válido (si existe)
Chat.RawMessage = respuesta
Chat.Kind = 60
Chat.Destiny[0] = agente, Type=1, TalkWithAgent=true
Chat.Destiny[1] = recurso humano, Type=2
```
O envelope de resposta indica ainda `Sender.Resource=IDAgentResource`,
 `Sender.Login=login del propietario` quando existe e mantém
 `Sender.Session` / `Sender.WorkRoom` em zero GUID. O canal de entrega é indicado
em `Destiny.WorkRoom`; O seu único destino é o recurso humano com `Kind=2`.

Assim a UI recebe `From: agente [IA] To: humano` , em vez de reutilizar o
endereço da mensagem original `From: humano To: agente [IA]`.

 O `SysResourceIA.IDResource` identifica o proprietário dos recursos humanos e é utilizado
para selecionar o agente, resolva o seu login, memória e conhecimento.
 O `SysResourceIA.IDAgentResource` identifica o recurso de software que representa
para o remetente técnico do agente no SolidSET e provém de
 `dbo.SysResource2Agent.IDAgentResource`; é utilizado em `IDAgentIA` e como identidade
técnica. Também identifica sempre o participante De por
 `Chat.Destiny[0].IDResource`, mesmo quando o proprietário está a falar com o seu
própria IA.  `Chat.Destiny[1]` contém exclusivamente o recurso humano
destinatário e não possui `TalkWithAgent`.  `Chat.IDSenderResource` contém o
mesmo `IDAgentResource` verificado que identifica o De. `Chat.IDSender`
contém exclusivamente o login do proprietário quando disponível;
Nunca contém um GUID de recurso e é ignorado se o agente não tiver um login.
nunca usado
o UUID interno `SysResourceIA.ID` como
Participante SolidSET. Se um agente ativo ainda não tiver
 `IDAgentResource` , a resposta é omitida para não a publicar com identidade
técnica incorreta.

 O `TrainingMode` suporta `rag_reinforcement`, `rag_only` e `disabled`. A melhoria
atual não modifica os pesos do modelo: utiliza conhecimento vetorial isolado,
mensagens do proprietário do recurso, conhecimentos gerais permitidos, memória de
conversa e recompensas derivadas das reações. Esta estratégia pode funcionar
continuamente sem parar Ollama. Um futuro ajuste fino de pesos deve ser executado
como um processo separado, versione o modelo resultante e registe-o como um
novas definições antes de ativá-lo.

 `LocalExecution=true` é válido apenas para `ollama`, `local_openai` ou um endpoint
 `openai_compatible` implementado em infraestrutura própria. Os modelos oficiais
da OpenAI, Azure OpenAI, Anthropic e Gemini são remotas; guarde as suas configurações
O PostgreSQL não converte estes modelos proprietários em modelos locais.

Para adicionar outro motor basta implementar o `ChatProvider.create_model()` e
registe-o utilizando o `ProviderRegistry.register()`. O router, LangGraph,
As ferramentas, a memória e os endpoints do SolidSET permanecem inalterados.

A seleção local atual foi calculada com o `llmfit` e está documentada em
 `docs/llmfit-model-selection.md`: `qwen2.5:3b` para coordenação/chat,
 `qwen2.5-coder:3b` para código e SQL, `Phi-4-mini-reasoning` como raciocinador
sequencial opcional e `nomic-embed-text` para manter a coleção atual.
 O `Qwen3-Embedding-0.6B` está reservado para migração com reindexação.

## 0. Registe uma instância SolidSET

```http
POST /api/v1/agent/solidset/instances
```
Registe ou atualize pela `Code` os URLs e a API SolidSET Data de um
instalação. O agente não recebe as credenciais do SQL Server:

```json
{
  "Code": "solidset-lisboa",
  "Name": "SolidSET Lisboa",
  "BaseUrl": "http://192.168.10.20:52130",
  "NotificationUrl": "http://192.168.10.20:52131",
  "SourceIP": "192.168.10.20",
  "CountryCode": "PT",
  "Locale": "pt-PT",
  "TimeZone": "Europe/Lisbon",
  "DataAPI": {
    "BaseUrl": "https://192.168.10.20:8081",
    "APIKey": "<secret>",
    "TimeoutSeconds": 120,
    "MaxRows": 5000,
    "VerifyTLS": true,
    "active": true
  },
  "active": true
}
```
A configuração geral é guardada em `SysSolidSETInstance` e o gateway em
 `SysSolidSETDataAPI`. A chave API é encriptada com Fernet antes de ser persistida e nunca
é devolvido: A resposta contém apenas `APIKeyConfigured=true` . Para atualizar
uma instância sem a alterar `DataAPI.APIKey` é ignorada.

O campo `Database` já não faz parte do contrato deste endpoint. Se
envia, o FastAPI responde `422` porque as credenciais e parâmetros do SQL Server
Pertencem exclusivamente à implantação independente `solidset-data-api/.env`.
A tabela legada do PostgreSQL `SysSolidSETDatabase` foi preservada temporariamente
para uma migração segura, mas este endpoint já não o lê nem atualiza.
Se `LLM_CREDENTIAL_ENCRYPTION_KEY` não for fornecido, a API gera uma chave
Fernet apenas uma vez em `/app/data/credential.key`. O diretório `data` já está
persistentemente montado em desenvolvimento, API, produtor e trabalhador. Também é
pode fornecer a chave como segredo de implantação; Não é a chave API ou uma
Credencial SQL Server. O ficheiro deve ser incluído nas cópias de segurança: sim
for perdido, as credenciais guardadas não poderão ser recuperadas.
Se a variável ou ficheiro contiver uma chave que não seja um Fernet válido, o
variável é ignorada e o ficheiro é mantido como
 `credential.key.invalid-<timestamp>` antes de gerar uma chave correta.

 O `DataAPI.BaseUrl` deve ser acessível a partir dos contentores do agente. Se ele
o gateway de teste está na mesma composição em que é utilizado
 `http://solidset-data-api:8080`; se estiver no servidor SolidSET é utilizado
DNS ou IP HTTPS. Para facilitar o desenvolvimento, foi criado um URL registado em
 `localhost` , `127.0.0.1` ou `::1` traduz em tempo de execução para
 `host.docker.internal` quando o agente está dentro do Docker. Fora do Docker
o URL é preservado inalterado.

Se o SQL Server estiver noutro Compose, a API Data também pode ser iniciada com
 `solidset-data-api/docker-compose.sql-container.yml`. A sobreposição incorpora o
rede externa configurada para `SQL_SERVER_DOCKER_NETWORK` e permite utilizar o nome
do contentor SQL como `SQL_SERVER_HOST` , sem depender de uma porta host.

Antes de inserir, a API corresponde a `Code` ; Se encontrar um,
atualiza essa linha e preserva o seu `ID` .  O `BaseUrl` é utilizado para login e
respostas;  `NotificationUrl`, para notificações.  `SourceIP` é um endereço
de destino. O registo ainda usa `Code` como identificador estável; o
Os pedidos recebidos utilizam o host explícito de `X-SolidSET-Instance` para pesquisar
uma única linha activa com aquele `SourceIP` . No arranque, o serviço remove o
índice exclusivo herdado de `SourceIP` ; se várias linhas ativas partilharem o mesmo
destino, o pedido é rejeitado por ambiguidade.

Após o registo, a ligação é verificada utilizando:

```http
POST /api/v1/agent/solidset/instances/solidset-lisboa/test-connection
```
O teste atravessa a API Data e devolve o catálogo real, uma versão
abreviatura do servidor, do adaptador e se existir `dbo.SysResource2Agent` ; nunca
inclui nome de utilizador, palavra-passe, chave API encriptada ou string completa. Os vestígios de
servidor apenas mostra `instance` , `DataAPI.BaseUrl` , o tipo de erro e
um caso técnico abreviado.

O projeto independente está na `solidset-data-api/` e afirma:

```http
GET  /health
GET  /api/v1/system/capabilities
GET  /api/v1/datasets/{dataset}
GET  /api/v1/agents/{humanResourceId}
POST /api/v1/query/read
```
Os terminais protegidos requerem `X-SolidSET-Data-Key` .  O `query/read` suporta
apenas `SELECT` ou CTE parametrizado, rejeita escrita, procedimentos,
comentários e múltiplas instruções e limita o número de linhas. A conta
O SQL configurado no gateway também deve ter apenas permissões
lendo. Esta primeira versão preserva as consultas existentes enquanto o seu
execução e as credenciais são deixadas fora do agente.

O adaptador de compatibilidade remove os comentários SQL herdados antes
enviar uma consulta de leitura para o gateway. Os valores temporários devolvidos por
JSON são normalizados a partir da ISO 8601 antes de se construir o contexto do agente.

Os conjuntos de dados `resources` , `logins` , `workrooms` e `workroom-resources`, em conjunto
com a validação `agents/{humanResourceId}`, mantêm as suas consultas dentro do
projeto independente. Consultas históricas e de aprendizagem cujo SQL é
adapta-se dinamicamente ao esquema utilize `query/read` , mas também execute
exclusivamente dentro do gateway.

 O `GET /api/v1/datasets/{dataset}` suporta `offset` e `limit` e também retorna
 `hasMore` e `nextOffset`. O conector do agente percorre automaticamente todos os
páginas, pelo que uma instalação com mais linhas que o `MaxRows` não é
parcialmente sincronizado.

Ao responder dentro de uma reunião, o agente valida o `meeting_id` utilizando
o `BaseUrl` da instância selecionada. Perguntas sobre recursos ou
Os participantes da reunião não utilizam o contador de recursos global:
resolver contra `dbo.SysMeeting2Resource` utilizando o `meeting_id` do
conversação e eliminar recursos pendentes, bloqueados ou expulsos. A URL
lógica `localhost` e a sua tradução Docker `host.docker.internal` identificam o
mesma instância ao selecionar o `SysLogin` do agente.

Para executar o gateway de teste na mesma máquina:

```powershell
docker compose -f docker-compose-dev.yml --profile data-api up -d --build solidset-data-api
```
Para o implementar completamente separadamente no servidor onde está o SQL Server,
O Compose incluído no projeto independente é utilizado:

```powershell
Set-Location solidset-data-api
Copy-Item .env.example .env
# Configurar SQL_SERVER_* y SOLIDSET_DATA_API_KEY en .env.
docker compose up -d --build
```
Este Compose cria apenas `solidset_data_api` , a sua rede privada e o
exame de saúde; não requer nenhum contentor de agente.

 `CountryCode`, `Locale` e `TimeZone` definem o contexto regional do
respostas.  `TimeZone` deve ser uma zona IANA válida, como por exemplo `Europe/Lisbon`, e
 O `Locale` utiliza o formato BCP 47, como o `pt-PT`. As instalações existentes são
migram automaticamente com `PT`, `pt-PT` e `Europe/Lisbon`. A localização não está geolocalizada
O IP privado nem o país são deduzidos da língua: estes métodos não são fiáveis
atrás do NAT, VPN ou Docker.

O agente recebe estes valores em todas as respostas dessa instância. Para
 `pt-PT` utiliza vocabulário e ortografia em português. As consultas explícitas de
data ou hora (`Que dia é hoje?`, `Que horas são?`) são calculadas diretamente com
 `TimeZone`, sem pedir ao LLM para adivinhar a localização; não pode, portanto, responder
com a hora de Brasília quando a instância estiver configurada em Portugal.

A linguagem da pergunta tem sempre precedência sobre `Locale`: uma pergunta
em inglês recebe uma resposta em inglês mesmo que a instância utilize `pt-PT` ; um
pergunta em espanhol recebe espanhol.  `Locale` determina apenas a variante regional
quando a língua corresponde e nunca obriga à tradução da resposta para português.

Se o cliente souber a localização real do recurso – por exemplo, porque o
o utilizador está temporariamente noutro país - pode incluir `Info`, `TimeData` ou
 `UserData` os campos `country_code` , `locale` e `time_zone`. Esses valores
As especificidades da mensagem têm prioridade sobre a instância; uma zona IANA não
válido é descartado. Quando a carga útil não os inclui, a configuração do
 `SysSolidSETInstance`. O IP observado pela API corresponde normalmente ao
Servidor ou proxy SolidSET, e não a máquina WPF, pelo que não é utilizado para localizar
ao recurso.

Cada SolidSET deve chamar os endpoints de entrada com o host de resposta:

```http
X-SolidSET-Instance: 192.168.10.20
```
O valor deve corresponder a `SysSolidSETInstance.SourceIP` de uma única instância
ativo, com equivalência entre `localhost`, `127.0.0.1` e `::1`. Não aceito
URL, caminho ou credenciais no cabeçalho. Depois de encontrar a linha, a API lê
seus `BaseUrl`, `NotificationUrl` e `SysSolidSETDataAPI`; endereços de acesso
e a resposta sai dessa configuração, não do cabeçalho. A instância
resolvido é mantido na pegada do evento, sessão do agente, login e envio
de resposta. Os erros de acesso do PostgreSQL continuam a retornar `503` .

## 1. Guardar ou atualizar um agente

```http
POST /api/v1/agent/solidset/chat-configuration
```
Crie ou atualize um agente utilizando `IDResource` como identificador único.

```json
{
  "Name": "Agente de mantenimiento",
  "Stamp": "2026-08-17T16:30:00",
  "IDResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
  "active": true
}
```
Comportamento:

- Caso o recurso não exista, crie o `SysResourceIA` .
- Se já existir, atualize o nome, data e estado.
- `active = false` impede o agente de responder.
- O campo interno `ID` é gerado automaticamente pelo PostgreSQL.

---

## 2. Configure um agente dentro de um canal

```http
PUT /api/v1/agent/solidset/agents/{agent_resource_id}/workrooms/{workroom_id}
```
Ative, desative ou comande o agente dentro de um canal específico.

```json
{
  "active": true,
  "response_order": 1
}
```
Exemplo:

```http
PUT /api/v1/agent/solidset/agents/ce0e837a-fe28-47ae-9ba0-8841fe042ca8/workrooms/007e3b2a-bbf6-4f46-8cbd-26d26db06ec1
```
Comportamento:

- Transforma `UPSERT` em `SysChatIAResource`.
- `active` controla se pode responder nesse canal.
- `response_order` determina a sua posição em relação aos outros agentes.
- Um agente pode estar ativo globalmente e desativado apenas num canal.

---

## 3. Adicione conhecimento próprio a um agente

```http
POST /api/v1/agent/solidset/agents/{agent_resource_id}/knowledge
```
Armazena conhecimento privado no PostgreSQL e indexa-o no Qdrant.

Conhecimento geral do agente:

```json
{
  "Title": "Especialización",
  "KnowledgeText": "Este agente está especializado en mantenimiento preventivo de tornos CNC.",
  "Source": "solidset",
  "active": true
}
```
Conhecimento específico do canal:

```json
{
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "Title": "Máquinas del canal",
  "KnowledgeText": "Este canal gestiona los tornos de la línea cuatro.",
  "Source": "solidset",
  "active": true
}
```
Comportamento:

- Guarde o conteúdo em `SysResourceIAKnowledge`.
- Caso não contenha `IDWorkRoom`, o agente poderá utilizá-lo em todos os seus canais.
- Se contiver `IDWorkRoom`, só será utilizado dentro desse canal.
- Outro agente não consegue recuperar este conhecimento.
- `active = false` mantém o conteúdo, mas deixa de o utilizar.

---

## 4. Execute vários agentes

```http
POST /api/v1/agent/solidset/multi-agent/dialogue
```
É o principal ponto final da arquitetura multiagente.

```json
{
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "IDSession": "06e64429-fb46-4544-a0be-c6bbde4acd66",
  "RawMessage": "¿Cuál puede ser la causa de esta alarma?",
  "SenderResourceId": "ba55b081-3e30-4f38-9816-194720c6701f",
  "SelectedAgentResourceIds": [
    "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
    "272700d8-d1ba-46a6-a121-b76fce8ecb9f"
  ],
  "SendToSolidSET": true,
  "SolidSETInstanceCode": "solidset-lisboa"
}
```
`SolidSETInstanceCode` é necessário quando `SendToSolidSET=true` . Se apenas pretende gerar a resposta sem a publicar, pode manter `SendToSolidSET=false` e ignorar a instância.

Comportamento:

1.º Verifique se cada agente existe.
2.º Verifique se está ativo globalmente.
3.º Verifique se está ativo e atribuído ao canal.
4.º Crie ou atualize uma linha em `SysAgentIASession`.
5.º Execute todos os agentes em paralelo.
6.º Cada um utiliza a sua própria memória e conhecimento.
7.º Retorna uma resposta separada por agente.
8.Com o `SendToSolidSET = true`, publique as respostas no canal.

O mesmo `IDResource` pode aparecer como remetente e agente configurados. Isto é válido quando uma pessoa intervém utilizando o recurso que o SolidSET configurou como agente; o bloqueio do loop é feito pelo `Info.generated_by_ia` , não eliminando o recurso do remetente.

Responder:

```json
{
  "IDSession": "06e64429-fb46-4544-a0be-c6bbde4acd66",
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "responses": [
    {
      "IDAgentResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
      "AgentName": "Agente de mantenimiento",
      "response": "La alarma puede estar relacionada con...",
      "sent": false,
      "sendDetail": null
    }
  ]
}
```
`IDSession` deve ser UUID. Se não for enviado, a API gera um.

# Sincronização com SQL Server

## 5. Sincronizar recursos

```http
POST /api/v1/agent/solidset/resources/sync
```
Execute a consulta para `SysResources` e `SysLogin` e obtenha a identidade do
recurso de software utilizando o relacionamento ativo de `SysResource2Agent`.

Mapeamento:

```text
SysResources.DisplayName → SysResourceIA.Name
SysResources.ResourceId  → SysResourceIA.IDResource
SysResources.ActiveIDLogin2Resource → SysResourceIA.ActiveIDLogin2Resource
SysResource2Agent.IDHumanResource → SysResourceIA.IDResource
SysResource2Agent.IDAgentResource → SysResourceIA.IDAgentResource
```
O `SysResourceIA.ID` continua a ser a chave interna gerada automaticamente a partir do PostgreSQL.
Não é devolvida como a identidade do agente SolidSET. Sempre que um contrato
resposta expõe `IDAgentResource` , devolve o GUID sincronizado de
 `dbo.SysResource2Agent.IDAgentResource`;  O `IDResource` mantém o GUID do
recurso humano proprietário.

A sincronização é idempotente:

- Inserir novos recursos.
- Atualizar os recursos existentes.
- Não cria duplicados.
- Preserva o estado `active`.

Resposta aproximada:

```json
{
  "status": "synchronized",
  "sourceRows": 126,
  "synchronized": 126,
  "inserted": 0,
  "updated": 126,
  "skipped": 0
}
```
---

## 6. Sincronize recursos e canais

```http
POST /api/v1/agent/solidset/chat-workroom/sync
```
Sincronize as atribuições de recursos a canais de:

```text
SysResources
SysLogin
SysWorkRoomResource
SysWorkRoom
```
Mapeamento:

```text
ResourceId → SysChatIAResource.IDResource
IDWorkRoom → SysChatIAResource.IDWorkRoom
```
Comportamento:

- Criar relações entre canais de recursos.
- Evite duplicados por `(IDResource, IDWorkRoom)`.
- Não modifica as sessões.
- Os novos relacionamentos ficam ativos por defeito.

---

## 7. Sincronize o catálogo de canais

```http
POST /api/v1/agent/solidset/workrooms/sync
```
Execute esta consulta no SQL Server:

```sql
SELECT Code, Name, Description, IDWorkRoom
FROM dbo.SysWorkRoom;
```
Mapeamento para o PostgreSQL:

```text
Code        → SysWorkRoom.Code
Name        → SysWorkRoom.Name
Description → SysWorkRoom.Description
IDWorkRoom  → SysWorkRoom.IDWorkRoom
```
A sincronização faz `UPSERT` para `IDWorkRoom`, remove espaços adicionados pelo SQL Server tipo `NCHAR` e não cria duplicados.

 `SysWorkRoom.IDWorkRoom` é a chave pai de:

-`SysChatIAResource.IDWorkRoom` .
-`SysResourceIAKnowledge.IDWorkRoom` .
-`SysAgentIASession.IDWorkRoom` .

Resposta aproximada:

```json
{
  "status": "synchronized",
  "sourceRows": 29976,
  "synchronized": 29976,
  "inserted": 0,
  "updated": 29976,
  "skipped": 0
}
```
---

## 8. Sincronizar contas de acesso

```http
POST /api/v1/agent/solidset/logins/sync
```
Execute no SQL Server:

```sql
SELECT Username, FullName, Password, Salt, IDLogin,
       LastIDResource, ActiveIDLogin2Resource
FROM dbo.SysLogin;
```
Guarda os dados no PostgreSQL `SysLogin` utilizando um `UPSERT` de `IDLogin` . A contagem exacta dos agentes é resolvida juntando `SysResourceIA.ActiveIDLogin2Resource` com `SysLogin.ActiveIDLogin2Resource` ; isto evita a escolha de outro utilizador que tenha o mesmo `LastIDResource` .

Mapeamento adicional:

```text
dbo.SysLogin.FullName → PostgreSQL SysLogin.FullName
```
Ao enviar uma resposta automática ou multiagente, o router entrega o
 `agent_resource_id` e `IDLogin` selecionados em `Chat.destiny` para o método
 `_solidset_login`. Este método requer que `SysResourceIA.active=true` procure o
contas relacionadas por `ActiveIDLogin2Resource` e priorizar exatamente essa
 `IDLogin`; Desta forma evita escolher uma linha histórica através de uma ordem arbitrária.
De seguida, inicie uma sessão separada com `POST /User/LoginJson` e poste o
mensagem com os cookies dessa mesma sessão. Se o SolidSET retornar HTTP 200 com
 `Success=false` , a API ressincroniza o `dbo.SysLogin` com o PostgreSQL e tenta novamente
uma vez, cobrindo as alterações recentes de ID ou palavra-passe sem criar loops.

A autenticação do agente envia internamente:

```text
UserName          = SysLogin.Username
Password          = SysLogin.Password
PasswordEncrypted = true
TimezoneID        = SOLIDSET_TIMEZONE_ID
Resources[0]      = SysResourceIA.IDResource
```
`SysLogin.Password` é o HMAC já gerado pelo SolidSET, e não uma password reversível.  `PasswordEncrypted=true` faz com que o método SolidSET ignore `GenerateHMAC` e compare directamente esse valor.  `Resources[0]` força o registo da sessão no recurso do agente solicitado quando o login tem vários recursos. Caso o recurso não seja um agente ativo, não possua uma conta válida ou `LoginJson` recuse o acesso, o envio falha explicitamente e não utiliza a identidade global configurada em `.env` .

A resposta publicada utiliza `SysLogin.FullName` e mantém um ID visível no formato `{FullName}: respuesta`; por exemplo, `Alejandro Veitia: ...` . O SolidSET mostra também o login do próprio recurso como emissor. Se excepcionalmente `FullName` estiver vazio, `SysResourceIA.Name` será utilizado como backup.

A resposta contém apenas contadores; nunca devolve ou regista `Password` ou `Salt`:

```json
{
  "status": "synchronized",
  "sourceRows": 325,
  "synchronized": 325,
  "inserted": 325,
  "updated": 0,
  "skipped": 0
}
```
Os campos `Password` e `Salt` são dados sensíveis. O acesso ao esquema PostgreSQL deve ser limitado ao serviço do agente e não deve ser incluído em endpoints de consulta, registos ou mensagens de erro.

# Entrada de mensagens do SolidSET

## 9. Receber uma notificação FrameworkMessage

```http
POST /api/v1/agent/notification/framework-message
```
Recebe diretamente um `FrameworkMessage` do SolidSET.

Swagger inclui o exemplo fictício `meetingAgentQuestion`, também reutilizado
por `/framework-message/preview` e `/agent/dialogue`. Representa uma questão
um recurso humano para o seu recurso de IA numa reunião, com `talkWithAgent=true`,
Identificadores consistentes de canal/reunião e dados regionais. Os valores não são
Pertencem a usuários ou instalações reais.

No modo `AGENT_RESPONSE_QUEUE_ENABLED=true` standard, este terminal
basta validar a instância, obter `Chat.IDChat2` , criar o estado e publicar o
mensagem original no Redis Stream. Retorna imediatamente; Captura Qdrant,
seleção de agentes, LLM e envio para SolidSET executados em `agent-worker`.
O commit HTTP é `202 Accepted` .
A resolução `SysSolidSETInstance` é retida durante 60 segundos na memória para
evitar uma consulta PostgreSQL por pedido durante picos de carga; salvar
uma configuração de instância invalida imediatamente essa cache.

A resposta retém `Result` , `Message` e `Error` e adiciona os dados a
siga o trabalho assíncrono:

```json
{
  "Result": 0,
  "requestId": "1824911",
  "status": "queued",
  "statusUrl": "/api/v1/agent/responses/1824911/status"
}
```
`requestId` corresponde directamente a `Chat.IDChat2` , convertido em texto. De
Desta forma, o WPF pode relacionar o carregamento e os estados com a mensagem que já está
saber Apenas as notificações técnicas sem `IDChat2` recebem um UUID temporário
contingência.

O cliente WPF deve persistir `requestId` , apresentar um sinalizador indeterminado e
consulte `statusUrl` a cada 1–2 segundos até `completed=true` .

### Sugerir uma resposta a `Chat.chatQuestion`

```http
POST /api/v1/agent/notification/chat-question/suggest-response
Content-Type: application/json
```
Recebe o mesmo `FrameworkMessage` mas não captura a mensagem como uma nova
pedido de resposta automática nem envia nada para o SolidSET. O ponto final leva:

- `Chat.IDChat2` como `requestId` para rastreio de estado.
- `Chat.IDSenderResource` como recurso humano requerente da sugestão.
- `Chat.chatQuestion.IDSenderResource` como autor da mensagem anterior.
- `Chat.chatQuestion.IDChat2` e `RawMessage` como mensagem a responder.
- `Chat.IDWorkRoom` , `Chat.IDMeeting` e `Info.meeting_code` como contexto.

Também suporta o modo de sugestão contextual quando `Chat=null` e
 `RawMessage` está vazio. Nesse caso, deverão ser enviados:

- `Info.advice_mode="1"` para ativar explicitamente o modo.
- `Info.request_id` como identificador de estado e resultado.
- `Info.session_id` como sessão lógica quando `Sender.session` é o GUID vazio.
- `Sender.resource` como recurso humano candidato.
- `Sender.workRoom` ou `Destiny.workRoom` como canal a verificar.

Neste modo a API consulta, através da API SolidSET Data, até 30 mensagens
canais acessíveis recentes do canal e constrói uma janela cronológica limitada a
3.200 caracteres, dando prioridade às mensagens mais recentes. Este contexto inclui também a conversa de
uma reunião quando as suas mensagens estão associadas ao mesmo `IDWorkRoom` . Os três
As sugestões baseiam-se exclusivamente nestas mensagens, em conhecimento privado
do agente e no seu contexto reforçador. Se o canal não contiver mensagens
acessível, a API não inventa conteúdo e devolve um erro tratado.
Os avisos técnicos do modelo (por exemplo, “consulta demasiado longa”) são apresentados
São descartados e nunca mais devolvidos como se fossem uma sugestão de conversa.

Antes de gerar, verifique no SQL Server se o requerente tem uma relação
ativa em `dbo.SysResource2Agent`, sincroniza `IDAgentResource` e resolve o seu
agente ativo no PostgreSQL. A geração utiliza conhecimento privado e
reforçando o contexto do próprio agente do requerente. Não utiliza o agente
autor citado, não consulta a Internet e trata o texto citado como não-dado
fiável.

A operação de dica só é válida quando `Chat.RawMessage` está vazio.
O texto a responder provém de `Chat.chatQuestion.RawMessage`; se ele
a mensagem atual já contém texto, o endpoint retorna HTTP 422 para evitar
uma resposta escrita pelo utilizador é substituída.

Swagger inclui os exemplos `quotedMeetingMessage` e `emptyContextAdvice` com
dados completamente fictícios. A primeira preserva a relação entre a `Chat.IDChat2`,
 `chatQuestionMessage`, `Chat.chatQuestion`, o requerente, o autor citado, o
canal e a reunião. O segundo documenta um pedido sem `Chat` ou texto que
Solicite sugestões do canal. Sem GUID, nome ou número de chat
Os exemplos pertencem a uma instalação real do SolidSET.

Se for bem-sucedido, irá devolver HTTP 200 com uma lista JSON de alternativas
independente. O modelo tenta produzir três variantes – direta, breve e
colaborativo - na mesma língua da mensagem citada:

```json
{
  "requestId": "1824995",
  "questionChatId": "1824994",
  "status": "completed",
  "code": 5,
  "language": "pt",
  "suggestions": [
    {"id": "1", "text": "Obrigado pela informação. Vou confirmar esse ponto."},
    {"id": "2", "text": "Entendido, obrigado."},
    {"id": "3", "text": "Obrigado. Pretende que validemos este ponto em conjunto?"}
  ],
  "statusUrl": "/api/v1/agent/responses/1824995/status"
}
```
Cada `text` é elegível para ser atribuído a `RawMessage`; não contém nome de
agente, prefixo ou carga útil de remessa. A seleção pertence exclusivamente ao
cliente e este endpoint nunca publica qualquer alternativa ao SolidSET. Para
mostrar o progresso enquanto a chamada estiver aberta, o WPF poderá consultar em paralelo:

```http
GET /api/v1/agent/responses/{Chat.IDChat2}/status?lang=es
```
A sequência normal é `queued` → `processing` → `searching` → `thinking` →
 `completed`. `sending` não aparece porque este endpoint nunca publica o texto
em SolidSET. Quando concluído, o estado inclui também `result.questionChatId`,
 `result.language` e `result.suggestions`, para que o cliente possa recuperar
as alternativas mesmo que o pedido POST esteja fechado. Os erros de validação retornam HTTP 422; a ausência de um
o autoagente ativo retorna HTTP 404; dependências ou geração
disponível retorna HTTP 503 e deixa o estado em `failed` .

### Verificar o estado de uma resposta

```http
GET /api/v1/agent/responses/{requestId}/status?lang=es
```
Como recuperação alternativa utilizando a mensagem original:

```http
GET /api/v1/agent/responses/status?chatId={IDChat2}&lang=es
```
Os Estados são guardados temporariamente no Redis para
 `AGENT_RESPONSE_STATUS_TTL_SECONDS` (padrão 86400 segundos):

 `lang` suporta `es`, `en` e `pt`; o valor predefinido é `es` . cada resposta
inclui também o `displayMessages` com as três traduções para que o WPF possa
alterar o idioma sem consultar novamente a API.

| Código | Estado | Espanhol | Inglês | Português |
|---:|---|---|---|---|
|  `0` |  `queued` |  `Esperando…` |  `Waiting…` |  `Aguardando…` |
|  `1` |  `processing` |  `Procesando…` |  `Processing…` |  `Processando…` |
|  `2` |  `searching` |  `Buscando información…` |  `Searching for information…` |  `Procurando informações…` |
|  `3` |  `thinking` |  `Pensando…` |  `Thinking…` |  `Pensando…` |
|  `4` |  `sending` |  `Enviando respuesta…` |  `Sending response…` |  `Enviando resposta…` |
|  `5` |  `completed` |  `Respondido` |  `Answered` |  `Respondido` |
|  `6` |  `failed` |  `No se pudo responder` |  `Unable to respond` |  `Não foi possível responder` |
|  `7` |  `cancelled` |  `Cancelado` |  `Cancelled` |  `Cancelado` |

A resposta de estado inclui `agents` para mostrar cada agente em separado,
 `stageHistory`, `responseCount`, `createdAt`, `updatedAt`, `completedAt` e
 `error`. A votação deve terminar quando receber `completed`, `failed` ou
 `cancelled` (todos regressam `completed=true` ). Um HTTP 404 significa que o
 `requestId` não existe ou já expirou.

### Fila durável e escalonamento de trabalho

A fila utiliza Redis Streams com grupo de consumidores. Uma mensagem só é confirmada com
 `XACK` após acabamento; as mensagens abandonadas por um trabalhador são recuperadas
com `XAUTOCLAIM`. As falhas são repostas na fila até
 `AGENT_RESPONSE_MAX_RETRIES`; depois permanecem no estado `failed` e PostgreSQL
preserva o erro.

```env
AGENT_RESPONSE_QUEUE_ENABLED=true
AGENT_RESPONSE_STREAM=machining:agent-responses:v1
AGENT_RESPONSE_CONSUMER_GROUP=agent-response-workers-v1
AGENT_RESPONSE_STREAM_MAXLEN=100000
AGENT_RESPONSE_MAX_RETRIES=3
AGENT_RESPONSE_CLAIM_IDLE_MS=300000
AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS=15
AGENT_RESPONSE_STATUS_TTL_SECONDS=86400
```
`XREADGROUP` espera até 5 segundos. Um `redis.exceptions.TimeoutError` durante
Esta espera é interpretada como uma fila vazia e o trabalhador continua. Outros erros
Os casos temporários do Redis provocam uma reconexão automática a cada 2 segundos; não
encerram o processo de trabalho.

A tabela PostgreSQL `SysAgentIAResponseAudit` preserva `RequestID` , `IDChat2` ,
payload original, estado, código, número de respostas, resultado resumido,
erro e carimbos de data/hora.

Para aumentar a capacidade sem modificar a API:

```powershell
docker compose -f docker-compose-prod.yml up -d --scale agent-worker=4
```
O número efetivo de trabalhadores deve respeitar a capacidade do Ollama/GPU. Redis
pode aceitar uma fila muito maior que a simultaneidade do modelo, mas aumenta
trabalhadores acima de `OLLAMA_NUM_PARALLEL` só aumentam a espera em Ollama.
O Nginx limita por IP a 100 pedidos/s (burst 200) e por
 `X-SolidSET-Instance` a 200 pedidos/s (burst 500), devolvendo HTTP 429 em
ultrapassar esses limites.

As métricas de fila de espera estão disponíveis em:

```http
GET /api/v1/agent/responses/queue/status
```
Devolve `length` , `pending` , `consumers` e `lag` , necessário para decidir se pretende
os trabalhadores devem aumentar.

### Visualize a resposta sem a enviar

```http
POST /api/v1/agent/notification/framework-message/preview
```
Recebe o mesmo `FrameworkMessage` , resolve a instância e os agentes
selecionado, gera as suas respostas e constrói exatamente a carga útil que é
enviaria para o SolidSET, mas este não faz login nem chama `Chat/SendMessageForm` .
A resposta contém `Payloads` , uma lista porque uma mensagem pode seleccionar
vários agentes. Cada elemento é devolvido como JSON aninhado com `Sender` ,
 `Destiny`, `ExtraData`, `Info` e `Chat`.  `PayloadCount=0` indica que não
agente ativo e verificado teve de responder.

Recursos:

- Normalizar a mensagem.
- Captura para aprender.
- Indexa no Qdrant.
- Identifica os agentes selecionados.
- Agende respostas automáticas, se aplicável.
- Descartar as mensagens marcadas como `generated_by_ia`.

Este endpoint não funciona como proxy: recebe e processa a mensagem.

Cada mensagem humana é primeiro indexada como aprendizagem do sistema global. Se `IDSenderResource` corresponder a um `SysResourceIA.IDResource` ativo, uma cópia privada com `scope=agent_owner_behavior` e `agent_resource_id` do proprietário também será indexada. Assim, cada agente aprende o conhecimento, o vocabulário e os padrões expressos pelo seu próprio recurso humano, enquanto todos os agentes continuam a aprender a partir do contexto geral permitido. A cópia privada é eliminada de outros agentes utilizando o filtro `agent_resource_id`; As respostas geradas pela IA não entram novamente neste ciclo.

Para identificar os agentes candidatos, o router suporta estas fontes de carga:

```text
Chat.destiny[].talkWithAgent=true + type=2 (prioridad absoluta)
Destiny.dests[].resource
SelectedAgentResourceIds[] (solo cuando Destiny.dests está vacío)
Destiny.resource (solo cuando Destiny.dests está vacío)
```
O novo sinal canónico é `Chat.destiny[].talkWithAgent`. Se o campo aparecer em qualquer uma das entradas, essa coleção terá precedência absoluta. Apenas é selecionada uma entrada com `talkWithAgent=true` , `type=2` e um `idResource` válido. Adicionalmente, o agente responde quando `Chat.questionType=2` (pergunta), `Chat.questionType=3` (solicitação), ou quando o texto contém o sinal `?` mesmo sendo `questionType=1` . Nenhuma outra heurística linguística é aplicada. Um alvo `type=3` está sempre a aprender conteúdo e nunca gera uma resposta, mesmo que contenha `talkWithAgent=true` , `questionType=2/3` ou um sinal `?`. As restantes combinações permanecem exclusivamente na aprendizagem. Estão excluídos as entradas humanas ( `type=1` ), os agentes com `talkWithAgent=false` e quaisquer agentes presentes apenas em fontes antigas.

Apenas o recurso selecionado responde se existir em `SysResourceIA` , possuir `active=true` e estiver habilitado para o canal. Uma lista auxiliar `SelectedAgentResourceIds` não pode adicionar outros agentes quando a carga contém uma seleção autorizada.

Imediatamente antes de criar cada execução, a API consulta de forma direcionada
 `dbo.SysResource2Agent` para `IDHumanResource` e requer uma proporção `Active=1`.
O `IDAgentResource` obtido é sincronizado com o `SysResourceIA` e substitui
qualquer valor local anterior. A mesma verificação sincroniza `active=true`
quando existe uma relação ativa e `active=false` quando não existe. Ele funciona
antes do filtro de agente/canal local, evitando um valor PostgreSQL
Obsoleto impede que um agente confirmado pelo SQL Server responda. A ausência
Agente no SQL Server prevalece
sobre qualquer configuração ou cache existente no PostgreSQL: se o SQL Server não
confirmar a relação, estiver inativo ou a verificação falhar, esse agente será ignorado
e nenhuma resposta é gerada ou enviada para o SolidSET.

A resposta inverte sempre a relação da mensagem original. Se a entrada for `Alejandro -> Víctor`, o agente inicia sessão com a conta de Victor e publica `Víctor -> Alejandro`: `Destiny.WorkRoom` mantém o canal e `Destiny.Dests[0].Resource`/`Login` contém o recurso e o login do autor original. Esta inversão é aplicada após a seleção do agente, uma vez que a descoberta inicial apenas pode aprender uma identidade global e não todos os agentes dinâmicos registados.

O `Destiny.Dests[0].Type=2` e o `Destiny.Dests[0].Kind=2` são enviados no formulário de resposta para que as versões novas e antigas do SolidSET reconheçam a intervenção da IA.

 `Chat.resourceTable` por si só nunca seleciona agentes.  `Chat.destiny` apenas os seleciona através do `talkWithAgent=true` ou através do antigo chat privado e atendendo a regras específicas. Assim, estar presente no canal não autoriza o agente a responder. Caso o recurso destinatário ativo ainda não tenha relação com canal privado ou dinâmico, o router cria `SysChatIAResource(IDResource, IDWorkRoom)` com `active=true` exclusivamente para esse destino.

 `Chat.channels[].idChannel` e `Chat.idWorkRoom` são interpretados como `SysWorkRoom.IDWorkRoom`.

Uma mensagem humana pode ter o mesmo `Sender.resource` que o agente configurado. O agente pode responder porque o SolidSET utiliza esta funcionalidade como uma identidade partilhada; Apenas as mensagens que chegam marcadas com `Info.generated_by_ia` são descartadas.

No seu chat privado (`Chat.channels[].channelKind=1`) é válido conversar com o agente associado ao mesmo recurso de utilizador. Quando `Destiny.dests` está vazio, o router assume exclusivamente `Chat.destiny[].idResource` com `type=1` como proprietário do canal privado. Este recurso deve ainda existir como agente ativo. Esta exceção aplica-se apenas a chats privados e não altera a regra da reunião, em que `type=1` é o autor e nunca responde.

Quando o proprietário conversa com a sua própria IA, a resposta retém
 `SysResourceIA.IDResource` para login, permissões e participante humano.
O participante De e a identidade lógica enviada
 `Info[agent_resource_id]`, `IDAgentIA`, `Info[id_agent_ia]` e `Info[agent_id]`
utilize sempre `SysResourceIA.IDAgentResource`, sincronizado de
 `dbo.SysResource2Agent.IDAgentResource`; a chave interna nunca é utilizada
 `SysResourceIA.ID`. SolidSET persiste remetente efetivo da sessão
autenticado utilizando o `St_SendMessageSync(req, currentL, currentS, currentR)` .

Para apresentar um autoresponder à esquerda, o cliente SolidSET deve considerar a flag do agente no cálculo de `ChatView.FromSelf`: se `Info[generated_by_ia]=1` e `Info[id_agent_ia]` contiverem um UUID diferente, a mensagem deverá ser tratada visualmente como `FromSelf=false`, mesmo que `Chat.IDSender` / `IDSenderResource` correspondam ao utilizador autenticado. A alternativa estrutural é registar para cada agente um login independente e recurso SolidSET; nesse caso, não é necessária uma exceção visual.
### Respostas nas reuniões

Quando a mensagem contém `Info.meeting_id` , `ExtraData.meeting_id` ou `Chat.idMeeting` , a resposta é mantida na reunião.  O `meeting_mirror_general` já não é necessário para detetar este contexto.

O formulário enviado para `/Chat/SendMessageForm` inclui:

```text
Destiny.WorkRoom      = canal técnico subyacente
Info[meeting_id]      = UUID del meeting
Info[meeting_code]    = código opcional, por ejemplo M10
ExtraData             = {"meeting_id":"...","meeting_code":"M10"}
```
O `WorkRoom` é retido apenas porque o SolidSET o utiliza como caminho de transporte.  `ExtraData.meeting_id` é quem liga o novo chat à reunião e ativa as validações de participantes bloqueados ou expulsos mostradas pelo `MeetingChatSendGuard`. A API não adiciona `Info[meeting_mirror_general]` , evitando transformar a resposta num espelho geral do canal.

Antes de enviar, a API valida se `meeting_id` existe em `dbo.SysMeeting` , está ativo e se o seu `IDChannel` corresponde a `Destiny.WorkRoom` . Caso o identificador recebido esteja obsoleto, tente resolver a reunião utilizando o `meeting_code` dentro do mesmo canal. Caso nenhuma reunião corresponda, ignore o âmbito da reunião e envie para o canal técnico, evitando conflitos com o FK `FK_SysChat2SysWorkRoom_SysMeeting`.

Quando `Chat.chatQuestion` está presente, o agente recebe `chatQuestion.rawMessage` e `chatQuestion.idChat2` como contexto para a mensagem citada. A solicitação atual mantém-se `RawMessage` ; A mensagem citada não substitui o autor, os destinatários ou a reunião atual e é tratada como conteúdo não fidedigno, não como uma instrução do sistema.

As questões operacionais sobre os participantes da reunião são resolvidas de forma
determinístico no SQL Server utilizando os métodos `SysMeeting` , `SysMeeting2Resource` e
 `SysResources`. O `meeting_id` incluído na carga útil define o âmbito do
consulta, pelo que não é obrigatório repetir a palavra “reunião” nas perguntas
como "Diz-me quais são os recursos ativos". Os relacionamentos pendentes são excluídos,
bloqueado ou expulso e a contagem, a lista nominal de recursos são suportados
ativos e a identificação do recurso criador. Estas consultas não são delegadas no
LLM nem para a descoberta gratuita de tabelas.

A língua do `RawMessage` atual tem prioridade absoluta sobre `Locale`, país,
instância, memória de conversação, documentos recuperados e resultados SQL. O
API deteta espanhol, português ou inglês em cada pedido e constrói ou normaliza
a resposta nesse mesmo idioma.  `Locale=pt-PT` apenas adapta as variantes
regional quando a mensagem é escrita em português; não é possível converter um
Pergunta em espanhol ou inglês com resposta em português.

Para qualquer dúvida sobre recursos, canais, reuniões, atividades ou tarefas
A corrente `Qdrant -> SolidSET Data API -> SQL Server` é obrigatória.
Primeiro, é consultado o conhecimento vetorial isolado do agente e do canal. Um
o resultado só é considerado referente se atingir o limite semântico configurado
por `BUSINESS_RAG_MIN_SCORE` (valor por defeito `0.60` ). Se não houver evidências
suficiente, a API consulta os dados operacionais utilizando a API SolidSET Data;
o agente não se liga diretamente ao SQL Server. Estas intenções nunca usam
pesquisa na web e LLM não podem substituir a consulta por nomes de tabelas
inventadas ou por explicações genéricas.

Se o SQL Server rejeitar uma coluna ou tabela, a API Data irá devolver um erro
estruturado `COLUMN_NOT_FOUND` ou `TABLE_NOT_FOUND` sem expor o traço completo.
O agente pode atualizar o fragmento do catálogo e realizar no máximo uma
correção baseada em identificadores reais; Não entra em tentativas ilimitadas.

As questões sobre o estado ativo ou operacional constituem uma exceção
autoridade, e não ordem: Qdrant é consultado em primeiro lugar, mas uma coincidência histórica
não pode substituir os identificadores atuais recebidos na carga útil. Consultas
como “participantes”, “nomes”, “recursos ativos”, “estado atual”, contagens ou
As listagens são sempre verificadas através do SolidSET Data API/SQL Server utilizando
 `Chat.idMeeting` , `Info.meeting_id` , `IDWorkRoom` e os restantes identificadores do
mensagem. Para estes casos, o SQL é a fonte autorizada e o LLM apenas pode escrever
os dados obtidos; Não pode responder com instruções genéricas sobre reuniões.

### Catálogo de esquemas e consultas dinâmicas seguras

A API do agente nunca descobre o esquema ligando-se diretamente ao SQL
Servidor. A API SolidSET Data expõe o `GET /api/v1/schema/catalog`, autenticado com
 `X-SolidSET-Data-Key` , que devolve tabelas, colunas, tipos, nulidade `dbo`,
chaves primárias e chaves externas. Parâmetro opcional `tables` aceita nomes
separados por vírgulas para devolver apenas o fragmento necessário.

O agente seleciona as tabelas candidatas com base na entidade comercial e pré-carrega essas tabelas.
fragmento antes de solicitar ao modelo uma nova consulta. O modelo só pode gerar
um `SELECT` /CTE, devem ser utilizadas relações presentes no catálogo, marcadores `%s` e
a matriz `parameters_json`. A execução continua através
 `POST /api/v1/query/read` , com limite de linha, tempo limite, rejeição de gravação,
comentários, múltiplas declarações, declarações de controlo e referências a outros
bases de dados. Frequentemente, as consultas retêm os seus modelos determinísticos;
O SQL dinâmico é apenas o substituto para uma nova intenção operacional.

A API SolidSET Data regista os rastreios operacionais seguros para diagnosticar erros
 `503` - tentativa e resultado de ligação (`host`, instância, porta e base de dados),
rótulo da operação, identificador SHA-256 curto da consulta, número de
parâmetros, linhas, colunas e duração. Em caso de falha, inclua o tipo e uma mensagem
limitado. Os traces não mostram a consulta, os seus parâmetros, o utilizador, a palavra-passe
nem a chave API.

Quando a API SolidSET Data é executada dentro do Docker, o SQL Server aloja
 `localhost` , `127.0.0.1` e `::1` resolvem para `host.docker.internal` , uma vez que
o endereço de loopback do contentor não representa o host. Se o SQL Server estiver ativado
outro contentor deve ter o seu nome DNS de serviço configurado (por ex.
 `sqlserver` ) e ambas as aplicações devem partilhar uma rede Docker.

####`POST /api/v1/agent/solidset/instances/{code}/schema/refresh`

Obtém todo o catálogo da API SolidSET Data configurada para o
instância e guarda-a no PostgreSQL em `SysSolidSETSchemaSnapshot` . Devolva o
estado, base de dados, número de tabelas, hash e data de captura. As mensagens de
a saída está em português de Portugal e a descrição do Swagger está em inglês.

####`GET /api/v1/agent/solidset/instances/{code}/schema`

Retorna o instantâneo mais recente do PostgreSQL sem abrir uma ligação ao SQL Server.
Cada instância mantém o seu próprio catálogo e hash, permitindo o suporte de versões.
de diferentes esquemas sem misturar tabelas ou relações entre instalações.

Nas reuniões, `Chat.destiny` é a fonte canónica para decidir qual o agente que responde:

```text
Chat.destiny[].type = 1 → autor de la pregunta; nunca responde
Chat.destiny[].type = 2 → destinatario solicitado; puede responder
Chat.destiny[].sequence → orden de los destinatarios
```
Quando `Chat.destiny` está presente, o router ignora `Destiny.dests` , uma vez que esta última coleção pode conter cópias técnicas para o autor e outros participantes da reunião. Apenas os recursos `type=2` passam pelas validações `SysResourceIA.active` e atribuição ao canal técnico. Se existir apenas uma entrada `type=1`, não será executado qualquer agente.  O `Destiny.dests` será utilizado como substituto apenas se a carga útil da reunião não contiver `Chat.destiny` .

---

## 10. Capturar e encaminhar FrameworkHub

```http
POST /api/v1/agent/notification/frameworkHub/SendMessage
```
Funciona como um proxy entre o SolidSET e o endpoint das notificações reais.

Fluxo:

```text
Mensaje entrante
    ↓
Captura e indexación
    ↓
Reenvío al endpoint real de SolidSET
    ↓
Programación de agentes seleccionados
```
Preserva o corpo, os cabeçalhos e os parâmetros relevantes.

A resposta inclui cabeçalhos como:

```text
X-Agent-Capture-Learned
X-Agent-Replies-Scheduled
```
# Conversa tradicional

## 11. Diálogo com um único agente

Saudações identifique respeitosamente o locutor utilizando o `FullName` associado ao recurso, por exemplo: `¡Hola, Alejandro Veitia! Es un placer saludarte. ¿En qué puedo ayudarte?` . Não mostram o alias do recurso, perfil, função, permissões ou número ou nomes de canais. Se `FullName` não puder ser resolvido, será utilizada a mesma saudação sem nome.

```http
POST /api/v1/agent/dialogue
```
Processe um `FrameworkMessage` utilizando o agente tradicional.

Utiliza principalmente:

```json
{
  "RawMessage": "Consulta del usuario",
  "Sender": {
    "resource": "UUID-del-usuario",
    "login": "UUID-del-login"
  },
  "Destiny": {
    "workRoom": "UUID-del-canal"
  }
}
```
Recursos:

- Valida o conteúdo e o comprimento.
- Deteta injeção imediata.
- Resolve utilizador, recurso e canal.
- Recupera contexto SQL Server e Qdrant.
- Utilize memória Redis.
- Execute ferramentas permitidas.
- Retorna uma única resposta.

Para novos desenvolvimentos com vários agentes deve ser preferido o `/multi-agent/dialogue`.

---

## 12. Registar feedback

```http
POST /api/v1/agent/feedback
```
Grave um sinal de avaliação, correção ou aprendizagem.

```json
{
  "session_id": "session-123",
  "user_id": "usuario",
  "user_text": "La pregunta original",
  "agent_response": "La respuesta del agente",
  "corrected_response": "La respuesta correcta",
  "canal_id": "UUID-del-canal",
  "feedback_type": "explicit",
  "reason": "La causa indicada era incorrecta",
  "update_profile": true
}
```
Recursos:

- Analise a reação.
- Guarde a correção.
- Atualizar perfil dinâmico.
- Incorpora a aprendizagem a longo prazo.

Para feedback multiagente, seria aconselhável que o SolidSET também retivesse o `IDAgentResource` que produziu a resposta.

---

## 13. Capte uma reação SolidSET

```http
POST /api/v1/agent/solidset/reactions/capture
```
Recebe o mesmo contrato de `ChangeReactionRequest` após SolidSET definir `IDUser` na sessão:

```json
{
  "IDChat": 1822812,
  "IDUser": "1790fc78-023d-4506-a7e8-5c030e9386d1",
  "IDChannel": "d8e82821-d52f-44bf-9b70-682651a6196e",
  "IDEmoji": "U+1F64F",
  "Counter": 1
}
```
A API consulta `dbo.SysChat.IDChat2` , verifica se o remetente está registado em `SysResourceIA` e se a mensagem começa por `Asistente IA` . Em seguida, guarda o evento no PostgreSQL `SysAgentIAReaction` e incorpora-o na aprendizagem isolada do agente que emitiu a resposta.

Este ponto final fecha um ciclo de Aprendizagem por Reforço baseado na memória de preferência:

```text
positive → reward = +Counter
negative → reward = -Counter
neutral  → reward = +0.1 × Counter
removed  → reward = 0
```
Antes de gerar respostas futuras, o agente consulta as recompensas do seu canal. Os padrões positivos são apresentados como exemplos cujo foco e clareza devem ser encorajados; os negativos como padrões que deve corrigir e evitar. A política é isolada por `IDAgentResource` e `IDChannel`, não mistura reações entre agentes e nunca expõe recompensas internas ao utilizador.

Este é o RL com recuperação de memória e preferência, apropriado para o melhoramento online seguro. Não altera os pesos do modelo básico nem copia literalmente as respostas anteriores.

Possíveis sinais:

```text
positive → aprobación, agradecimiento, corazón, celebración
negative → desaprobación, enfado o tristeza
neutral  → emoji sin clasificación explícita
removed  → Counter = 0; se registra la retirada pero no se aprende
```
A combinação `(IDChat, IDUser, IDEmoji)` é idempotente. Repetir o mesmo contador não gera aprendizagem duplicada.

Responder:

```json
{
  "status": "captured",
  "learned": true,
  "changed": true,
  "signal": "positive",
  "reward": 1.0,
  "IDChat": 1822812,
  "IDAgentResource": "272700d8-d1ba-46a6-a121-b76fce8ecb9f",
  "AgentName": "Victor Vargas"
}
```
# Memória e ficheiros

## 14. Verifique o histórico

```http
GET /api/v1/agent/history/{session_id}
```
Retorna as mensagens armazenadas no Redis.

Parâmetros opcionais:

```text
before
limit
```
Utilizado para paginar o histórico de uma conversa.

No multiagente, o identificador interno inclui o agente, o canal e a sessão.

---

## 15. Apagar histórico

```http
DELETE /api/v1/agent/history/{session_id}
```
Limpa a memória Redis correspondente a uma sessão.

É uma operação destrutiva: elimina o histórico de conversação dessa chave.

---

## 16. Obter áudio gerado

```http
GET /api/v1/agent/audio-response?file=nombre.mp3
```
Retorna um ficheiro de áudio criado anteriormente pelo agente.

Valida que o ficheiro existe e que o caminho solicitado é seguro.

# Monitorização e diagnóstico

## 17. Situação geral do agente

```http
GET /api/v1/agent/health
```
Verifique o estado do serviço e dependências como:

-Olhama.
-Qdrant.
- Redis.
- PostgreSQL.
-Servidor SQL.
-SólidoSET.
- API de notificação.

É o principal endpoint para monitorização.

---

## 18. Resumo da avaliação

```http
GET /api/v1/agent/evaluation/summary
```
Retorna métricas operacionais relacionadas com:

- Diálogos processados.
- Duração.
- Cache.
- Erros.
- Capturar notificações.
- Respostas automáticas.
- Estado das integrações.

---

## 19. Mensagens recentes capturadas

```http
GET /api/v1/agent/notification/recent-messages?limit=30
```
Retorna as últimas mensagens captadas pelo ouvinte.

O limite permitido situa-se entre 1 e 200.

É utilizado para verificar se o SolidSET está a enviar corretamente:

- Mensagem.
- Canal.
- Remetente.
- Identificadores.
- Tipo de evento.

---

## 20. Contexto de um utilizador

```http
GET /api/v1/agent/context/{user_id}
```
Retorna o contexto computado para um utilizador:

- Identidade.
- Papéis.
- Canais.
- Atividades recentes.
- Recursos disponíveis.
- Autorizações.
- Perfil aprendido.

É utilizado principalmente para depuração.

---

## 21. Métricas de nova tentativa SQL

```http
GET /api/v1/agent/sql-retry-stats
```
Amostra:

- Novas tentativas de ligação.
- Tentativas de consulta.
- Operações que geraram novas tentativas.
- Data da última tentativa.

---

## 22. Repor métricas SQL

```http
POST /api/v1/agent/sql-retry-stats/reset
```
Repõe as métricas de repetição SQL para zero.

Não modifica tabelas ou dados SolidSET; apenas reinicia os contadores internos.

# Conectividade

## 23. Experimente o SolidSET

```http
GET /api/v1/connectivity/solidset
```
Testa a conectividade com o SolidSET, normalmente através do seu endpoint de pulsação.

É utilizado para diagnosticar:

- URL incorreto.
- Serviço não disponível.
- Tempo esgotado.
- Problemas de TLS.
- Resposta HTTP inesperada.

---

## 24. Teste todas as integrações

```http
GET /api/v1/connectivity/all
```
Executa uma verificação conjunta dos serviços configurados.

Permite localizar rapidamente se o problema está em:

-SólidoSET.
- API de notificação.
- PostgreSQL.
-Servidor SQL.
- Redis.
-Qdrant.
-Olhama.

## Tabelas e responsabilidades

```text
SysResourceIA ──────────────┐
Identidad del agente        │
                            ├──► SysChatIAResource
SysWorkRoom ────────────────┘    Asignación agente–canal
Catálogo de canales                  │
                                     ├── active
                                     └── response_order
                                     │
                                     ▼
SysAgentIASession
    Conversaciones por agente y canal

SysResourceIAKnowledge
    Conocimiento privado por agente y, opcionalmente, canal
        │
        ▼
Qdrant
    Recuperación semántica aislada

SysAgentIASession
        │
        ▼
Redis
    Memoria conversacional rápida
```
A regra central é:

```text
Un mensaje puede seleccionar varios agentes.
Cada agente se valida y ejecuta por separado.
Cada agente mantiene su propia sesión, memoria y conocimiento.
```
# Conectividade de produção: SQL Server e Qdrant

Cada SQL Server é configurado no PostgreSQL utilizando o endpoint da instância.
Já não existem variáveis `SQL_SERVER_HOST` , `SQL_SERVER_INSTANCE` ,
 `SQL_SERVER_PORT`, `SQL_SERVER_DB`, `SQL_SERVER_USER` ou
 `SQL_SERVER_PASSWORD` nos ficheiros `.env` ou Compose. Uma instância nomeada
utilize `Host` mais `InstanceName`; Para ligação TCP direta, deixe
 `InstanceName=null` e a porta publicada são indicados.

A produção `.env` deve declarar `ENVIRONMENT=production`, utilizar
 `OLLAMA_BASE_URL=http://ollama-llm:11434` e não contém uma conta global em
 `SOLIDSET_LOGIN_*`; a identidade para responder é obtida a partir de `SysLogin` de acordo com
o recurso do agente selecionado.

Os endpoints de sincronização manual requerem agora o parâmetro
 `instanceCode`, por exemplo
 `POST /api/v1/agent/solidset/resources/sync?instanceCode=solidset-lisboa`.
O mesmo se aplica a `logins/sync`, `workrooms/sync` e `chat-workroom/sync`.
A ingestão histórica percorre cada instância com a sua própria ligação e cursores;
uma instância offline configurada é ignorada, sem recurso a outra base.

Após a atualização de uma instalação existente, o pedido inicial recomendado
é: implantar e configurar o seu `solidset-data-api`, registar a instância com
 `DataAPI`, execute `test-connection`,
sincronizar `resources` , `logins` , `workrooms` e `chat-workroom` , e por fim
retomar a ingestão histórica. A sincronização de recursos cria o âmbito de
instância necessária; até então os agentes são deliberadamente omitidos.

 O `SysSolidSETInstanceResource` regista quais os recursos que foram descobertos em cada
instalação. A validação histórica requer esta relação para além de um agente
ativo, evitando utilizar recursos pertencentes a outra instância.
As contas são também replicadas em `SysSolidSETInstanceLogin`, cuja chave é
 `(IDSolidSETInstance, IDLogin)`. O login de uma resposta é resolvido pelo
instância de URL de destino; mesmo que duas instalações reutilizem o mesmo
login GUID, as suas passwords não se sobrepõem.

A antiga conta global SolidSET está desativada em
 `docker-compose-prod.yml`. Cada resposta faz login com o `SysLogin` do
recurso de agente armazenado no PostgreSQL.

O Qdrant tem uma verificação de integridade TCP. O agente não arranca até
que o `vector-db:6333` aceita ligações, impedindo a criação inicial do
A coleção `machining_docs` falhou devido a uma corrida inicial.

O ciclo periódico de aprendizagem da estrutura processa todas as instâncias
SolidSETs ativos que possuem uma API de dados ativa. Cada corrida estabelece o seu próprio
contexto da instância antes de consultar o SQL Server e regista o resultado por
 `instanceCode`. Uma falha numa instalação não impede as outras; o ciclo está marcado
como `partial`. Só é considerado com falha quando nenhuma instância pode ser ingerida.

## Ingestão retroativa de conhecimento SolidSET

A ingestão histórica é independente das respostas em tempo real. sozinho
cria processos para recursos com o `SysResourceIA.active=true`, um
 `IDAgentResource` e uma relação `dbo.SysResource2Agent.Active=1` verificada.

Cada agente ativo possui cursores independentes:

```text
solidset_chat_history:{IDResource}
solidset_task_history:{IDResource}
```
O cursor do chat lê `dbo.SysChat` incrementalmente por `IDChat2` e inclui
apenas as mensagens que o recurso escreveu, recebeu como participante ou pode
Verifique os seus relacionamentos `SysChatIAResource` ativos. Os documentos são
São classificados como `owner`, `workroom`, `private` ou `meeting`.
 O `SysChat.IDMeeting` é opcional dependendo da instalação. Antes de extrair, o
consulta do produtor `INFORMATION_SCHEMA.COLUMNS` ; Se não existir, projete
 `NULL AS IDMeeting` e continua sem classificar estas mensagens como reuniões.

O cursor de tarefas descobre as colunas instaladas do `dbo.SysTask` e as suas
tabelas relacionais. Extraia apenas as tarefas em que o recurso esteja listado como criador,
responsável, proprietário, cessionário ou participante. Se a instalação não expor
 `IDTask` ou uma relação verificável com recursos, esta fonte é omitida
seguro e nunca se torna conhecimento global.
Discovery inclui `DATA_TYPE` - apenas colunas
 `uniqueidentifier` pode estar relacionado com um recurso ou login. Colunas
Os homónimos `tinyint`, `int` ou outros tipos são ignorados e `IDTask` deve ser um
identificador incremental numérico.

Por segurança, inicia e em modo de simulação:

```env
HISTORICAL_INGESTION_ENABLED=false
HISTORICAL_INGESTION_DRY_RUN=true
HISTORICAL_INGESTION_BATCH_SIZE=500
HISTORICAL_INGESTION_STREAM=machining:historical-ingestion:v1
HISTORICAL_INGESTION_GROUP=historical-workers-v1
HISTORICAL_INGESTION_STREAM_MAXLEN=10000
HISTORICAL_INGESTION_MAX_RETRIES=3
HISTORICAL_INGESTION_CLAIM_IDLE_MS=60000
HISTORICAL_INGESTION_STALE_SECONDS=300
HISTORICAL_INGESTION_POLL_SECONDS=60
HISTORICAL_INGESTION_ADMIN_KEY=<secreto-administrativo>
DB_INGEST_CONNECT_TIMEOUT_SECONDS=15
DB_INGEST_QUERY_TIMEOUT_SECONDS=120
```
`DB_INGEST_CONNECT_TIMEOUT_SECONDS` limita abertura de ligação com SQL
Server e `DB_INGEST_QUERY_TIMEOUT_SECONDS` limitam cada lote de extração.

Todas as operações requerem o cabeçalho `X-Agent-Admin-Key`, cujo valor
deve ser exactamente aquele que está configurado em `HISTORICAL_INGESTION_ADMIN_KEY` .
O cabeçalho é declarado no OpenAPI e aparece como um campo obrigatório no
Arrogância. Se for omitido, a API irá retornar `422` ; se não corresponder, devolve `401` .

```http
POST /api/v1/agent/historical-ingestion/start
POST /api/v1/agent/historical-ingestion/pause
POST /api/v1/agent/historical-ingestion/resume
POST /api/v1/agent/historical-ingestion/approve-dry-run?instanceCode=local-solidset
GET  /api/v1/agent/historical-ingestion/status
GET  /api/v1/agent/historical-ingestion/batches?limit=50
DELETE /api/v1/agent/historical-ingestion/messages/{idChat2}?instanceCode=local-solidset&sourceType=chat
```
O corpo do `start` é:

```json
{"instanceCode":"local-solidset","dryRun":true}
```
`dryRun` normaliza, rejeita segredos e valida escopos sem gerar embeddings
nem avance `LastIDChat2` . Depois de analisar a auditoria,
 `approve-dry-run` e depois para `start` com `dryRun=false` .

As mensagens de IA, os segredos, as mensagens vazias e os registos sem autor/canal são
rejeitam. O conhecimento é armazenado apenas para o agente alvo com
escopo `owner`, `workroom`, `private`, `meeting` ou `task`;  `global` permanece
desativado. Os pontos Qdrant incluem `agent_resource_id`, `canal_id`,
 `source_type`, `source_id`, `scope`, `id_chat2` e `content_hash`.

O produtor funciona também como um reconciliador. Quando um agente aparece
novo ativo, cria automaticamente os seus cursores de chat e tarefas. Se existirem
documentos anteriores desse agente, parte da origem máxima confirmada; sim
É realmente novo, começando do zero. A ativação de um agente não reinicia os outros. Um
o agente desativado deixa de produzir lotes e o trabalhador verifica novamente o seu
estado antes de indexar qualquer trabalho pendente.

O PostgreSQL preserva cursores, auditoria em batch e a relação exata entre
 `IDChat2` e `QdrantPointID`. O ponto final DELETE elimina os pontos e marca o
documentos como eliminados.

Na produção podem ser mantidos simultaneamente:

```env
HISTORICAL_INGESTION_ENABLED=true
HISTORICAL_INGESTION_DRY_RUN=false
```
Retomar após reiniciar o Docker utiliza o PostgreSQL como ponto de verificação
durável.  A `SysAgentIAIngestionCursor.LastIDChat2` representa exclusivamente o
última mensagem cujo lote foi finalizado com sucesso e `CurrentBatchID` identifica a
lote em curso. O cursor é atualizado monotonicamente - uma versão antiga
recuperado do Redis nunca poderá reduzir o `LastIDChat2` .

Redis persiste o Stream via AOF no volume `redis_data` . Depois de um
reiniciar, um trabalhador afirma em aproximadamente
 `HISTORICAL_INGESTION_CLAIM_IDLE_MS` as mensagens pendentes do consumidor
anterior. Se o Redis perder o lote inteiro, o produtor detetará um cursor
 `queued` ou `processing` abandonado após
 `HISTORICAL_INGESTION_STALE_SECONDS`, mantém o último checkpoint confirmado
e extraia novamente de `LastIDChat2 + 1` .

O último lote pode ser processado novamente após uma interrupção, mas não
conhecimento duplicado: `DocumentID` e `QdrantPointID` são UUIDs determinísticos,
O Qdrant utiliza o `upsert` e o PostgreSQL aplica chaves únicas. Esta garantia oferece
processamento eficaz *pelo menos uma vez* com um resultado idempotente, evitando tanto
perda de mensagens, como por exemplo reiniciar do zero.

 `GET /api/v1/agent/historical-ingestion/status` mostra agora também
 `CurrentBatchID` , `LastIDChat2` , `LastRunAt` e o estado de recuperação para
diagnosticar exatamente onde a ingestão continuará.

Os estados e as auditorias podem ser filtrados por agente:

```http
GET /api/v1/agent/historical-ingestion/status?resourceId={IDResource}
GET /api/v1/agent/historical-ingestion/batches?resourceId={IDResource}&limit=50
```
O `approve-dry-run` liberta todos os cursores de chat e tarefas do `dry_run` no
instância selecionada. O cursor global das versões anteriores está marcado como
 `superseded` e deixe de participar no planeamento.

O terminal DELETE utiliza o `sourceType=chat` por defeito. Para remover
um documento de tarefa é enviado `sourceType=task` e o valor do caminho é
interpretado como `IDTask` , evitando colisões entre `IDChat2` e `IDTask` .

Serviços Docker:

```powershell
docker compose -f docker-compose-prod.yml up -d historical-worker historical-producer
docker compose -f docker-compose-prod.yml up -d --scale historical-worker=2
```
## Organização arrogante

Swagger (`/docs`) apresenta documentação pública de endpoint em inglês
e agrupa operações utilizando tags OpenAPI estáveis:

-`Conversation`
-`SolidSET Notifications`
-`Asynchronous Responses`
-`Historical Ingestion`
-`SolidSET Agents`
-`SolidSET Configuration`
-`LLM Providers`
-`Learning and Feedback`
-`Audio, History and Context`
-`Observability`
-`Connectivity`

A classificação apenas modifica a apresentação e documentação do OpenAPI; não
alterar os URLs, corpos, respostas ou comportamento dos endpoints.

As mensagens humanas devolvidas pela API (`detail`, `message`, erros
validação e diagnóstico) utilizam o português de Portugal. Os códigos técnicos
consumido pelos clientes (`queued`, `processing`, `completed`, `failed`, etc.)
São mantidos estáveis para não quebrar a integração com o WPF. O texto gerado
pelo agente preserva o idioma solicitado pelo utilizador.

As respostas conversacionais não revelam detalhes internos de recuperação
ou armazenamento. Termos como `RAG`, `Qdrant`, `embeddings`, `base vectorial`
ou `vectorial knowledge base` são proibidos imediatamente e removidos por
uma validação final comum antes de devolver ou enviar qualquer resposta.

Quando o `Chat.chatQuestion` está presente, o `Chat.rawMessage` é a intervenção
atual e `chatQuestion.rawMessage` são mantidos apenas como contexto citado.
A intervenção atual pode sempre alimentar a aprendizagem. Basta gerar um
resposta se contiver pergunta, pedido, saudação ou continuação; um
declaração informativa ou correção sobre a mensagem citada é classificada como
 `respuesta_citada_solo_aprendizaje` e não provoca resposta automática.

A deteção de idioma também se aplica a mensagens curtas e respostas
deterministas que não passam pelo LLM. Expressões como `Bom dia` , `Boa tarde` ,
 `Good morning` , `Good evening` , `Buenos días` e os seus equivalentes geram o
responder diretamente em português, inglês ou espanhol, respetivamente.

As mensagens declarativas são linguisticamente distintas das perguntas e
solicitações. As estruturas factuais são consideradas sinais de conhecimento,
datas, conteúdo longo ou multilinha e expressões como `ten en cuenta` ,
 `para seu conhecimento` ou `remember that`. A mensagem é aprendida, mas não
envia para o LLM. Se foi endereçado explicitamente ao agente, a única resposta
é um breve agradecimento na língua detectada; num canal sem destino
Diretamente aprende-se silenciosamente.
