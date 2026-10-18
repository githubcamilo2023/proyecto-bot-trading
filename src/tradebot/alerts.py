"""Avisos del bot: siempre a un archivo local y, si lo configuras, también por correo y/o Telegram.

Credenciales SOLO en variables de la consola, nunca en archivos:
- Correo: TRADEBOT_SMTP_USER (cuenta que envía), TRADEBOT_SMTP_PASSWORD (contraseña de aplicación) y
  TRADEBOT_ALERT_EMAIL (destino; por defecto, la misma cuenta). Servidor: TRADEBOT_SMTP_HOST (smtp.gmail.com)
  y TRADEBOT_SMTP_PORT (465 = TLS directo; 587 = STARTTLS, útil si tu red bloquea el 465).
- Telegram: TRADEBOT_TELEGRAM_TOKEN y TRADEBOT_TELEGRAM_CHAT.
Un aviso que falla jamás detiene al bot ni escribe credenciales en los logs (solo el tipo de error).
"""

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("tradebot.alerts")
Notify = Callable[[str], object]  # devuelve la lista de canales que fallaron (con pista); se puede ignorar
HINTS = {
    "SMTPAuthenticationError": "usuario o contraseña de aplicación incorrectos (¿2 pasos activado?)",
    "SMTPServerDisconnected": "la conexión se cortó: tu red o antivirus puede bloquear el puerto; prueba "
    '$env:TRADEBOT_SMTP_PORT = "587"',
    "TimeoutError": 'sin respuesta del servidor: revisa internet o prueba $env:TRADEBOT_SMTP_PORT = "587"',
    "ConnectionRefusedError": 'conexión rechazada: prueba $env:TRADEBOT_SMTP_PORT = "587"',
    "SSLCertVerificationError": "certificado no verificable: un antivirus/proxy intercepta TLS; desactiva su "
    "análisis de correo para esta prueba",
}
TOKEN_ENV, CHAT_ENV = "TRADEBOT_TELEGRAM_TOKEN", "TRADEBOT_TELEGRAM_CHAT"
SMTP_USER_ENV, SMTP_PASSWORD_ENV = "TRADEBOT_SMTP_USER", "TRADEBOT_SMTP_PASSWORD"
EMAIL_TO_ENV, SMTP_HOST_ENV, SMTP_PORT_ENV = (
    "TRADEBOT_ALERT_EMAIL",
    "TRADEBOT_SMTP_HOST",
    "TRADEBOT_SMTP_PORT",
)
SMTP_PORT = 465  # SMTP sobre TLS directo
GMAIL = "smtp.gmail.com"
APP_PASSWORD_LEN = 16
TIMEOUT = 10
MAX_TEXT = 1000


def _telegram(token: str, chat: str, text: str) -> None:
    req = urllib.request.Request(  # noqa: S310 - URL https fija
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=TIMEOUT).close()  # noqa: S310


def _email(host: str, port: int, user: str, password: str, to: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"TRADEBOT: {text[:70]}"
    msg["From"], msg["To"] = user, to
    msg.set_content(f"{text}\n\n(Aviso automático de tu bot en modo paper. No es asesoría financiera.)")
    ctx = ssl.create_default_context()
    if port == SMTP_PORT:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx) as s:
            s.login(user, password)
            s.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=TIMEOUT) as s:  # 587: se exige cifrar ANTES de enviar la contraseña
        s.starttls(context=ctx)
        s.login(user, password)
        s.send_message(msg)


def smtp_port(env: Mapping[str, str]) -> int:
    raw = env.get(SMTP_PORT_ENV) or str(SMTP_PORT)
    return int(raw) if raw.isdigit() else SMTP_PORT


def config_problems(env: Mapping[str, str] | None = None) -> list[str]:
    """Errores típicos de configuración del correo, sin conectarse. Nunca muestra la contraseña."""
    env = os.environ if env is None else env
    user, password = env.get(SMTP_USER_ENV, "").strip(), env.get(SMTP_PASSWORD_ENV, "")
    if not (user and password) or (env.get(SMTP_HOST_ENV) or GMAIL) != GMAIL:
        return []
    out = []
    n = len(password.replace(" ", ""))
    if n != APP_PASSWORD_LEN:
        out.append(
            f"la contraseña tiene {n} caracteres; una contraseña de APLICACIÓN de Gmail tiene "
            f"{APP_PASSWORD_LEN} letras. Créala en https://myaccount.google.com/apppasswords y pégala"
            " entre comillas SIMPLES en PowerShell (con comillas dobles, un $ la recorta)"
        )
    if "@" not in user:
        out.append("TRADEBOT_SMTP_USER debe ser la dirección completa (algo@gmail.com)")
    return out


def channels(env: Mapping[str, str] | None = None) -> list[str]:
    """Canales externos configurados (para decírselo al usuario). Nunca devuelve credenciales."""
    env = os.environ if env is None else env
    out = []
    if env.get(SMTP_USER_ENV) and env.get(SMTP_PASSWORD_ENV):
        out.append(f"correo a {env.get(EMAIL_TO_ENV) or env[SMTP_USER_ENV]}")
    if env.get(TOKEN_ENV) and env.get(CHAT_ENV):
        out.append("Telegram")
    return out


def make_notifier(
    log_path: str | Path,
    env: Mapping[str, str] | None = None,
    send: Callable[[str, str, str], None] = _telegram,
    mail: Callable[[str, int, str, str, str, str], None] = _email,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Notify:
    env = os.environ if env is None else env
    token, chat = env.get(TOKEN_ENV, ""), env.get(CHAT_ENV, "")
    user, password = env.get(SMTP_USER_ENV, "").strip(), env.get(SMTP_PASSWORD_ENV, "")
    host = env.get(SMTP_HOST_ENV) or GMAIL
    if host == GMAIL:
        password = "".join(password.split())  # Google la muestra en 4 grupos con espacios; no los lleva
    to = env.get(EMAIL_TO_ENV) or user
    port = smtp_port(env)
    path = Path(log_path)

    def attempt(name: str, fn: Callable[[], None], failed: list[str]) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - un aviso fallido no debe afectar al bot
            kind = type(e).__name__
            log.error("no se pudo enviar el aviso por %s (%s)", name, kind)  # sin detalle: credenciales
            hint = HINTS.get(kind, "revisa las variables y la conexión")
            if name == "correo" and port != SMTP_PORT and "587" in hint:
                hint = "la conexión se cortó al iniciar sesión: revisa usuario y contraseña de aplicación"
            failed.append(f"{name}: {kind} → {hint}")

    def notify(text: str) -> list[str]:
        text = " ".join(text.split())[:MAX_TEXT]  # una línea: sin saltos que permitan inyectar cabeceras
        failed: list[str] = []
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{now():%Y-%m-%d %H:%M:%S} UTC  {text}\n")
        except OSError:
            log.error("no se pudo escribir el archivo de alertas")
            failed.append("archivo de avisos: no se pudo escribir")
        if user and password:
            attempt("correo", lambda: mail(host, port, user, password, to, text), failed)
        if token and chat:
            attempt("Telegram", lambda: send(token, chat, f"TRADEBOT: {text}"), failed)
        return failed

    return notify
