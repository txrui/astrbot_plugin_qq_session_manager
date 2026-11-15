from __future__ import annotations

from .models import NormalizedMessage

class MessageService:
    def __init__(self, repository): self.repository = repository
    def save(self, message: NormalizedMessage, expires_at: int | None = None) -> int | None:
        return self.repository.upsert_message(message, expires_at=expires_at)
