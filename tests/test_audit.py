import io
import json
import logging
from decimal import Decimal as D

import pytest

from tradebot.audit import AuditError, AuditLog
from tradebot.logging_setup import configure_logging, current_trace_id, trace
from tradebot.models import Order, RiskLimits, Side
from tradebot.risk import RiskEngine

KEY = b"k" * 40


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "logs" / "audit.jsonl", KEY)


def _fill(audit: AuditLog, n: int = 3) -> None:
    for i in range(n):
        audit.append("camilo", "test.action", f"obj-{i}", "ok", {"i": i})


def _lines(audit: AuditLog) -> list[str]:
    return audit.path.read_text(encoding="utf-8").splitlines()


# --- la cadena -----------------------------------------------------------------------------------


def test_records_are_chained_and_verify(audit):
    _fill(audit)
    r1, r2, r3 = (json.loads(ln) for ln in _lines(audit))
    assert (r1["seq"], r2["seq"], r3["seq"]) == (1, 2, 3)
    assert r1["prev"] == "" and r2["prev"] == r1["mac"] and r3["prev"] == r2["mac"]
    assert audit.verify().ok and audit.verify().records == 3
    assert audit.head() == (3, r3["mac"])


def test_empty_or_missing_log_is_valid(audit):
    assert audit.verify().ok and audit.verify().records == 0
    assert audit.head() is None


def test_chain_continues_across_instances_and_processes(audit):
    _fill(audit, 2)
    AuditLog(audit.path, KEY).append("otro", "x", "y", "ok")  # "otro proceso" con la misma clave
    assert audit.verify().ok and audit.verify().records == 3


def test_edited_record_is_detected(audit):
    _fill(audit)
    audit.path.write_text("\n".join(_lines(audit)).replace("obj-1", "obj-X") + "\n", encoding="utf-8")
    res = audit.verify()
    assert not res.ok and res.records == 1 and "firma" in (res.error or "")


def test_deleted_middle_record_is_detected(audit):
    _fill(audit, 4)
    a, _, c, d = _lines(audit)
    audit.path.write_text("\n".join([a, c, d]) + "\n", encoding="utf-8")
    assert not audit.verify().ok


def test_reordered_records_are_detected(audit):
    _fill(audit, 3)
    a, b, c = _lines(audit)
    audit.path.write_text("\n".join([a, c, b]) + "\n", encoding="utf-8")
    assert not audit.verify().ok


def test_forged_record_without_the_key_is_detected(audit):
    _fill(audit, 2)
    forged = json.dumps({"seq": 3, "actor": "x", "action": "a", "mac": "00" * 32, "prev": "", "details": {}})
    audit.path.write_text("\n".join([*_lines(audit), forged]) + "\n", encoding="utf-8")
    assert not audit.verify().ok


def test_wrong_key_cannot_verify(audit):
    _fill(audit)
    assert not AuditLog(audit.path, b"z" * 40).verify().ok


def test_garbage_lines_are_detected(audit):
    _fill(audit, 1)
    with audit.path.open("a", encoding="utf-8") as f:
        f.write("esto no es json\n")
    res = audit.verify()
    assert not res.ok and "formato" in (res.error or "")


def test_forked_chain_is_detected(audit):
    _fill(audit, 2)
    first = _lines(audit)[0]
    audit.path.write_text("\n".join([first, first]) + "\n", encoding="utf-8")  # duplicado del registro 1
    assert not audit.verify().ok


# --- falla cerrado -------------------------------------------------------------------------------


def test_corrupt_tail_blocks_further_appends(audit):
    _fill(audit, 2)
    with audit.path.open("a", encoding="utf-8") as f:
        f.write("{ basura\n")
    with pytest.raises(AuditError, match="corrupto"):
        audit.append("a", "b", "c", "ok")


def test_tampered_tail_blocks_further_appends(audit):
    _fill(audit, 2)
    audit.path.write_text(audit.path.read_text(encoding="utf-8").replace("obj-1", "obj-9"), encoding="utf-8")
    with pytest.raises(AuditError):
        audit.append("a", "b", "c", "ok")


def test_unwritable_location_raises_audit_error(tmp_path):
    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    with pytest.raises(AuditError, match="no se pudo"):
        AuditLog(blocker / "audit.jsonl", KEY).append("a", "b", "c", "ok")


def test_failed_flush_to_disk_raises_audit_error(audit, monkeypatch):
    def boom(_fd):
        raise OSError("disco lleno")

    monkeypatch.setattr("tradebot.audit.os.fsync", boom)
    with pytest.raises(AuditError, match="no se pudo escribir"):
        audit.append("a", "b", "c", "ok")


def test_verify_reports_unreadable_file_and_skips_blank_lines(audit):
    _fill(audit, 2)
    lines = _lines(audit)
    audit.path.write_text(lines[0] + "\n\n" + lines[1] + "\n", encoding="utf-8")  # línea en blanco: se ignora
    assert audit.verify().ok
    audit.path.write_bytes(b"\xff\xfe\x00\x80")
    res = audit.verify()
    assert not res.ok and "ilegible" in (res.error or "")


def test_oversized_record_is_refused(audit):
    with pytest.raises(AuditError, match="demasiado grande"):
        audit.append("a", "b", "c", "ok", {"blob": "x" * 20000})


def test_record_carries_the_trace_id(audit):
    with trace("abc123"):
        rec = audit.append("a", "b", "c", "ok")
    assert rec["trace_id"] == "abc123"


# --- el motor de riesgo exige rastro para abrir exposición ----------------------------------------


def _buy() -> Order:
    return Order("BTC/USDT", Side.BUY, D("0.003"), D("60000"))


def _sell() -> Order:
    return Order("BTC/USDT", Side.SELL, D("0.003"), D("60000"))


def test_every_risk_decision_is_audited(audit):
    engine = RiskEngine(RiskLimits(), D(10000), audit=audit)
    assert engine.check(_buy(), 0, D(0)).allowed
    assert not engine.check(_buy(), 5, D(0)).allowed
    recs = [json.loads(ln) for ln in _lines(audit)]
    assert [r["result"] for r in recs] == ["allow", "deny"]
    assert recs[1]["details"]["reason"] == "máximo de posiciones abiertas"
    assert audit.verify().ok


def test_buy_is_denied_when_audit_is_down_but_sell_is_still_allowed(tmp_path):
    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    engine = RiskEngine(RiskLimits(), D(10000), audit=AuditLog(blocker / "a.jsonl", KEY))
    d = engine.check(_buy(), 0, D(0))
    assert not d.allowed and "auditoría" in d.reason
    assert engine.check(_sell(), 1, D(0)).allowed  # salir de una posición nunca se bloquea


def test_kill_switch_changes_are_audited_and_release_needs_the_audit(audit, tmp_path):
    state = tmp_path / "kill.json"
    engine = RiskEngine(RiskLimits(), D(1), state, audit)
    engine.kill("prueba", "camilo")
    engine.release("camilo")
    assert [json.loads(ln)["action"] for ln in _lines(audit)] == ["killswitch.activate", "killswitch.release"]

    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    broken = RiskEngine(RiskLimits(), D(1), tmp_path / "k2.json", AuditLog(blocker / "a.jsonl", KEY))
    assert broken.kill() is True  # activar nunca depende de la auditoría
    with pytest.raises(AuditError):
        broken.release("camilo")
    assert broken.killed  # y sin rastro no se reabre el trading


# --- el almacén de estrategias deja rastro ---------------------------------------------------------


def test_strategy_lifecycle_is_audited_and_tampering_is_recorded(tmp_path, audit):
    from tradebot.strategy import Strategy, StrategyStore
    from tradebot.strategy.store import ApprovalError

    spec = {
        "name": "t",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "size_pct": "0.02",
        "entry": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 1},
        "exit": {"type": "cmp", "left": {"kind": "close"}, "op": "<", "right": 1},
    }
    store = StrategyStore(tmp_path / "s", key=KEY, audit=audit)
    store.save(Strategy.model_validate(spec))
    store.approve("t", 1, "camilo")
    store.approve("t", 1, "camilo")  # idempotente: no duplica el registro
    path = tmp_path / "s" / "t" / "v1.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"0.02"', '"0.5"'), encoding="utf-8")
    with pytest.raises(ApprovalError):
        store.load_approved("t")
    assert [json.loads(ln)["action"] for ln in _lines(audit)] == [
        "strategy.save",
        "strategy.approve",
        "strategy.tamper_suspected",
    ]


def test_without_audit_trail_nothing_is_saved_or_approved(tmp_path):
    from tradebot.strategy import Strategy, StrategyStore

    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    store = StrategyStore(tmp_path / "s", key=KEY, audit=AuditLog(blocker / "a.jsonl", KEY))
    spec = {
        "name": "t",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "size_pct": "0.02",
        "entry": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 1},
        "exit": {"type": "cmp", "left": {"kind": "close"}, "op": "<", "right": 1},
    }
    with pytest.raises(AuditError):
        store.save(Strategy.model_validate(spec))
    assert store.versions("t") == []  # la versión no queda huérfana

    plain = StrategyStore(tmp_path / "s", key=KEY)
    plain.save(Strategy.model_validate(spec))
    with pytest.raises(AuditError):
        StrategyStore(tmp_path / "s", key=KEY, audit=AuditLog(blocker / "a.jsonl", KEY)).approve("t", 1, "c")
    assert not plain.is_approved("t", 1)  # la aprobación sin rastro se revierte


# --- logs estructurados ----------------------------------------------------------------------------


def test_logs_are_json_with_trace_id():
    buf = io.StringIO()
    configure_logging("INFO", buf)
    with trace("t-1"):
        logging.getLogger("tradebot.prueba").info("hola %s", "mundo")
    entry = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert entry["msg"] == "hola mundo" and entry["trace_id"] == "t-1" and entry["level"] == "INFO"


def test_logs_include_exceptions():
    buf = io.StringIO()
    configure_logging("INFO", buf)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("tradebot.prueba").exception("fallo")
    assert "RuntimeError: boom" in json.loads(buf.getvalue().strip().splitlines()[-1])["exc"]


def test_configure_logging_is_idempotent_and_validates_level():
    configure_logging("INFO", io.StringIO())
    configure_logging("DEBUG", io.StringIO())
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_tradebot_handler", False)]
    assert len(ours) == 1
    with pytest.raises(ValueError):
        configure_logging("RUIDOSO")


def test_trace_restores_previous_id():
    assert current_trace_id() == "-"
    with trace() as tid:
        assert current_trace_id() == tid and len(tid) == 12
    assert current_trace_id() == "-"
