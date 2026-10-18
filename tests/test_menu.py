import subprocess

import pytest

from tradebot import cli
from tradebot.integrity import KEY_ENV
from tradebot.menu import ANTHROPIC_ENV, GLOSARIO, Menu
from tradebot.strategy import Strategy, StrategyStore

KEY = "k" * 40
SPEC = {
    "name": "siempre",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "size_pct": "0.02",
    "entry": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 0},
    "exit": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 100000},
}


class Script:
    """Respuestas del usuario en orden; falla si el menú pregunta de más."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)

    def __call__(self, _prompt: str = "") -> str:
        assert self.answers, "el menú pidió más respuestas de las previstas"
        return self.answers.pop(0)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv(ANTHROPIC_ENV, raising=False)
    return tmp_path


def build(tmp_path, answers=(), secrets_=(), propose_ok=True):
    out: list[str] = []
    calls: list[list[str]] = []
    launched: list[list[str]] = []
    holder: dict[str, Menu] = {}

    def run(argv: list[str]) -> int:
        calls.append(argv)
        if argv[0] == "propose":
            if not propose_ok:
                holder["m"].out("No se pudo generar la estrategia: x")
                return 2
            holder["m"].out("Guardada como nueva-estrategia v1, SIN APROBAR.")
        if argv[0] == "import":
            holder["m"].out(f"Guardada como {argv[1]} v1, SIN APROBAR (importada de la plantilla {argv[1]}).")
        if argv[0] == "report":
            holder["m"].out("Informe con gráfico: C:/x/siempre-v1.html  (ábrelo con doble clic)")
        if argv[0] == "edit":
            holder["m"].out("Guardada como siempre v2, SIN APROBAR (la v1 sigue igual).")
        return 0

    m = Menu(
        out=out.append,
        ask=Script(*answers),
        secret=Script(*secrets_),
        run=run,
        launch=launched.append,
        strategies=tmp_path / "strategies",
        audit=tmp_path / "audit.jsonl",
    )
    holder["m"] = m
    return m, out, calls, launched


def saved_strategy(tmp_path, approved: bool):
    store = StrategyStore(tmp_path / "strategies", key=KEY.encode())
    store.save(Strategy.model_validate(SPEC))
    if approved:
        store.approve("siempre", 1, "camilo")


# --- claves ---------------------------------------------------------------------------------------


def test_existing_key_is_not_asked_again(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    m, out, *_ = build(env)
    assert m.ensure_key() and out == []


def test_pasted_key_is_kept_only_in_the_environment(env):
    m, _, *_ = build(env, secrets_=[KEY])
    import os

    assert m.ensure_key() and os.environ[KEY_ENV] == KEY
    assert not list(env.iterdir())  # nada se escribe en disco


def test_short_key_is_rejected(env):
    m, out, *_ = build(env, secrets_=["corta"])
    assert m.ensure_key() is False and any("muy corta" in o for o in out)


def test_new_key_archives_the_old_audit_and_shows_the_key_once(env):
    import os

    (env / "audit.jsonl").write_text("viejo")
    m, out, *_ = build(env, answers=["s", "s"], secrets_=["NUEVA", ""])
    assert m.ensure_key()
    key = os.environ[KEY_ENV]
    assert len(key) >= 40 and any(key in o for o in out)
    archived = [p.name for p in env.iterdir()]
    assert "audit.jsonl" not in archived and any(
        n.startswith("audit-") and "clave-anterior" in n for n in archived
    )


def test_new_key_archive_never_overwrites_an_existing_archive(env):
    from datetime import date

    (env / "audit.jsonl").write_text("viejo")
    first = env / f"audit-{date.today():%Y-%m-%d}-clave-anterior.jsonl"
    first.write_text("archivo previo")
    m, *_ = build(env, answers=["s", "s"], secrets_=["NUEVA", ""])
    assert m.ensure_key()
    assert first.read_text() == "archivo previo" and (env / first.name.replace(".jsonl", "-2.jsonl")).exists()


def test_new_key_can_be_cancelled_and_does_not_set_anything(env):
    import os

    m, *_ = build(env, answers=["n"], secrets_=["NUEVA"])
    assert m.ensure_key() is False and KEY_ENV not in os.environ


def test_new_key_refuses_to_continue_without_archiving_the_old_audit(env):
    import os

    (env / "audit.jsonl").write_text("viejo")
    m, out, *_ = build(env, answers=["s", "n"], secrets_=["NUEVA"])
    assert m.ensure_key() is False and KEY_ENV not in os.environ
    assert (env / "audit.jsonl").exists() and any("Archívala" in o for o in out)


# --- bucle ----------------------------------------------------------------------------------------


def test_loop_basic_navigation(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    m, out, calls, _ = build(env, answers=["x", "1", "8", "0"])
    assert m.loop() == 0
    text = "\n".join(out)
    assert "No entendí esa opción" in text and GLOSARIO in text and calls == [["status"]]
    assert "Hasta luego" in text and "dinero simulado" in text


def test_loop_exits_cleanly_on_end_of_input(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)

    def eof(_p: str = "") -> str:
        raise EOFError

    m, *_ = build(env)
    m.ask = eof
    assert m.loop() == 0


def test_loop_gives_up_when_there_is_no_key(env):
    m, *_ = build(env, answers=["n"], secrets_=["corta"])
    assert m.loop() == 2


def test_loop_survives_ctrl_c_inside_an_action(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    m, out, *_ = build(env, answers=["1", "0"])

    def interrupted(_argv):
        raise KeyboardInterrupt

    m._run = interrupted
    assert m.loop() == 0 and any("Volviendo al menú" in o for o in out)


# --- crear una estrategia ---------------------------------------------------------------------------


def test_create_flow_runs_propose_backtest_and_approval(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    m, _, calls, _ = build(env, answers=["2", "compra algo", "s", "aprobar"], secrets_=["sk-ant-prueba"])
    m.create()
    assert [c[0] for c in calls] == ["propose", "evaluate", "approve"]
    assert calls[2] == ["approve", "nueva-estrategia", "1"]
    import os

    assert os.environ[ANTHROPIC_ENV] == "sk-ant-prueba"


def test_create_without_typing_aprobar_leaves_it_pending(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    m, out, calls, _ = build(env, answers=["2", "compra algo", "n", "no sé"], secrets_=["sk-ant-prueba"])
    m.create()
    assert [c[0] for c in calls] == ["propose"] and any("Queda pendiente" in o for o in out)


def test_create_stops_if_the_model_fails_or_nothing_is_typed(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv(ANTHROPIC_ENV, "x")
    m, _, calls, _ = build(env, answers=["2", "compra algo"], propose_ok=False)
    m.create()
    assert [c[0] for c in calls] == ["propose"]
    m2, _, calls2, _ = build(env, answers=["2", ""])
    m2.create()
    assert calls2 == []


def test_create_without_an_anthropic_key_cancels(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    m, _, calls, _ = build(env, answers=["2", "compra algo"], secrets_=[""])
    m.create()
    assert calls == []


def test_approve_needs_the_key(env):
    m, out, calls, _ = build(env)
    m.approve("x", 1)
    assert calls == [] and any("clave de integridad" in o for o in out)


# --- estrategias ----------------------------------------------------------------------------------


def test_my_strategies_lists_status_and_offers_actions(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=False)
    m, out, calls, _ = build(env, answers=["1", "a", "1", "b", "1", "c", "APROBAR", "1", "", "", "9", ""])
    m.strategies_menu()
    m.strategies_menu()
    m.strategies_menu()
    m.strategies_menu()  # Enter para volver al submenú
    m.strategies_menu()  # número fuera de lista
    m.strategies_menu()  # Enter en la lista
    assert calls == [
        ["show", "siempre"],
        ["backtest", "siempre", "--days", "365"],
        ["approve", "siempre", "1"],
    ]
    assert any("siempre v1 · pendiente" in o for o in out) and any("no está en la lista" in o for o in out)


def test_no_strategies_yet(env):
    m, out, calls, _ = build(env)
    m.strategies_menu()
    assert calls == [] and any("Todavía no tienes estrategias" in o for o in out)


def test_status_is_unknown_without_a_key(env):
    saved_strategy(env, approved=True)
    m, *_ = build(env)
    assert m._status_of("siempre", 1) == "?"


# --- iniciar el bot ---------------------------------------------------------------------------------


def test_start_bot_only_offers_approved_strategies_and_opens_both_windows(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=True)
    m, _, _, launched = build(env, answers=["1", "s", "s"])
    m.start_bot()
    assert launched == [["paper", "siempre"], ["guard"], ["watch", "siempre"]]


def test_start_bot_without_approved_strategies_or_confirmation(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=False)
    m, out, _, launched = build(env)
    m.start_bot()
    assert launched == [] and any("No hay estrategias aprobadas" in o for o in out)
    saved_strategy(env, approved=True) if False else StrategyStore(
        env / "strategies", key=KEY.encode()
    ).approve("siempre", 1, "c")
    m2, _, _, launched2 = build(env, answers=["1", "n"])
    m2.start_bot()
    assert launched2 == []


def test_live_panel_runs_watch(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=True)
    m, _, calls, _ = build(env, answers=["1"])
    m.live_panel()
    assert calls == [["watch", "siempre"]]


# --- emergencia -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("reply", "expected"), [("s", [["kill", "--reason", "menú"]]), ("", [])])
def test_emergency_brake_needs_confirmation(env, reply, expected):
    m, _, calls, _ = build(env, answers=[reply])
    m.emergency()
    assert calls == expected


@pytest.mark.parametrize(("reply", "expected"), [("s", [["release"]]), ("n", [])])
def test_release_needs_confirmation(env, reply, expected):
    m, _, calls, _ = build(env, answers=[reply])
    m.release()
    assert calls == expected


# --- ventana nueva ----------------------------------------------------------------------------------


def test_launch_opens_a_new_console_on_windows(monkeypatch, env):
    seen = {}
    monkeypatch.setattr("tradebot.menu.os.name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    m = Menu(out=lambda _s: None, ask=Script(), secret=Script(), run=lambda _a: 0)
    m._launch(["paper", "siempre"])
    assert seen["cmd"][-3:] == ["tradebot", "paper", "siempre"] and seen["creationflags"] == 16


def test_launch_runs_inline_elsewhere(monkeypatch):
    monkeypatch.setattr("tradebot.menu.os.name", "posix")
    calls: list[list[str]] = []
    m = Menu(out=lambda _s: None, ask=Script(), secret=Script(), run=lambda a: calls.append(a) or 0)
    m._launch(["paper", "siempre"])
    assert calls == [["paper", "siempre"]]


# --- comando ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["menu"], []])
def test_menu_command_and_no_arguments_open_the_menu(monkeypatch, argv):
    opened = []
    monkeypatch.setattr("tradebot.menu.run_menu", lambda out=print: opened.append(out) or 0)
    assert cli.main(argv, out=lambda _s: None) == 0 and len(opened) == 1


def test_default_menu_uses_the_real_cli(monkeypatch, env):
    monkeypatch.setenv(KEY_ENV, KEY)
    out: list[str] = []
    m = Menu(
        out=out.append, ask=Script("1", "0"), secret=Script(), strategies=env / "s", audit=env / "a.jsonl"
    )
    assert m.loop() == 0 and any("Kill switch" in o for o in out)  # `status` real


# --- pre-vuelo y edición ---------------------------------------------------------------------------


def test_start_bot_runs_review_first_and_cancel_launches_nothing(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=True)
    m, _, calls, launched = build(env, answers=["1", ""])
    m.start_bot()
    assert calls == [["review", "siempre"]] and launched == []


def test_start_bot_edit_option_goes_to_edit_flow(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=True)
    m, _, calls, launched = build(env, answers=["1", "e", "1", "3", "n", "n"])
    m.start_bot()
    assert calls[0] == ["review", "siempre"]
    assert calls[1] == ["edit", "siempre", "--version", "1", "--size", "3"]
    assert launched == []


def test_edit_strategy_stop_loss_and_phrase(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    saved_strategy(env, approved=False)
    m, _, calls, _ = build(env, answers=["2", "2.5", "n", "n", "3", "sube el RSI a 35", "n", "n"])
    m.edit_strategy("siempre", 1)
    m.edit_strategy("siempre", 1)
    assert calls[0] == ["edit", "siempre", "--version", "1", "--stop-loss", "2.5"]
    assert calls[1] == ["edit", "siempre", "--version", "1", "--phrase", "sube el RSI a 35"]


def test_edit_strategy_cancel_paths(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=False)
    m, _, calls, _ = build(env, answers=["", "1", "", "9"])
    m.edit_strategy("siempre", 1)
    m.edit_strategy("siempre", 1)
    m.edit_strategy("siempre", 1)
    assert calls == []
    m.edit_strategy("no-existe", 1)
    assert calls == []


def test_strategies_menu_can_evaluate_by_segments(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=True)
    m, _, calls, _ = build(env, answers=["1", "e"])
    m.strategies_menu()
    assert calls == [["evaluate", "siempre", "--days", "1460"]]


def test_create_from_a_documented_template(env, monkeypatch):
    from tradebot.strategy import templates

    monkeypatch.setenv(KEY_ENV, KEY)
    idx = str(templates.names().index("sma-filtro-tendencia") + 1)
    m, out, calls, _ = build(env, answers=["1", idx, "s", "aprobar"])
    m.create()
    assert calls == [
        ["import", "sma-filtro-tendencia"],
        ["evaluate", "sma-filtro-tendencia", "--days", "1460"],
        ["approve", "sma-filtro-tendencia", "1"],
    ]
    assert any("Fuente:" in o for o in out)  # el usuario ve de dónde sale cada plantilla


def test_create_from_template_cancel_paths(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    for answers in (["1", ""], ["1", "99"], ["1", "abc"], [""]):
        m, _, calls, _ = build(env, answers=answers)
        m.create()
        assert calls == []


def test_strategies_menu_builds_and_opens_the_report(env, monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    saved_strategy(env, approved=True)
    m, _, calls, _ = build(env, answers=["1", "f"])
    opened: list[str] = []
    m.open_file = opened.append
    m.strategies_menu()
    assert calls == [["report", "siempre", "--days", "1460"]] and opened == ["C:/x/siempre-v1.html"]
