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
            print(f"♻️ Recuperadas {recovered} entregas SolidSET interrumpidas", flush=True)
    except redis.RedisError as exc:
        print(f"⚠️ No se pudo recuperar la cola SolidSET: {exc}", flush=True)

    print(
        f"📮 Worker de reintentos SolidSET activo interval={settings.SOLIDSET_RETRY_INTERVAL_SECONDS}s",
        flush=True,
    )
    while True:
        try:
            items = await asyncio.to_thread(queue.claim_due)
            for item in items:
                arguments = dict(item.get("arguments") or {})
                arguments["enqueue_on_connection_error"] = False
                result = str(await asyncio.to_thread(solidset_send_chat_message.invoke, arguments))
                if result.startswith("✅"):
                    await asyncio.to_thread(queue.acknowledge, str(item["id"]))
                    print(f"✅ Entrega SolidSET recuperada id={item['id']}", flush=True)
                elif result.startswith("Error de conexión enviando"):
                    await asyncio.to_thread(queue.retry, item, result)
                    print(
                        f"⚠️ Entrega SolidSET seguirá pendiente id={item['id']} "
                        f"attempt={int(item.get('attempts') or 0) + 1}", flush=True,
                    )
                else:
                    # Un rechazo HTTP/funcional no mejora reintentándolo y
                    # podría repetir indefinidamente una acción inválida.
                    await asyncio.to_thread(queue.acknowledge, str(item["id"]))
                    print(
                        f"❌ Entrega SolidSET descartada por error no transitorio "
                        f"id={item['id']} detail={result[:300]}", flush=True,
                    )
        except redis.RedisError as exc:
            print(f"⚠️ Cola de reintentos SolidSET no disponible: {exc}", flush=True)
        except Exception as exc:
            print(f"⚠️ Error procesando cola SolidSET: {exc}", flush=True)
        await asyncio.sleep(settings.SOLIDSET_RETRY_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_worker())
