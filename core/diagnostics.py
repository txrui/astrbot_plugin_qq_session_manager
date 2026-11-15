from __future__ import annotations

class Diagnostics:
    def __init__(self, repository): self.repository = repository
    def record(self, platform_id: str | None, category: str, detail: str = "") -> None:
        self.repository.diagnostic(platform_id, category, detail)
    def health(self): return self.repository.health_check()
