import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from tradebot.config import MAX_RULE_CONDITIONS, MAX_RULE_DEPTH
from tradebot.integrity import MissingKeyError
from tradebot.models import Candle
from tradebot.strategy import Signal, Strategy, StrategyStore, signal
from tradebot.strategy.indicators import ema, rsi, sma
from tradebot.strategy.store import ApprovalError

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ds(*xs) -> list[D]:
    return [D(str(x)) for x in xs]


def candles(*closes) -> list[Candle]:
    def one(i: int, c) -> Candle:
        px = D(str(c))
        return Candle(T0 + timedelta(hours=i), px, px, px, px, D(1))

    return [one(i, c) for i, c in enumerate(closes)]


def strat(entry: dict, exit_: dict | None = None) -> Strategy:
    never = {"type": "cmp", "left": {"kind": "close"}, "op": "<", "right": -1}
    base = {"name": "t", "symbol": "BTC/USDT", "timeframe": "1h", "size_pct": "0.02"}
    return Strategy.model_validate(base | {"entry": entry, "exit": exit_ or never})


def cmp(left, op, right) -> dict:
    return {"type": "cmp", "left": left, "op": op, "right": right}


CLOSE = {"kind": "close"}


def sma_(n):
    return {"kind": "sma", "period": n}


def rsi_(n):
    return {"kind": "rsi", "period": n}


# --- indicadores ---------------------------------------------------------------------------------


def test_sma():
    assert sma(ds(1, 2, 3, 4, 5), 3) == D(4)
    assert sma(ds(1, 2), 3) is None


def test_ema_seeds_with_sma():
    assert ema(ds(1, 2, 3, 4, 5), 3) == D(4)  # semilla 2 -> 3 -> 4
    assert ema(ds(1, 2), 3) is None


def test_rsi_known_values():
    assert rsi(ds(1, 2, 1, 2), 2) == D(75)  # g=0.75, l=0.25 -> RS=3
    assert rsi(ds(1, 2, 3, 4), 2) == D(100)
    assert rsi(ds(4, 3, 2, 1), 2) == D(0)
    assert rsi(ds(5, 5, 5, 5), 2) == D(50)
    assert rsi(ds(1, 2), 2) is None


# --- validación del esquema ----------------------------------------------------------------------

SPEC = {
    "name": "rsi-sobrevendido",
    "symbol": "BTC/USDT",
    "timeframe": "4h",
    "size_pct": "0.02",
    "entry": {"type": "all", "of": [cmp(rsi_(14), "<", 30), cmp(CLOSE, ">", sma_(200))]},
    "exit": cmp(rsi_(14), ">", 55),
}


def test_spec_parses_and_roundtrips_through_json():
    s = Strategy.model_validate(SPEC)
    assert Strategy.model_validate_json(s.model_dump_json()) == s


@pytest.mark.parametrize(
    "patch",
    [
        {"extra_field": 1},  # campos desconocidos se rechazan
        {"name": "../etc"},
        {"name": "Mayusculas"},
        {"timeframe": "4x"},
        {"size_pct": "0"},
        {"size_pct": "1.5"},
        {"entry": cmp({"kind": "sma"}, "<", 1)},  # sma sin period
        {"entry": cmp({"kind": "close", "period": 5}, "<", 1)},  # close con period
        {"entry": cmp({"kind": "sma", "period": 0}, "<", 1)},
        {"entry": cmp({"kind": "macd", "period": 5}, "<", 1)},  # indicador no soportado
        {"entry": {"type": "all", "of": []}},
        {"entry": cmp(CLOSE, "!=", 1)},
    ],
)
def test_invalid_specs_are_rejected(patch):
    with pytest.raises(ValidationError):
        Strategy.model_validate(SPEC | patch)


def test_min_candles_is_largest_period_plus_two():
    from tradebot.strategy.engine import min_candles

    assert min_candles(Strategy.model_validate(SPEC)) == 202  # sma(200) + 2
    assert min_candles(strat(cmp(CLOSE, ">", 1))) == 2  # solo close


# --- señales -------------------------------------------------------------------------------------


def test_buy_when_entry_true_and_hold_when_false():
    s = strat(cmp(CLOSE, ">", sma_(3)))
    assert signal(s, candles(1, 2, 3, 10), in_position=False) is Signal.BUY  # 10 > 5
    assert signal(s, candles(10, 3, 2, 1), in_position=False) is Signal.HOLD  # 1 < 2


def test_hold_when_not_enough_data():
    assert signal(strat(cmp(CLOSE, ">", sma_(3))), candles(1, 2), False) is Signal.HOLD
    assert signal(strat(cmp(CLOSE, ">", 1)), [], False) is Signal.HOLD


def test_crosses_above_fires_only_on_the_crossing_candle():
    s = strat(cmp(CLOSE, "crosses_above", sma_(2)))
    assert signal(s, candles(5, 4, 3, 4), False) is Signal.BUY
    assert signal(s, candles(5, 4, 3, 4, 5), False) is Signal.HOLD  # ya estaba arriba


def test_crosses_below():
    s = strat(cmp(CLOSE, "crosses_below", sma_(2)))
    assert signal(s, candles(1, 2, 3, 2), False) is Signal.BUY  # 3 >= 2.5 antes; 2 < 2.5 ahora
    assert signal(s, candles(1, 2, 3, 2, 1), False) is Signal.HOLD


def test_in_position_only_evaluates_exit():
    s = strat(entry=cmp(CLOSE, ">", 0), exit_=cmp(rsi_(2), ">", 70))
    assert signal(s, candles(1, 2, 3, 4), in_position=True) is Signal.SELL  # rsi = 100
    assert signal(s, candles(4, 3, 2, 1), in_position=True) is Signal.HOLD  # entry sería true, pero se ignora
    assert signal(s, candles(1, 2, 3, 4), in_position=False) is Signal.BUY


def test_all_and_any_combinators():
    t, f = cmp(CLOSE, ">", 1), cmp(CLOSE, "<", 0)
    c = candles(1, 2)
    assert signal(strat({"type": "any", "of": [f, t]}), c, False) is Signal.BUY
    assert signal(strat({"type": "all", "of": [f, t]}), c, False) is Signal.HOLD
    assert signal(strat({"type": "all", "of": [t, {"type": "any", "of": [f, t]}]}), c, False) is Signal.BUY


KEY = b"k" * 40
NAME = "rsi-sobrevendido"


# --- almacén versionado --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> StrategyStore:
    return StrategyStore(tmp_path / "strategies", key=KEY)


def test_save_creates_incrementing_immutable_versions(store):
    s = Strategy.model_validate(SPEC)
    v1, v2 = store.save(s), store.save(s)
    assert (v1.version, v2.version) == (1, 2)
    assert store.versions("rsi-sobrevendido") == [1, 2]
    assert store.load("rsi-sobrevendido") == v2  # sin versión = la última
    assert store.load("rsi-sobrevendido", 1) == v1


def test_unknown_strategy_or_version_raises(store):
    with pytest.raises(FileNotFoundError):
        store.load("no-existe")
    store.save(Strategy.model_validate(SPEC))
    with pytest.raises(FileNotFoundError):
        store.load("rsi-sobrevendido", 9)


def test_invalid_names_are_rejected_before_touching_disk(store):
    with pytest.raises(ValueError):
        store.versions("../afuera")


def test_approval_is_per_version_and_idempotent(store):
    s = Strategy.model_validate(SPEC)
    store.save(s), store.save(s)
    assert not store.is_approved("rsi-sobrevendido", 1)
    store.approve("rsi-sobrevendido", 1, "camilo")
    store.approve("rsi-sobrevendido", 1, "otro")  # no pisa el registro original
    assert store.is_approved("rsi-sobrevendido", 1) and not store.is_approved("rsi-sobrevendido", 2)
    assert "camilo" in (store.root / "rsi-sobrevendido" / "v1.approved.json").read_text()
    assert store.versions("rsi-sobrevendido") == [1, 2]  # el archivo de aprobación no cuenta como versión
    with pytest.raises(FileNotFoundError):
        store.approve("rsi-sobrevendido", 7, "camilo")


# --- integridad de la aprobación -----------------------------------------------------------------


def _saved(store: StrategyStore, n: int = 1) -> None:
    for _ in range(n):
        store.save(Strategy.model_validate(SPEC))


def test_editing_an_approved_strategy_invalidates_approval(store):
    _saved(store)
    store.approve(NAME, 1, "camilo")
    path = store.root / NAME / "v1.json"
    before = path.read_text(encoding="utf-8")
    path.write_text(before.replace('"size_pct": "0.02"', '"size_pct": "1"'), encoding="utf-8")
    assert path.read_text(encoding="utf-8") != before  # la edición realmente cambió el archivo
    assert not store.is_approved(NAME, 1)
    with pytest.raises(ApprovalError, match="cambió"):
        store.check_approval(NAME, 1)


def test_forged_or_tampered_approval_record_is_rejected(store):
    _saved(store)
    store.approve(NAME, 1, "camilo")
    rec_path = store.root / NAME / "v1.approved.json"
    rec = json.loads(rec_path.read_text())
    rec["by"] = "atacante"  # cambia quién aprobó sin poder recalcular la firma
    rec_path.write_text(json.dumps(rec))
    with pytest.raises(ApprovalError, match="firma"):
        store.check_approval(NAME, 1)


def test_approval_signed_with_another_key_is_rejected(store, tmp_path):
    _saved(store)
    store.approve(NAME, 1, "camilo")
    other = StrategyStore(store.root, key=b"z" * 40)
    assert not other.is_approved(NAME, 1)


def test_approval_cannot_be_copied_to_another_version(store):
    _saved(store, 2)
    store.approve(NAME, 1, "camilo")
    d = store.root / NAME
    (d / "v2.approved.json").write_text((d / "v1.approved.json").read_text())
    with pytest.raises(ApprovalError, match="otra versión"):
        store.check_approval(NAME, 2)


@pytest.mark.parametrize("junk", ["no es json", "[]", '{"by": "x"}'])
def test_garbage_approval_files_fail_closed(store, junk):
    _saved(store)
    (store.root / NAME / "v1.approved.json").write_text(junk)
    assert not store.is_approved(NAME, 1)
    store.approve(NAME, 1, "camilo")  # una aprobación inválida se puede reemplazar por una válida
    assert store.is_approved(NAME, 1)


def test_approval_needs_the_key(tmp_path):
    keyless = StrategyStore(tmp_path / "s")
    keyless.save(Strategy.model_validate(SPEC))
    assert keyless.load(NAME).version == 1  # leer no necesita clave
    with pytest.raises(MissingKeyError):
        keyless.approve(NAME, 1, "camilo")
    with pytest.raises(MissingKeyError):
        keyless.is_approved(NAME, 1)


def test_load_approved_returns_latest_valid_approved_version(store, caplog):
    _saved(store, 3)
    with pytest.raises(ApprovalError, match="ninguna"):
        store.load_approved(NAME)
    store.approve(NAME, 2, "camilo")
    with caplog.at_level("WARNING", logger="tradebot.strategy"):
        assert store.load_approved(NAME).version == 2  # v3 sin aprobar: se usa v2 y se avisa
    assert "más nuevas sin aprobar" in caplog.text
    store.approve(NAME, 3, "camilo")
    assert store.load_approved(NAME).version == 3


def test_load_approved_skips_tampered_latest(store):
    _saved(store, 2)
    store.approve(NAME, 1, "camilo")
    store.approve(NAME, 2, "camilo")
    path = store.root / NAME / "v2.json"
    before = path.read_text(encoding="utf-8")
    path.write_text(before.replace('"size_pct": "0.02"', '"size_pct": "0.9"'), encoding="utf-8")
    assert path.read_text(encoding="utf-8") != before
    assert store.load_approved(NAME).version == 1


# --- tope explícito de anidación -----------------------------------------------------------------


def _nest(depth: int) -> dict:
    node: dict = cmp(CLOSE, ">", 0)
    for _ in range(depth - 1):
        node = {"type": "all", "of": [node]}
    return node


def test_rule_depth_limit():
    assert strat(_nest(MAX_RULE_DEPTH))  # justo en el límite: válida
    with pytest.raises(ValueError, match="anida"):
        strat(_nest(MAX_RULE_DEPTH + 1))


def test_rule_condition_count_limit():
    ok = {"type": "all", "of": [cmp(CLOSE, ">", i) for i in range(MAX_RULE_CONDITIONS)]}
    assert strat(ok)
    with pytest.raises(ValueError, match="comparaciones"):
        strat({"type": "all", "of": [cmp(CLOSE, ">", i) for i in range(MAX_RULE_CONDITIONS + 1)]})


@pytest.mark.parametrize("bad", ["BTC", "btc/usdt", "BTC-USDT", "BTC/", "/USDT", "BTC/USDT/X", " BTC/USDT"])
def test_symbol_must_be_base_slash_quote(bad):
    never = cmp(CLOSE, "<", -1)
    spec = {"name": "t", "symbol": bad, "timeframe": "1h", "size_pct": "0.02", "entry": never, "exit": never}
    with pytest.raises(ValidationError):
        Strategy.model_validate(spec)
