import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradebot.audit import AuditError, AuditLog
from tradebot.integrity import MissingKeyError, sha256_hex, sign, verify
from tradebot.strategy.rules import NAME_PATTERN, Strategy

log = logging.getLogger("tradebot.strategy")
_DOMAIN = "tradebot/approval/v1"
_FIELDS = ("name", "version", "content_sha256", "by", "at")


class ApprovalError(Exception):
    """La versión no tiene una aprobación válida (no existe, está alterada o no coincide).
    `tamper=True` indica una señal de manipulación (no un simple "sin aprobar")."""

    def __init__(self, message: str, tamper: bool = False) -> None:
        super().__init__(message)
        self.tamper = tamper


class StrategyStore:
    """Versiones en disco: <root>/<nombre>/v<N>.json (nunca se sobrescribe una versión).

    La aprobación es un registro firmado con HMAC que incluye el hash SHA-256 del contenido exacto:
    si el archivo de la estrategia se edita después de aprobarla, la aprobación deja de ser válida.
    Leer y guardar no necesita clave; aprobar y verificar aprobaciones sí.
    """

    def __init__(
        self, root: str | Path = "strategies", key: bytes | None = None, audit: AuditLog | None = None
    ) -> None:
        self.root, self._key, self._audit = Path(root), key, audit

    def _record(self, actor: str, action: str, subject: str, result: str, **details: Any) -> None:
        if self._audit is not None:
            self._audit.append(actor, action, subject, result, details)

    def _need_key(self) -> bytes:
        if self._key is None:
            raise MissingKeyError("esta operación necesita la clave de integridad")
        return self._key

    def _dir(self, name: str) -> Path:
        if not re.fullmatch(NAME_PATTERN, name):  # evita rutas raras (../) venga el nombre de donde venga
            raise ValueError(f"nombre de estrategia inválido: {name!r}")
        return self.root / name

    def versions(self, name: str) -> list[int]:
        d = self._dir(name)
        stems = (
            (p.stem[1:] for p in d.glob("v*.json") if not p.name.endswith(".approved.json"))
            if d.is_dir()
            else ()
        )
        return sorted(int(s) for s in stems if s.isdigit())

    def save(self, s: Strategy) -> Strategy:
        """Guarda como NUEVA versión (la última + 1) y devuelve la estrategia con ese número."""
        d = self._dir(s.name)
        d.mkdir(parents=True, exist_ok=True)
        new = s.model_copy(update={"version": max(self.versions(s.name), default=0) + 1})
        path = d / f"v{new.version}.json"
        # newline="\n": mismos bytes en Windows y Linux, o el hash de la aprobación cambiaría entre sistemas
        with path.open("x", encoding="utf-8", newline="\n") as f:  # "x": falla si ya existe
            f.write(new.model_dump_json(indent=2))
        try:
            self._record(
                "system",
                "strategy.save",
                f"{new.name} v{new.version}",
                "ok",
                sha256=sha256_hex(path.read_bytes()),
            )
        except AuditError:
            path.unlink()  # sin rastro de auditoría no queda versión nueva
            raise
        return new

    def _path(self, name: str, version: int) -> Path:
        return self._dir(name) / f"v{version}.json"

    def load(self, name: str, version: int | None = None) -> Strategy:
        v = version or max(self.versions(name), default=0)
        path = self._path(name, v)
        if not v or not path.exists():
            raise FileNotFoundError(f"no existe la estrategia {name!r} (versión {version or 'última'})")
        return Strategy.model_validate_json(path.read_text(encoding="utf-8"))

    # --- aprobación -------------------------------------------------------------------------

    def _approval_path(self, name: str, version: int) -> Path:
        return self._dir(name) / f"v{version}.approved.json"

    def _content_hash(self, name: str, version: int) -> str:
        path = self._path(name, version)
        if not path.exists():
            raise FileNotFoundError(f"no existe la estrategia {name!r} (versión {version})")
        return sha256_hex(path.read_bytes())

    def check_approval(self, name: str, version: int) -> dict[str, Any]:
        """Devuelve el registro si la aprobación es válida; si no, lanza ApprovalError con el motivo."""
        key = self._need_key()
        path = self._approval_path(name, version)
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ApprovalError("sin aprobar") from None
        except (OSError, ValueError):
            raise ApprovalError("registro de aprobación ilegible", tamper=True) from None
        if not isinstance(rec, dict) or set(rec) != {*_FIELDS, "mac"}:
            raise ApprovalError("registro de aprobación con formato inválido", tamper=True)
        body = {k: rec[k] for k in _FIELDS}
        if not isinstance(rec["mac"], str) or not verify(key, _DOMAIN, body, rec["mac"]):
            raise ApprovalError("firma de aprobación inválida", tamper=True)
        if rec["name"] != name or rec["version"] != version:
            raise ApprovalError("la aprobación pertenece a otra versión", tamper=True)
        if rec["content_sha256"] != self._content_hash(name, version):
            raise ApprovalError("la estrategia cambió después de ser aprobada", tamper=True)
        return rec

    def approve(self, name: str, version: int, user: str) -> None:
        """Firma la aprobación del contenido actual. Idempotente: si ya hay una válida, se conserva."""
        key = self._need_key()
        digest = self._content_hash(name, version)  # falla si la versión no existe
        try:
            self.check_approval(name, version)
            return
        except ApprovalError:
            pass  # no hay aprobación válida: se crea (o se reemplaza una inválida)
        body: dict[str, Any] = {
            "name": name,
            "version": version,
            "content_sha256": digest,
            "by": user,
            "at": datetime.now(UTC).isoformat(),
        }
        record = {**body, "mac": sign(key, _DOMAIN, body)}
        path = self._approval_path(name, version)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, path)  # atómico: nunca queda un registro a medias
        try:
            self._record(user, "strategy.approve", f"{name} v{version}", "ok", sha256=digest)
        except AuditError:
            path.unlink()  # aprobación sin rastro de auditoría no vale
            raise
        log.info("estrategia %s v%s aprobada por %s", name, version, user)

    def is_approved(self, name: str, version: int) -> bool:
        try:
            self.check_approval(name, version)
        except ApprovalError:
            return False
        return True

    def load_approved(self, name: str) -> Strategy:
        """La versión MÁS RECIENTE con aprobación válida. Es la única vía por la que el bot ejecuta.
        Si la última versión no está aprobada, usa la anterior aprobada y lo avisa en el log."""
        versions = self.versions(name)
        for v in reversed(versions):
            try:
                self.check_approval(name, v)
            except ApprovalError as e:
                log.warning("estrategia %s v%s no ejecutable: %s", name, v, e)
                if e.tamper:
                    try:
                        self._record(
                            "system", "strategy.tamper_suspected", f"{name} v{v}", "denied", reason=str(e)
                        )
                    except AuditError:
                        log.exception("no se pudo auditar la sospecha de manipulación")
                continue
            if v != versions[-1]:
                log.warning("se usa %s v%s (aprobada); hay versiones más nuevas sin aprobar", name, v)
            return self.load(name, v)
        raise ApprovalError(f"{name!r} no tiene ninguna versión aprobada")
