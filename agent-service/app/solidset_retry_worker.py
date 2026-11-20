from __future__ import annotations

import asyncio

import redis

from app.agent.tools import solidset_send_chat_message
from app.config import settings
from app.solidset_retry_queue import SolidSETRetryQueue


async def run_worker() -> None:
    queue = SolidSETRetryQueue()
    try:
        recovered = await asyncio.to_thread(queue.recover_processing)
        if recovered:
            pass
    except redis.RedisError as exc:
        pass

    pass
    while True:
        try:
            items = await asyncio.to_thread(queue.claim_due)
            for item in items:
                arguments = dict(item.get("arguments") or {})
                arguments["enqueue_on_connection_error"] = False
                result = str(await asyncio.to_thread(solidset_send_chat_message.invoke, arguments))
                if result.startswith("✅"):
                    await asyncio.to_thread(queue.acknowledge, str(item["id"]))
                    pass
                elif result.startswith("Error de conexión enviando"):
                    await asyncio.to_thread(queue.retry, item, result)
                    pass
                else:
                    # Un rechazo HTTP/funcional no mejora reintentándolo y
                    # podría repetir indefinidamente una acción inválida.
                    await asyncio.to_thread(queue.acknowledge, str(item["id"]))
                    pass
        except redis.RedisError as exc:
            pass
        except Exception as exc:
            pass
        await asyncio.sleep(settings.SOLIDSET_RETRY_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_worker())
