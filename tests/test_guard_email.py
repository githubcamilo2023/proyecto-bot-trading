import json
from datetime import UTC, datetime, timedelta

from tradebot.alerts import (
    CHAT_ENV,
    EMAIL_TO_ENV,
    SMTP_PASSWORD_ENV,
    SMTP_USER_ENV,
    TOKEN_ENV,
    _email,
    channels,
    make_notifier,
)
from tradebot.cli import main
from tradebot.guard import STARTUP_GRACE, health, run_guard
from tradebot.watch import Heartbeat

T = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
MAIL = {SMTP_USER_ENV: "bot@gmail.com", SMTP_PASSWORD_ENV: "apppassSECRETA16"}


# --- correo -----------------------------------------------------------------------------------------


def test_email_goes_to_the_sender_by_default_or_to_the_configured_address(tmp_path):
    sent = []
    make_notifier(tmp_path / "a.log", MAIL, mail=lambda *a: sent.append(a))("compra hecha")
    assert sent == [
        ("smtp.gmail.com", 465, "bot@gmail.com", "apppassSECRETA16", "bot@gmail.com", "compra hecha")
    ]
    sent.clear()
    make_notifier(tmp_path / "a.log", {**MAIL, EMAIL_TO_ENV: "yo@x.com"}, mail=lambda *a: sent.append(a))("y")
    assert sent[0][4] == "yo@x.com"


def test_email_needs_user_and_password(tmp_path):
    sent = []
    make_notifier(tmp_path / "a.log", {SMTP_USER_ENV: "bot@gmail.com"}, mail=lambda *a: sent.append(a))("x")
    assert sent == []


def test_failed_email_does_not_raise_nor_leak_the_password(tmp_path, caplog):
    def boom(*a):
        raise RuntimeError("login failed for apppassSECRETA16")

    with caplog.at_level("ERROR"):
        make_notifier(tmp_path / "a.log", MAIL, mail=boom)("hola")
    assert "apppassSECRETA16" not in caplog.text and "correo (RuntimeError)" in caplog.text


def test_one_failing_channel_does_not_block_the_other(tmp_path):
    got = []

    def boom(*a):
        raise OSError("sin red")

    env = {**MAIL, TOKEN_ENV: "t", CHAT_ENV: "c"}
    make_notifier(tmp_path / "a.log", env, send=lambda *a: got.append(a), mail=boom)("hola")
    assert got == [("t", "c", "TRADEBOT: hola")]


def test_newlines_cannot_inject_email_headers(tmp_path):
    sent = []
    make_notifier(tmp_path / "a.log", MAIL, mail=lambda *a: sent.append(a))("hola\r\nBcc: otro@x.com")
    assert "\n" not in sent[0][5] and "\r" not in sent[0][5]


def test_email_message_is_built_and_sent_over_tls(monkeypatch):
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout, context):
            calls["conn"] = (host, port, timeout, context is not None)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["msg"] = msg

    monkeypatch.setattr("tradebot.alerts.smtplib.SMTP_SSL", FakeSMTP)
    _email("smtp.gmail.com", 465, "bot@gmail.com", "pw", "yo@x.com", "kill switch activado")
    assert calls["conn"] == ("smtp.gmail.com", 465, 10, True) and calls["login"] == ("bot@gmail.com", "pw")
    msg = calls["msg"]
    assert msg["To"] == "yo@x.com" and msg["Subject"].startswith("TRADEBOT: kill switch")
    assert "No es asesoría financiera" in msg.get_content()


def test_channels_lists_configured_channels_without_secrets():
    assert channels({}) == []
    out = channels({**MAIL, EMAIL_TO_ENV: "yo@x.com", TOKEN_ENV: "t", CHAT_ENV: "c"})
    assert out == ["correo a yo@x.com", "Telegram"] and "apppass" not in str(out)


# --- vigilante --------------------------------------------------------------------------------------


def beat(next_due: datetime) -> Heartbeat:
    return Heartbeat(T, "ok", "hold: sin señal", next_due)


def test_health_states():
    assert health(beat(T + timedelta(hours=1)), None, T, T) == ""
    assert "NO late desde hace 10 min" in health(beat(T - timedelta(minutes=10)), None, T, T)
    assert health(None, None, T, T) == ""  # recién arrancado: aún no hay latido
    assert "no está corriendo" in health(None, None, T + STARTUP_GRACE, T)
    assert "kill switch ACTIVADO (caída máxima)" in health(beat(T + timedelta(hours=1)), "caída máxima", T, T)
    assert "sin motivo" in health(None, "", T, T)
    assert "ilegible" in health("ilegible", None, T, T)


def test_guard_alerts_once_per_change_and_on_recovery(tmp_path):
    hb = tmp_path / "hb.json"
    clock = [T]

    def write(next_due):
        hb.write_text(
            json.dumps({"ts": T.isoformat(), "status": "ok", "detail": "x", "next_due": next_due.isoformat()})
        )

    # vueltas: ok, tarde, tarde (no repite), recuperado
    plan = iter(
        [T + timedelta(hours=1), T - timedelta(hours=1), T - timedelta(hours=1), T + timedelta(hours=1)]
    )

    def sleep(_):
        write(next(plan))

    msgs: list[str] = []
    stops = iter([False] * 4 + [True])
    run_guard(
        msgs.append, lambda m: None, heartbeat=hb, kill_state=tmp_path / "kill.json",
        interval=60, sleep=sleep, now=lambda: clock[0], stop=lambda: next(stops),
    )  # fmt: skip
    assert (
        len(msgs) == 2 and msgs[0].startswith("PROBLEMA: el bot NO late") and msgs[1].startswith("RECUPERADO")
    )


def test_guard_reports_kill_switch(tmp_path):
    kill = tmp_path / "kill.json"
    kill.write_text(json.dumps({"killed": True, "reason": "caída máxima del 10%", "actor": "x", "at": "y"}))
    msgs: list[str] = []
    stops = iter([False, True])
    run_guard(
        msgs.append, lambda m: None, heartbeat=tmp_path / "hb.json", kill_state=kill,
        interval=60, sleep=lambda s: None, now=lambda: T, stop=lambda: next(stops),
    )  # fmt: skip
    assert msgs == ["PROBLEMA: kill switch ACTIVADO (caída máxima del 10%): el bot no compra"]


def run_cli(args):
    lines: list[str] = []
    return main(args, out=lines.append), "\n".join(lines)


def test_guard_test_command_sends_a_test_alert(tmp_path):
    code, text = run_cli(["guard", "--test", "--alerts", str(tmp_path / "a.log")])
    assert code == 0 and "Avisos solo en" in text and "prueba" in text.lower()
    assert "Prueba de aviso" in (tmp_path / "a.log").read_text(encoding="utf-8")


def test_guard_validates_interval_and_stops_cleanly(tmp_path, monkeypatch):
    assert run_cli(["guard", "--interval", "1"])[0] == 2

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("tradebot.cli.run_guard", interrupt)
    code, text = run_cli(["guard", "--alerts", str(tmp_path / "a.log")])
    assert code == 0 and "Vigilante detenido" in text


def test_port_587_uses_starttls_before_login(monkeypatch):
    order = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            order.append(("conn", port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            order.append("starttls")

        def login(self, user, password):
            order.append("login")

        def send_message(self, msg):
            order.append("send")

    monkeypatch.setattr("tradebot.alerts.smtplib.SMTP", FakeSMTP)
    _email("smtp.gmail.com", 587, "bot@gmail.com", "pw", "yo@x.com", "hola")
    assert order == [("conn", 587), "starttls", "login", "send"]


def test_port_comes_from_the_environment(tmp_path):
    sent = []
    for port in ("587", "abc"):
        make_notifier(
            tmp_path / "a.log", {**MAIL, "TRADEBOT_SMTP_PORT": port}, mail=lambda *a: sent.append(a)
        )("x")
    assert [c[1] for c in sent] == [587, 465]


def test_failures_are_returned_with_a_hint(tmp_path):
    import smtplib

    def boom(*a):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    failed = make_notifier(tmp_path / "a.log", MAIL, mail=boom)("x")
    assert len(failed) == 1 and "SMTPServerDisconnected" in failed[0] and "587" in failed[0]


def test_guard_test_reports_failure_clearly(tmp_path, monkeypatch):
    import smtplib

    monkeypatch.setenv("TRADEBOT_SMTP_USER", "bot@gmail.com")
    monkeypatch.setenv("TRADEBOT_SMTP_PASSWORD", "x")

    def boom(*a, **k):
        raise smtplib.SMTPAuthenticationError(535, b"bad")

    monkeypatch.setattr("tradebot.alerts.smtplib.SMTP_SSL", boom)
    code, text = run_cli(["guard", "--test", "--alerts", str(tmp_path / "a.log")])
    assert code == 3 and "FALLÓ" in text and "contraseña de aplicación incorrectos" in text


def test_tests_never_see_real_credentials():
    import os

    assert not [k for k in os.environ if k.startswith("TRADEBOT_")]


def test_config_problems_detect_wrong_password_length_without_showing_it():
    from tradebot.alerts import config_problems

    assert config_problems({}) == []
    assert config_problems({SMTP_USER_ENV: "bot@gmail.com", SMTP_PASSWORD_ENV: "abcd efgh ijkl mnop"}) == []
    probs = config_problems({SMTP_USER_ENV: "bot", SMTP_PASSWORD_ENV: "miClave$123"})
    assert "tiene 11 caracteres" in probs[0] and "miClave" not in " ".join(probs)
    assert "dirección completa" in probs[1]
    other = {SMTP_USER_ENV: "a@x.com", SMTP_PASSWORD_ENV: "corta", "TRADEBOT_SMTP_HOST": "smtp.x.com"}
    assert config_problems(other) == []  # otros proveedores: no se adivina el formato


def test_gmail_password_spaces_are_removed(tmp_path):
    sent = []
    env = {SMTP_USER_ENV: " bot@gmail.com ", SMTP_PASSWORD_ENV: "abcd efgh ijkl mnop"}
    make_notifier(tmp_path / "a.log", env, mail=lambda *a: sent.append(a))("x")
    assert sent[0][2] == "bot@gmail.com" and sent[0][3] == "abcdefghijklmnop"


def test_hint_does_not_suggest_587_when_already_using_it(tmp_path):
    import smtplib

    def boom(*a):
        raise smtplib.SMTPServerDisconnected("x")

    failed = make_notifier(tmp_path / "a.log", {**MAIL, "TRADEBOT_SMTP_PORT": "587"}, mail=boom)("x")
    assert "587" not in failed[0] and "contraseña de aplicación" in failed[0]


def test_guard_test_warns_about_short_password(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEBOT_SMTP_USER", "bot@gmail.com")
    monkeypatch.setenv("TRADEBOT_SMTP_PASSWORD", "zq7wv")
    monkeypatch.setattr("tradebot.alerts.smtplib.SMTP_SSL", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    code, text = run_cli(["guard", "--test", "--alerts", str(tmp_path / "a.log")])
    assert code == 3 and "ATENCIÓN: la contraseña tiene 5 caracteres" in text and "zq7wv" not in text
