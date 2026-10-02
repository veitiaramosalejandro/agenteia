# Documentação de integração e consumo: `AIAgentClient`

## 1. Localização da Classe

* **Ficheiro da classe:** 
    * `WpfIsiframeControls/IsiFrameServer.cs`
    * `WpfIsiframeControls/ChatViews/AiAdviceWindow.xaml.cs`
    * `WpfIsiframeControls/ChatViews/ChatInputBox.xaml.cs`
    * `WpfIsiframeControls/Infrastructure/AgenteIA/AIAgentClient.cs`
* **Propósito:** Atua como cliente HTTP/REST para notificar, enviar eventos e gerir a interação entre o cliente de desktop (WPF) e o serviço/servidor de IA.

---

## 2. Métodos Principais e Onde São Utilizados

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

## 3. Ficheiros do Sistema Envolvidos

### Capa WPF (`WpfIsiframeControls`)

* **`AIAgentClient.cs`**: Implementação direta do cliente HTTP.
* **`IsiFrameServer.cs`**: Ponto nevrálgico de dispatching onde se invoca o cliente para enviar notificações de mensagens.
  * **`ChatUpdateReaction`**: Lida com as reações de atualização de chat, garantindo que as alterações sejam refletidas corretamente na interface e notificadas ao agente IA.
* **`ChatInputBox.xaml.cs`**: Componente responsável pela entrada de mensagens no chat, incluindo a captura de texto e envio de mensagens para o canal apropriado.
  * **`SendMessageToAIAgentAsync`**: Método responsável por enviar mensagens capturadas pelo `ChatInputBox` diretamente para o agente IA.
* **`AiAdviceWindow.xaml.cs`**: Componente responsável por exibir sugestões de IA ao utilizador.
  * **`RequestSuggestionsAsync`**: Método responsável por solicitar sugestões ao agente IA com base na pergunta do chat.