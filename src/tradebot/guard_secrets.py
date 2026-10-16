"""Guardia anti-secretos para pre-commit: bloquea archivos con pinta de credenciales.

Uso: python -m tradebot.guard_secrets <archivos...>   (devuelve 1 si encuentra algo)
Es una red de seguridad barata, no un escáner completo.
"""

import re
import sys
from pathlib import Path

_PATTERNS = {
    "clave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "clave de AWS": re.compile(r"AKIA[0-9A-Z]{16}"),
    "token de Anthropic/OpenAI": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "asignación de secreto": re.compile(
        r"""(?ix)\b(api[_-]?key|api[_-]?secret|secret|password|passwd|token|tradebot_key)\b
        \s*[:=]\s*["']?(?!changeme|example|your|xxx|<|\$|\{)[A-Za-z0-9/+_=.-]{16,}"""
    ),
}
_FORBIDDEN_NAMES = (".env",)  # .env.example sí se permite


def scan_text(text: str) -> list[str]:
    return [name for name, rx in _PATTERNS.items() if rx.search(text)]


def scan_file(path: Path) -> list[str]:
    if path.name in _FORBIDDEN_NAMES:
        return ["archivo .env no debe versionarse"]
    try:
        return scan_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []  # binarios o ilegibles: no son texto con secretos


def main(argv: list[str] | None = None) -> int:
    bad = False
    for name in sys.argv[1:] if argv is None else argv:
        for why in scan_file(Path(name)):
            print(f"{name}: posible secreto ({why})", file=sys.stderr)
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
