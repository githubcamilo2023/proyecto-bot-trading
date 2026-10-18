"""Avisos del bot: siempre a un archivo local y, si lo configuras, también a Telegram.

El token y el chat de Telegram se definen SOLO en la consola (TRADEBOT_TELEGRAM_TOKEN y
TRADEBOT_TELEGRAM_CHAT), nunca en archivos.
Un aviso que falla jamás detiene al bot ni escribe el token en los logs.
"""

import json
import logging
import os
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("tradebot.alerts")
Notify = Callable[[str], None]
TOKEN_ENV, CHAT_ENV = "TRADEBOT_TELEGRAM_TOKEN", "TRADEBOT_TELEGRAM_CHAT"
TIMEOUT = 10
MAX_TEXT = 1000


def _telegram(token: str, chat: str, text: str) -> None:
    req = urllib.request.Request(  # noqa: S310 - URL https fija
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=TIMEOUT).close()  # noqa: S310


def make_notifier(
    log_path: str | Path,
    env: Mapping[str, str] | None = None,
    send: Callable[[str, str, str], None] = _telegram,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Notify:
    env = os.environ if env is None else env
    token, chat = env.get(TOKEN_ENV, ""), env.get(CHAT_ENV, "")
    path = Path(log_path)

    def notify(text: str) -> None:
        text = " ".join(text.split())[:MAX_TEXT]  # una línea, sin saltos raros
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{now():%Y-%m-%d %H:%M:%S} UTC  {text}\n")
        except OSError:
            log.error("no se pudo escribir el archivo de alertas")
        if token and chat:
            try:
                send(token, chat, f"TRADEBOT: {text}")
            except Exception as e:  # noqa: BLE001 - un aviso fallido no debe afectar al bot
                log.error(
                    "no se pudo enviar el aviso a Telegram (%s)", type(e).__name__
                )  # sin URL: lleva el token

    return notify
