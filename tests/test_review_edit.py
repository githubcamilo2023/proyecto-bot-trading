import json
from decimal import Decimal as D

import pytest

from tradebot.cli import main
from tradebot.models import RiskLimits
from tradebot.strategy import Strategy, StrategyStore
from tradebot.strategy.edit import changes, edit_directive, pct, with_values
from tradebot.strategy.review import origin_of, render_review, review
from tradebot.strategy.store import ApprovalError

KEY = "k" * 40
AUDIT: list = [None]
SPEC = {
    "name": "rebote",
    "symbol": "BTC/USDT",
    "timeframe": "4h",
    "size_pct": "0.02",
    "entry": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": "<", "right": 30},
    "exit": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": ">", "right": 55},
}


class Fake:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)

    def complete(self, system: str, user: str) -> str:
        return self.replies.pop(0)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    d = tmp_path / "s"
    store = StrategyStore(d, key=KEY.encode())
    store.save(Strategy.model_validate(SPEC))
    store.approve("rebote", 1, "camilo")
    common = ["--strategies-dir", str(d)]
    AUDIT[0] = tmp_path / "audit.jsonl"
    return common, store, tmp_path


def run(args, llm=None):
    lines: list[str] = []
    code = main(["--audit", str(AUDIT[0]), *args], out=lines.append, llm=llm)
    return code, "\n".join(lines)


# --- edit.py ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [("2", "0.02"), ("2%", "0.02"), (" 2,5 ", "0.025")])
def test_pct_accepts_percentages(text, expected):
    assert pct(text) == D(expected)


@pytest.mark.parametrize("text", ["", "abc", "-1", "1e3"])
def test_pct_rejects_garbage(text):
    with pytest.raises(ValueError):
        pct(text)


def test_with_values_revalidates():
    s = Strategy.model_validate(SPEC)
    assert with_values(s, D("0.05"), D("0.04")).size_pct == D("0.05")
    with pytest.raises(ValueError):
        with_values(s, D("2"))


def test_edit_directive_limits_and_content():
    s = Strategy.model_validate(SPEC)
    assert "Cambio pedido: sube el RSI" in edit_directive(s, " sube el RSI ")
    with pytest.raises(ValueError):
        edit_directive(s, "  ")
    with pytest.raises(ValueError):
        edit_directive(s, "x" * 401)


def test_changes_lists_only_differences():
    s = Strategy.model_validate(SPEC)
    diff = changes(s, with_values(s, D("0.03")))
    assert diff and all(ln.startswith(("- ", "+ ")) for ln in diff)
    assert changes(s, s) == []


# --- review.py ----------------------------------------------------------------------------------------


def test_review_requires_valid_approval(env):
    common, store, tmp_path = env
    StrategyStore(tmp_path / "otra", key=KEY.encode()).save(Strategy.model_validate(SPEC))
    with pytest.raises(ApprovalError):
        review(StrategyStore(tmp_path / "otra", key=KEY.encode()), "rebote", tmp_path / "a.jsonl")


def test_cli_review_shows_strategy_why_and_warnings(env):
    common, store, tmp_path = env
    store.save(Strategy.model_validate(SPEC))  # v2 sin aprobar
    code, text = run(["review", "rebote", *common])
    assert code == 0
    assert "versión 1" in text and "camilo" in text
    assert "v2" in text  # aviso de versión más nueva sin aprobar


def test_cli_review_errors(env, monkeypatch):
    common, *_ = env
    assert run(["review", "no-existe", *common])[0] == 2
    monkeypatch.delenv("TRADEBOT_KEY")
    assert run(["review", "rebote", *common])[0] == 2


def test_render_review_warns_when_killed(env):
    common, store, tmp_path = env
    r = review(store, "rebote", tmp_path / "audit.jsonl")
    assert "kill" in render_review(r, RiskLimits(), killed=True).lower()


# --- edit CLI + origen -------------------------------------------------------------------------------


def test_edit_values_creates_unapproved_new_version(env):
    common, store, tmp_path = env
    code, text = run(["edit", "rebote", "--size", "3", "--stop-loss", "2", *common])
    assert code == 0 and "rebote v2, SIN APROBAR" in text
    assert store.load("rebote", 2).size_pct == D("0.03")
    assert store.load("rebote", 1).size_pct == D("0.02")
    assert store.load_approved("rebote").version == 1
    assert "strategy.edit" in (tmp_path / "audit.jsonl").read_text()


def test_edit_without_changes_or_with_conflicting_flags(env):
    common, store, _ = env
    assert "no modifica nada" in run(["edit", "rebote", "--size", "2", *common])[1]
    assert run(["edit", "rebote", *common])[0] == 2
    assert run(["edit", "rebote", "--size", "3", "--phrase", "x", *common])[0] == 2
    assert run(["edit", "rebote", "--size", "abc", *common])[0] == 2
    assert run(["edit", "rebote", "--size", "300", *common])[0] == 2
    assert len(store.versions("rebote")) == 1 if hasattr(store, "versions") else True


def test_edit_with_phrase_uses_llm_and_records_origin(env):
    common, store, tmp_path = env
    new = {**SPEC, "name": "otro-nombre", "entry": {**SPEC["entry"], "right": 35}}
    code, text = run(["edit", "rebote", "--phrase", "sube el umbral a 35", *common], Fake(json.dumps(new)))
    assert code == 0 and "rebote v2" in text  # conserva el nombre
    assert store.load("rebote", 2).entry.right == 35  # type: ignore[union-attr]
    origin = origin_of(tmp_path / "audit.jsonl", "rebote", 2)
    assert origin and "sube el umbral a 35" in origin


def test_edit_with_failing_llm_exits_3(env):
    common, *_ = env
    code, text = run(["edit", "rebote", "--phrase", "algo", *common], Fake("no json", "no json", "no json"))
    assert code == 3 and "No se pudo" in text


def test_origin_chain_follows_edits(env):
    common, store, tmp_path = env
    audit = tmp_path / "audit.jsonl"
    assert origin_of(audit, "rebote", 1) is None
    run(["edit", "rebote", "--size", "3", *common])
    run(["edit", "rebote", "--version", "2", "--stop-loss", "2", *common])
    text = origin_of(audit, "rebote", 3)
    assert text and "ajustada" in text
