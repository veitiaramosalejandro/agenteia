from app.agent.learning import AgentLearning


def teach_agent(fact: str, category: str = "general"):
    """
    Herramienta para que los usuarios enseñen al agente.
    """
    return AgentLearning().learn_manual_result(fact, category)


def teach_from_conversation(conversation_text: str, topic: str):
    """
    Enseña al agente a partir de una conversación.
    """
    # Extraer preguntas y respuestas de la conversación
    lines = conversation_text.strip().split('\n')
    learning_pairs = []
    
    current_question = None
    for line in lines:
        if line.startswith("Pregunta:"):
            current_question = line.replace("Pregunta:", "").strip()
        elif line.startswith("Respuesta:") and current_question:
            answer = line.replace("Respuesta:", "").strip()
            learning_pairs.append((current_question, answer))
            current_question = None
    
    # Indexar cada par
    count = 0
    for question, answer in learning_pairs:
        learning_text = f"PREGUNTA: {question}\nRESPUESTA: {answer}\nTEMA: {topic}"
        if AgentLearning().learn_manual(learning_text, topic):
            count += 1
    
    return f"✅ Aprendidas {count} interacciones sobre '{topic}'"