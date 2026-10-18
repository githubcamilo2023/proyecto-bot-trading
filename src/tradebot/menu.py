"""Menú guiado en español: el mismo bot, sin tener que memorizar comandos.

Cada opción llama al mismo código que los comandos (`tradebot status`, `propose`, `approve`...), así que
las reglas de seguridad son idénticas. Las claves se piden sin mostrarse y solo viven en esta ejecución:
nunca se escriben en ningún archivo.
"""

import getpass
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from tradebot.integrity import KEY_ENV, MIN_KEY_LEN
from tradebot.strategy import StrategyStore
from tradebot.strategy.store import ApprovalError

ANTHROPIC_ENV = "ANTHROPIC_API_KEY"
AUDIT = Path("data/audit.jsonl")
STRATEGIES = Path("strategies")

GLOSARIO = """\
GLOSARIO RÁPIDO
  Paper trading   Operar con dinero SIMULADO usando precios reales. No hay riesgo real.
  Estrategia      Reglas escritas: cuándo comprar, cuándo vender, cuánto usar y dónde cortar la pérdida.
  Versión         Cada cambio es una versión nueva (v1, v2...). Una versión aprobada nunca se modifica.
  Aprobación      Tu firma de que leíste la regla y autorizas que el bot la use. Sin ella, no opera.
  Backtest        Simula cómo le habría ido a la estrategia en el pasado. No garantiza el futuro.
  Vela            Resumen de un periodo (1 hora, 4 horas...): apertura, máximo, mínimo y cierre.
  RSI             Indicador de 0 a 100. Bajo 30 suele llamarse "sobrevendido"; sobre 70, "sobrecomprado".
  Media móvil     Promedio del precio de las últimas N velas (SMA simple, EMA da más peso a lo reciente).
  Stop-loss       Si el precio cae ese % desde donde compraste, el bot vende solo para limitar la pérdida.
  Posición        Lo que tienes comprado en este momento.
  P&L             Ganancia o pérdida. "Latente" = si vendieras ahora; "realizada" = ya vendida.
  Capital         Tu dinero simulado total (efectivo + valor de la posición).
  Kill switch     Freno de emergencia: el bot deja de COMPRAR. No vende lo que ya tienes.
  Auditoría       Diario inalterable de todo lo que pasó (quién aprobó, qué se compró, cuándo).
  Clave de        Contraseña que firma tus aprobaciones y la auditoría. Si la pierdes, hay que
  integridad      aprobar de nuevo. Guárdala en un gestor de contraseñas.
Nada de esto es asesoría financiera.
"""

MENU = """
════════════════════════════════════════════════════════════
 TRADEBOT · modo PAPER (dinero simulado, sin riesgo real)
════════════════════════════════════════════════════════════
  1) Ver cómo está todo ahora
  2) Panel en vivo (precio, posición, señales)
  3) Crear una estrategia escribiendo una frase
  4) Mis estrategias (ver, probar en el pasado, aprobar)
  5) Iniciar el bot
  6) FRENO DE EMERGENCIA (dejar de comprar)
  7) Quitar el freno
  8) Ayuda y glosario
  0) Salir
"""

Out = Callable[[str], object]
Ask = Callable[[str], str]
Run = Callable[[list[str]], int]


class Menu:
    def __init__(
        self,
        out: Out = print,
        ask: Ask = input,
        secret: Ask = getpass.getpass,
        run: Run | None = None,
        launch: Callable[[list[str]], None] | None = None,
        strategies: Path = STRATEGIES,
        audit: Path = AUDIT,
    ) -> None:
        from tradebot.cli import main  # import diferido: cli importa este módulo solo cuando hace falta

        self.out, self.ask, self.secret = out, ask, secret
        self._run = run or (lambda argv: main(argv, out=self.out))
        self.launch = launch or self._launch
        self.strategies, self.audit = strategies, audit

    # --- utilidades ------------------------------------------------------------------------

    def _yes(self, question: str, default: bool = False) -> bool:
        hint = "S/n" if default else "s/N"
        reply = self.ask(f"{question} ({hint}): ").strip().lower()
        return default if not reply else reply in {"s", "si", "sí", "y", "yes"}

    def _launch(self, argv: list[str]) -> None:
        """Abre el comando en una ventana nueva (Windows) para que el bot siga corriendo aparte."""
        cmd = [sys.executable, "-m", "tradebot", *argv]
        if os.name == "nt":
            new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)  # solo existe en Windows
            subprocess.Popen(cmd, creationflags=new_console)  # noqa: S603
        else:
            self._run(argv)  # fuera de Windows no hay "ventana nueva" portable: corre aquí

    def _list(self) -> list[tuple[str, int]]:
        if not self.strategies.is_dir():
            return []
        store = StrategyStore(self.strategies)
        found = []
        for d in sorted(p for p in self.strategies.iterdir() if p.is_dir()):
            try:
                versions = store.versions(d.name)
            except ValueError:
                continue
            if versions:
                found.append((d.name, versions[-1]))
        return found

    def _status_of(self, name: str, version: int) -> str:
        key = os.environ.get(KEY_ENV, "")
        if len(key) < MIN_KEY_LEN:
            return "?"
        try:
            return (
                "aprobada"
                if StrategyStore(self.strategies, key=key.encode()).is_approved(name, version)
                else "pendiente"
            )
        except (OSError, ValueError):
            return "?"

    def _approved_version(self, name: str) -> int | None:
        key = os.environ.get(KEY_ENV, "")
        if len(key) < MIN_KEY_LEN:
            return None
        try:
            return StrategyStore(self.strategies, key=key.encode()).load_approved(name).version
        except (OSError, ValueError, ApprovalError):
            return None

    def _pick(self, only_approved: bool = False) -> tuple[str, int] | None:
        if only_approved:
            items = [(n, av) for n, _ in self._list() if (av := self._approved_version(n)) is not None]
        else:
            items = self._list()
        if not items:
            self.out(
                "No hay estrategias aprobadas todavía. Crea una con la opción 3 y apruébala."
                if only_approved
                else "Todavía no tienes estrategias. Crea una con la opción 3."
            )
            return None
        for i, (n, v) in enumerate(items, 1):
            self.out(f"  {i}) {n} v{v} · {self._status_of(n, v)}")
        reply = self.ask("Elige un número (Enter para volver): ").strip()
        if not reply:
            return None
        if reply.isdigit() and 1 <= int(reply) <= len(items):
            return items[int(reply) - 1]
        self.out("Ese número no está en la lista.")
        return None

    # --- claves ----------------------------------------------------------------------------

    def ensure_key(self) -> bool:
        if len(os.environ.get(KEY_ENV, "")) >= MIN_KEY_LEN:
            return True
        self.out(
            "\nNecesito tu CLAVE DE INTEGRIDAD (TRADEBOT_KEY). Firma tus aprobaciones y la auditoría.\n"
            "Pégala aquí (no se verá mientras la escribes y NO se guarda en ningún archivo).\n"
            "Si no tienes una o la perdiste, escribe NUEVA para crear otra."
        )
        raw = self.secret("Clave (o NUEVA): ").strip()
        if raw.upper() == "NUEVA":
            return self._new_key()
        if len(raw) < MIN_KEY_LEN:
            self.out(f"Esa clave es muy corta (mínimo {MIN_KEY_LEN} caracteres). Inténtalo de nuevo.")
            return False
        os.environ[KEY_ENV] = raw
        return True

    def _new_key(self) -> bool:
        self.out(
            "\nUna clave NUEVA invalida las aprobaciones anteriores (habrá que aprobar de nuevo)\n"
            "y el diario de auditoría anterior ya no se podrá verificar con ella."
        )
        if not self._yes("¿Continuar?", default=False):
            return False
        if self.audit.exists():
            if self._yes(
                "¿Archivo la auditoría anterior para empezar una limpia? (recomendado)", default=True
            ):
                target = self.audit.with_name(f"audit-{date.today():%Y-%m-%d}-clave-anterior.jsonl")
                n = 1
                while target.exists():
                    n += 1
                    target = target.with_name(f"audit-{date.today():%Y-%m-%d}-clave-anterior-{n}.jsonl")
                self.audit.rename(target)
                self.out(f"Auditoría anterior archivada como {target.name}.")
            else:
                self.out(
                    "Sin archivarla, el bot no podrá escribir en la auditoría vieja.\n"
                    "Archívala antes de seguir."
                )
                return False
        key = secrets.token_urlsafe(48)
        self.out(
            "\nTU CLAVE NUEVA (se muestra UNA sola vez; cópiala a tu gestor de contraseñas AHORA):\n\n"
            f"    {key}\n"
        )
        self.secret("Cuando la hayas guardado, pulsa Enter para continuar...")
        os.environ[KEY_ENV] = key
        self.out("\u001b[2J\u001b[H" if self._is_tty() else "(clave activa solo para esta sesión)")
        return True

    @staticmethod
    def _is_tty() -> bool:
        return bool(getattr(sys.stdout, "isatty", lambda: False)())

    def _ensure_anthropic(self) -> bool:
        if os.environ.get(ANTHROPIC_ENV):
            return True
        self.out(
            "\nPara convertir una frase en estrategia uso un modelo de IA y necesito tu clave de Anthropic\n"
            "(ANTHROPIC_API_KEY, de console.anthropic.com). No se verá al escribirla ni se guarda."
        )
        raw = self.secret("Clave de Anthropic (Enter para cancelar): ").strip()
        if not raw:
            return False
        os.environ[ANTHROPIC_ENV] = raw
        return True

    # --- acciones --------------------------------------------------------------------------

    def create(self) -> None:
        self.out(
            "\nEscribe tu idea con tus palabras. Incluye el par (por ejemplo BTC/USDT), cuándo comprar,\n"
            "cuándo vender, el tamaño de las velas (1h, 4h...) y qué % del capital usar.\n"
            "Ejemplo: compra BTC/USDT cuando el RSI de 14 baje de 30 y vende cuando suba de 55,\n"
            "velas de 4h, usa 2% del capital"
        )
        phrase = self.ask("\nTu estrategia: ").strip()
        if not phrase or not self._ensure_anthropic():
            return
        code, text = self._run_capture(["propose", phrase])
        found = re.search(r"Guardada como (\S+) v(\d+)", text)
        if code == 0 and found:
            self._after_save(found.group(1), found.group(2))

    def _run_capture(self, argv: list[str]) -> tuple[int, str]:
        """Ejecuta mostrando la salida y guardando copia (para saber qué versión se creó)."""
        lines: list[str] = []
        saved_out = self.out

        def tee(msg: object) -> None:
            lines.append(str(msg))
            saved_out(str(msg))

        self.out = tee
        try:
            code = self._run(argv)
        finally:
            self.out = saved_out
        return code, "\n".join(lines)

    def _after_save(self, name: str, version: str | int) -> None:
        self.out("\nLéela con calma: ¿hace exactamente lo que querías?")
        if self._yes("¿Pruebo cómo le habría ido el último año? (tarda unos segundos)", default=True):
            self._run(["backtest", name, "--days", "365"])
        self.approve(name, version)

    def approve(self, name: str, version: str | int) -> None:
        if len(os.environ.get(KEY_ENV, "")) < MIN_KEY_LEN:
            self.out("Para aprobar necesito la clave de integridad.")
            return
        self.out(
            f"\nAprobar «{name}» v{version} significa que autorizas al bot a usar esa regla (en paper).\n"
            "Es tu único control antes de que opere.\n"
            "Escribe APROBAR para confirmar; otra cosa la deja pendiente."
        )
        if self.ask("> ").strip().upper() == "APROBAR":
            self._run(["approve", name, str(version)])
        else:
            self.out("Queda pendiente. Puedes aprobarla después desde «Mis estrategias».")

    def strategies_menu(self) -> None:
        picked = self._pick()
        if picked is None:
            return
        name, version = picked
        self.out(
            f"\n«{name}» v{version}\n  a) Ver qué hace\n  b) Probar en el pasado (backtest)\n"
            "  c) Aprobarla\n  d) Editarla (crea una versión nueva)\n  Enter) Volver"
        )
        choice = self.ask("> ").strip().lower()
        if choice == "a":
            self._run(["show", name])
        elif choice == "b":
            self._run(["backtest", name, "--days", "365"])
        elif choice == "c":
            self.approve(name, version)
        elif choice == "d":
            self.edit_strategy(name, version)

    def edit_strategy(self, name: str, version: int) -> None:
        try:
            s = StrategyStore(self.strategies).load(name, version)
        except (OSError, ValueError) as e:
            self.out(f"No pude leer la estrategia: {e}")
            return
        self.out(
            f"\nEditar «{name}» v{version}. Se crea una versión NUEVA; la actual no se toca.\n"
            f"  1) Tamaño por operación (ahora {s.size_pct * 100:.2f}% del capital)\n"
            f"  2) Stop-loss (ahora {s.stop_loss_pct * 100:.2f}%)\n"
            "  3) Reglas de compra/venta, con una frase\n  Enter) Volver"
        )
        choice = self.ask("> ").strip()
        base = ["edit", name, "--version", str(version)]
        if choice in {"1", "2"}:
            value = self.ask("Nuevo valor en % (por ejemplo 2 o 2.5): ").strip()
            if not value:
                return
            argv = [*base, "--size" if choice == "1" else "--stop-loss", value]
        elif choice == "3":
            self.out("Describe el cambio, por ejemplo: sube el umbral de compra del RSI a 35")
            phrase = self.ask("Cambio: ").strip()
            if not phrase or not self._ensure_anthropic():
                return
            argv = [*base, "--phrase", phrase]
        else:
            return
        code, text = self._run_capture(argv)
        found = re.search(r"Guardada como (\S+) v(\d+)", text)
        if code == 0 and found:
            self._after_save(found.group(1), found.group(2))

    def start_bot(self) -> None:
        picked = self._pick(only_approved=True)
        if picked is None:
            return
        name, version = picked
        self.out("")
        if self._run(["review", name]) != 0:
            return
        self.out(
            "\n¿Es esta la estrategia que quieres aplicar?\n"
            "  s) Sí, iniciar el bot en modo PAPER (revisa una vez por cada vela cerrada; con velas de\n"
            "     1 hora puede pasar mucho tiempo sin operar, y es normal)\n"
            "  e) Editarla primero\n  Enter) Cancelar"
        )
        choice = self.ask("> ").strip().lower()
        if choice == "e":
            self.edit_strategy(name, version)
            return
        if choice != "s":
            return
        self.launch(["paper", name])
        self.out("Bot iniciado (revisa la otra ventana). Para detenerlo: Ctrl+C en esa ventana.")
        if self._yes("¿Abro también el panel en vivo?", default=True):
            self.launch(["watch", name])

    def live_panel(self) -> None:
        picked = self._pick()
        if picked is not None:
            self.out("Panel en vivo: Ctrl+C para volver al menú.")
            self._run(["watch", picked[0]])

    def emergency(self) -> None:
        self.out("El freno bloquea compras NUEVAS. No vende lo que ya tengas.")
        if self._yes("¿Activar el freno de emergencia?", default=False):
            self._run(["kill", "--reason", "menú"])

    def release(self) -> None:
        if self._yes("¿Quitar el freno? Hazlo solo si sabes por qué se activó", default=False):
            self._run(["release"])

    # --- bucle -----------------------------------------------------------------------------

    def loop(self) -> int:
        while not self.ensure_key():
            if not self._yes("¿Intentar de nuevo?", default=True):
                return 2
        actions: dict[str, Callable[[], object]] = {
            "1": lambda: self._run(["status"]),
            "2": self.live_panel,
            "3": self.create,
            "4": self.strategies_menu,
            "5": self.start_bot,
            "6": self.emergency,
            "7": self.release,
            "8": lambda: self.out(GLOSARIO),
        }
        while True:
            self.out(MENU)
            try:
                choice = self.ask("Elige una opción: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = "0"
            if choice == "0":
                self.out("Hasta luego. Si dejaste el bot corriendo, sigue en su ventana.")
                return 0
            action = actions.get(choice)
            if action is None:
                self.out("No entendí esa opción. Escribe un número del menú.")
                continue
            try:
                action()
            except KeyboardInterrupt:
                self.out("\nVolviendo al menú.")


def run_menu(out: Out = print) -> int:
    return Menu(out=out).loop()
