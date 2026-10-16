"""Adaptador de la API de Claude (SDK `anthropic`, extra opcional: pip install -e ".[llm]").

Lee ANTHROPIC_API_KEY del entorno (lo hace el SDK). No se pasa ningún secreto al prompt.
"""

import os
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_ENV = "TRADEBOT_LLM_MODEL"


class LlmError(Exception):
    """Falló la llamada al proveedor (red, clave, límite de uso...)."""


class AnthropicClient:
    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 1500,
        timeout: float = 30.0,
        client: Any = None,
    ) -> None:
        self.model = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        self.max_tokens = max_tokens
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LlmError('falta el SDK: pip install -c constraints.txt -e ".[llm]"') from e
            client = anthropic.Anthropic(timeout=timeout, max_retries=2)
        self._client = client

    def complete(self, system: str, user: str) -> str:
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:  # noqa: BLE001 - frontera con un SDK externo: un solo tipo de error propio
            raise LlmError(f"{type(e).__name__}: {e}") from e
        return "".join(getattr(b, "text", "") for b in msg.content)
