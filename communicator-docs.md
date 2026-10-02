# Documentação de integração e consumo: `AIAgentClient`

## 1. Localização da Classe

* **Ficheiro da classe:** 
    * `WpfIsiframeControls/IsiFrameServer.cs`
    * `WpfIsiframeControls/ChatViews/AiAdviceWindow.xaml.cs`
    * `WpfIsiframeControls/ChatViews/ChatInputBox.xaml.cs`
    * `WpfIsiframeControls/Infrastructure/AgenteIA/AIAgentClient.cs`
* **Propósito:** Atua como cliente HTTP/REST para notificar, enviar eventos e gerir a interação entre o cliente de desktop (WPF) e o serviço/servidor de IA.

---

## 2. Ficheiros do Sistema Envolvidos

### Capa WPF (`WpfIsiframeControls`)

* **`AIAgentClient.cs`**: Implementação direta do cliente HTTP.
* **`IsiFrameServer.cs`**: Ponto nevrálgico de dispatching onde se invoca o cliente para enviar notificações de mensagens.
  * **`ChatUpdateReaction`**: Lida com as reações de atualização de chat, garantindo que as alterações sejam refletidas corretamente na interface e notificadas ao agente IA.
* **`ChatInputBox.xaml.cs`**: Componente responsável pela entrada de mensagens no chat, incluindo a captura de texto e envio de mensagens para o canal apropriado.
  * **`SendMessageToAIAgentAsync`**: Método responsável por enviar mensagens capturadas pelo `ChatInputBox` diretamente para o agente IA.
* **`AiAdviceWindow.xaml.cs`**: Componente responsável por exibir sugestões de IA ao utilizador.
  * **`RequestSuggestionsAsync`**: Método responsável por solicitar sugestões ao agente IA com base na pergunta do chat.

---

## 3. Métodos Principais e Onde São Utilizados

### `SendMessageToAIAgentAsync`
### `RequestSuggestionsAsync`
### `ChatUpdateReaction`

Reenvia as mensagens do Framework (`FrameworkMessage` serializado em JSON camelCase) para a API da IA através do método `SendMessageToAIAgentAsync`.

* **Endpoint consumido:** 
  * `POST /api/v1/agent/notification/framework-message`
  * `POST /api/v1/agent/solidset/reactions/capture`
  * `GET /api/v1/agent/responses/{idchat}/status`
  * `POST /api/v1/agent/notification/chat-question/suggest-response`
* **URL por omissão:** `[https://agente.isicom.pt]` (configurável via `AIAgentUrl`)
* **Invocado desde / Chamado desde:**
* **IsiFrameServer.cs** : `ChatUpdateReaction`
```csharp
public async Task<ChatUpdateReactionResponse> ChatUpdateReaction(...)
{
    // Implementação do método
    var agent = new AIAgentClient();
    await agent.CaptureSolidSETReactionAsync(req);
}
```

* **AiAdviceWindow.xaml.cs** : `private async Task RequestSuggestionsAsync`
```csharp
private async void SuggestionOption_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
{
    // Implementação do método
    await RequestSuggestionsAsync(chosen, showUserBubble: true).ConfigureAwait(true);
}
```
* **ChatInputBox.xaml.cs** : `HandleChatMessage`
```csharp
public void HandleChatMessage(FrameworkMessage message, ChatMessageKind kind)
{
    // Implementação do método
    _ = SendMessageToAIAgentAsync(message);
}
```
* **AIAgentClient.cs** : Implementação direta do cliente HTTP.
```csharp
public async Task<SolidSETReactionCaptureResponse> CaptureSolidSETReactionAsync(ChatUpdateReactionRequest request)
{
    // Implementação do cliente HTTP
}

public async Task<AgentFrameworkEnqueueResult> SendFrameworkMessageForQueueAsync(FrameworkMessage message)
{
    // Implementação do cliente HTTP
}

public async Task<ChatQuestionSuggestionResponse> SuggestChatQuestionResponseAsync(FrameworkMessage frameworkMessage, CancellationToken cancellationToken = default)
{
    // Implementação do cliente HTTP
}
```

---

## 4. Endpoints consumidos

O URL base é obtido a partir de `AIAgentUrl` (por defeito, `https://agente.isicom.pt`).
Todos os corpos são enviados como JSON e o `FrameworkMessage` utiliza os nomes das propriedades em camelCase.

### 4.1 Enviar uma mensagem do framework

```http
POST /api/v1/agent/notification/framework-message
Content-Type: application/json
```

Envia à API um `FrameworkMessage` recebido desde SolidSET. O serviço valida
a instância e enfileira a mensagem para o seu processamento assíncrono pelo worker:
seleção do agente, geração da resposta e publicação posterior em
SolidSET.

Exemplo de payload para uma pergunta dirigida a um recurso IA dentro de um
canal e de uma reunião (os identificadores são fictícios):

```json
{
  "stamp": "2026-09-30T16:02:25.9913471Z",
  "sender": {
    "room": "00000000-0000-0000-0000-000000000000",
    "session": "00000000-0000-0000-0000-000000000000",
    "login": "00000000-0000-0000-0000-000000000000",
    "resource": "00000000-0000-0000-0000-000000000000",
    "team": "00000000-0000-0000-0000-000000000000",
    "role": "00000000-0000-0000-0000-000000000000",
    "conversationId": 0,
    "workRoom": "00000000-0000-0000-0000-000000000000"
  },
  "destiny": {
    "room": "00000000-0000-0000-0000-000000000000",
    "session": "00000000-0000-0000-0000-000000000000",
    "login": "00000000-0000-0000-0000-000000000000",
    "resource": "00000000-0000-0000-0000-000000000000",
    "team": "00000000-0000-0000-0000-000000000000",
    "role": "00000000-0000-0000-0000-000000000000",
    "conversationId": 0,
    "workRoom": "00000000-0000-0000-0000-000000000000",
    "resources": [
      "00000000-0000-0000-0000-000000000000",
      "00000000-0000-0000-0000-000000000000"
    ],
    "logins": [
      "00000000-0000-0000-0000-000000000000",
      "00000000-0000-0000-0000-000000000000"
    ],
    "dests": [
      {
        "login": "00000000-0000-0000-0000-000000000000",
        "resource": "00000000-0000-0000-0000-000000000000",
        "kind": 2,
        "sequence": 2
      },
      {
        "login": "00000000-0000-0000-0000-000000000000",
        "resource": "00000000-0000-0000-0000-000000000000",
        "kind": 1
      },
      {
        "login": "00000000-0000-0000-0000-000000000000",
        "resource": "00000000-0000-0000-0000-000000000000",
        "kind": 2
      }
    ]
  },
  "externalDestinations": [],
  "kind": 7,
  "rawMessage": "DIme sus caracteristicas principales?",
  "importance": 1,
  "editState": 0,
  "priority": 0,
  "modifiers": 0,
  "visibilityLevel": 3,
  "maskMessage": 536870920,
  "messageMonitoring": 0,
  "args": [1767862, 0, "0"],
  "chat": {
    "idChat2": 1767862,
    "idSender": "00000000-0000-0000-0000-000000000000",
    "idSenderResource": "00000000-0000-0000-0000-000000000000",
    "rawMessage": "DIme sus caracteristicas principales?",
    "stamp": "2026-09-30T16:02:25.9913471Z",
    "idActivityConversation": 0,
    "importance": 1,
    "editState": 0,
    "isPublic": 3,
    "attentionCallNotificationLevel": 0,
    "status": 0,
    "chatQuestionMessage": 1767861,
    "kind": 0,
    "readByCurrentResource": true,
    "messageStateForCurrentResource": 3,
    "isBookMarked": false,
    "canBookMark": true,
    "canUnBookmark": false,
    "chatQuestion": {
      "idChat2": 1767861,
      "idSender": "00000000-0000-0000-0000-000000000000",
      "idSenderResource": "00000000-0000-0000-0000-000000000000",
      "rawMessage": "FinModeler es una plataforma de decisión financiera impulsada por inteligencia artificial que guía a emprendedores y startups desde la descripción de su idea hasta la creación de proyecciones financieras detalladas. Ofrece un asistente paso a paso con IA integrada para estructurar ideas de negocio, acceso a estados financieros completos incluyendo ingresos, costos e inversiones, análisis financiero y gestión de versiones que permite comparar diferentes escenarios.",
      "stamp": "2026-09-30T16:01:27.687",
      "idActivityConversation": 0,
      "importance": 1,
      "editState": 0,
      "isPublic": 3,
      "attentionCallNotificationLevel": 0,
      "status": 0,
      "chatQuestionMessage": 1767860,
      "kind": 0,
      "readByCurrentResource": false,
      "messageStateForCurrentResource": 3,
      "isBookMarked": false,
      "canBookMark": true,
      "canUnBookmark": false,
      "idWorkRoom": "00000000-0000-0000-0000-000000000000",
      "messageMonitoring": 0,
      "maskMessage": 0,
      "questionType": 0,
      "questionStatus": 0,
      "questionCloseRequested": 0,
      "externalChannels": 0
    },
    "idWorkRoom": "00000000-0000-0000-0000-000000000000",
    "idChannelOrigin": "00000000-0000-0000-0000-000000000000",
    "originChannelName": "Testes",
    "messageMonitoring": 0,
    "maskMessage": 0,
    "destiny": [
      {
        "idLogin": "00000000-0000-0000-0000-000000000000",
        "idResource": "00000000-0000-0000-0000-000000000000",
        "type": 1,
        "idChannel": "00000000-0000-0000-0000-000000000000",
        "isOriginMessageSender": false,
        "sequence": 0,
        "action": 0
      },
      {
        "idLogin": "00000000-0000-0000-0000-000000000000",
        "userName": "Agente Financeiro",
        "idResource": "00000000-0000-0000-0000-000000000000",
        "resourceName": "Agente Financeiro",
        "talkWithAgent": true,
        "type": 2,
        "idChannel": "00000000-0000-0000-0000-000000000000",
        "isOriginMessageSender": false,
        "sequence": 2,
        "action": 0
      }
    ],
    "questionType": 0,
    "questionStatus": 0,
    "questionCloseRequested": 0,
    "externalChannels": 0,
    "channels": [
      {
        "idChannel": "00000000-0000-0000-0000-000000000000",
        "channelName": "Testes",
        "channelKind": 0,
        "kind": 0
      }
    ],
    "resourceTable": [
      {
        "idChannel": "00000000-0000-0000-0000-000000000000",
        "idResource": "00000000-0000-0000-0000-000000000000",
        "idLogin": "00000000-0000-0000-0000-000000000000",
        "userName": "Alejandro Veitia",
        "resourceName": "Dev17",
        "type": 1,
        "resourceKind": 1,
        "sequence": 0
      },
      {
        "idChannel": "00000000-0000-0000-0000-000000000000",
        "idResource": "00000000-0000-0000-0000-000000000000",
        "idLogin": "00000000-0000-0000-0000-000000000000",
        "userName": "Agente Financeiro",
        "resourceName": "Agente Financeiro",
        "type": 2,
        "resourceKind": 1,
        "sequence": 2,
        "talkWithAgent": true
      }
    ]
  },
  "workRoomData": {
    "id": "00000000-0000-0000-0000-000000000000",
    "kind": 0,
    "name": "Testes"
  },
  "chatData": {
    "idChat": 1767861
  },
  "relatedRecordsData": [],
  "info": {
    "talk_with_agent_to": "1"
  },
  "attentionCallNotificationLevel": 0,
  "attentionCallNotify": false
}
```

Para que a mensagem possa ativar um agente, `Chat.destiny` deve incluir um
destino de tipo `2` com `talkWithAgent=true` e um `idResource` válido. O campo
`Chat.idChat2` é utilizado como `requestId` para consultar posteriormente o
estado do processamento.

A resposta habitual é `202 Accepted` e inclui os identificadores necessários
para acompanhar o processamento:

```json
{
  "Result": 0,
  "requestId": "1824911",
  "status": "queued",
  "statusUrl": "/api/v1/agent/responses/1824911/status"
}
```

`requestId` corresponde normalmente a `Chat.IDChat2`. O cliente deve conservá-lo
e consultar `statusUrl` até que o estado indique que o processamento terminou.

### 4.2 Capturar uma reação de SolidSET

```http
POST /api/v1/agent/solidset/reactions/capture
Content-Type: application/json
```

Regista a reação do utilizador a uma resposta do agente. O corpo utilizado
por `CaptureSolidSETReactionAsync` é um `ChangeReactionRequest`, por exemplo:

```json
{
  "IDChat": 1822812,
  "IDUser": "00000000-0000-0000-0000-000000000000",
  "IDChannel": "00000000-0000-0000-0000-000000000000",
  "IDEmoji": "U+1F64F",
  "Counter": 1
}
```

A API valida o chat, o utilizador e que a mensagem corresponda a uma resposta
do agente antes de guardar o evento. As reações são utilizadas como memória
de preferências isolada por agente e canal; não atualizam diretamente os
pesos do modelo. `Counter = 0` representa a retirada de uma reação.

### 4.3 Consultar o estado de uma resposta

```http
GET /api/v1/agent/responses/{idchat}/status?lang=es
```

Consulta o progresso de um pedido enfileirado. Em `{idchat}` utiliza-se o
identificador devolvido como `requestId` (habitualmente `Chat.IDChat2`). O
parâmetro `lang` é opcional e controla o idioma das mensagens de estado.

A sequência normal pode ser `queued` → `processing` → `searching` →
`thinking` → `completed`; se ocorrer um erro, o estado será `failed`. O cliente
pode consultar esta URL a cada 1–2 segundos enquanto mostra um indicador de
progresso. Quando a resposta estiver disponível, o estado pode incluir o
resultado gerado e os dados do pedido original.

### 4.4 Solicitar sugestões para uma pergunta do chat

```http
POST /api/v1/agent/notification/chat-question/suggest-response
Content-Type: application/json
```

Solicita alternativas de resposta para `Chat.chatQuestion`. Recebe um
`FrameworkMessage`, mas não publica nenhuma resposta em SolidSET nem captura a
mensagem como uma autorresposta. O texto que se quer contestar obtém-se de
`Chat.chatQuestion.RawMessage` e `Chat.RawMessage` deve estar vazio; caso contrário,
a API devolve `422` para evitar substituir texto escrito pelo utilizador.

A resposta contém normalmente três alternativas independentes, além do
estado e da URL de acompanhamento:

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

O cliente seleciona a alternativa e decide se a atribui ao campo `RawMessage`
do chat. A API não adiciona o nome do agente, não envia a sugestão e não deve
tratar o texto citado como instruções confiáveis.

Erros habituais: `422` para pedidos inválidos, `404` se não existir um
agente ativo para o solicitante e `503` quando não estiverem disponíveis as
dependências ou a geração.

