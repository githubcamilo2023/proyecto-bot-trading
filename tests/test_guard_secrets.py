from pathlib import Path

from tradebot.guard_secrets import main, scan_file, scan_text


def test_detecta_clave_privada_y_aws():
    assert "clave privada" in scan_text("-----BEGIN RSA PRIVATE KEY-----")
    assert "clave de AWS" in scan_text("AKIAABCDEFGHIJKLMNOP")


def test_detecta_asignacion_de_secreto():
    assert scan_text('API_SECRET = "abcd1234efgh5678ijkl"')
    assert scan_text("password: Zx9Qw8Er7Ty6Ui5Op4")


def test_ignora_placeholders_y_texto_normal():
    assert not scan_text("BROKER_API_KEY=changeme-please-change-me")
    assert not scan_text("TRADEBOT_KEY=$TRADEBOT_KEY")
    assert not scan_text("def token(): return 1")


def test_env_prohibido_pero_example_permitido(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("X=1", encoding="utf-8")
    ex = tmp_path / ".env.example"
    ex.write_text("X=1", encoding="utf-8")
    assert scan_file(env)
    assert not scan_file(ex)


def test_binarios_y_faltantes_se_ignoran(tmp_path: Path):
    b = tmp_path / "a.bin"
    b.write_bytes(b"\xff\xfe\x00\x80")
    assert scan_file(b) == []
    assert scan_file(tmp_path / "no-existe.txt") == []


def test_main_codigo_de_salida(tmp_path: Path, capsys):
    bad = tmp_path / "c.py"
    bad.write_text('api_key = "abcd1234efgh5678ijkl"', encoding="utf-8")
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1", encoding="utf-8")
    assert main([str(ok)]) == 0
    assert main([str(bad)]) == 1
    assert "posible secreto" in capsys.readouterr().err
