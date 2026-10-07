"""Finite conversation cache, separate from persistent learned knowledge."""

import json

from langchain_community.chat_message_histories import RedisChatMessageHistory as BaseHistory
from langchain_core.messages import message_to_dict

from app.config import settings
from app.redis_runtime import redis_client


class RedisChatMessageHistory(BaseHistory):
    def __init__(self, session_id, url=None, key_prefix="message_store:", **kwargs):
        # BaseHistory's properties use these attributes. Avoid its unbounded client.
        self.session_id = session_id
        self.key_prefix = key_prefix
        self.ttl = settings.REDIS_HISTORY_TTL_SECONDS
        self.redis_client = redis_client(url)

    @property
    def messages(self):
        # Bound reads even for large legacy histories awaiting maintenance.
        from langchain_core.messages import messages_from_dict
        items = self.redis_client.lrange(self.key, 0, settings.REDIS_HISTORY_MAX_MESSAGES - 1)
        return messages_from_dict([json.loads(item) for item in reversed(items)])

    def add_message(self, message):
        with self.redis_client.pipeline(transaction=True) as pipe:
            pipe.lpush(self.key, json.dumps(message_to_dict(message), ensure_ascii=False))
            pipe.ltrim(self.key, 0, settings.REDIS_HISTORY_MAX_MESSAGES - 1)
            pipe.expire(self.key, self.ttl)
            pipe.execute()
