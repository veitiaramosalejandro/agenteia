"""Process-wide runtime dependencies shared by HTTP and queue workers."""

from app.agent.core import MachiningAgent
from app.agent.orchestrator import SolidSETOrchestrator
from app.response_queue import AgentResponseQueue
from app.services import auto_reply, suggestions
from app.suggestion_queue import SuggestionQueue
from app.system.notification_listener import NotificationApiListener
from app.redis_runtime import require_redis


require_redis()  # Fail before constructing runtimes that read Redis during import.
agent = MachiningAgent()
orchestrator = SolidSETOrchestrator(agent)
notification_listener = NotificationApiListener()
response_queue = AgentResponseQueue()
suggestion_queue = SuggestionQueue()

auto_reply.configure(agent, orchestrator, response_queue)
suggestions.configure(agent)
