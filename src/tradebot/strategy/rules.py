"""Esquema de reglas: JSON validado y estricto. Es lo que produce el LLM y lo que aprueba el usuario."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradebot.config import DEFAULT_STOP_LOSS_PCT, MAX_RULE_CONDITIONS, MAX_RULE_DEPTH
from tradebot.timeframe import tf_seconds

NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")  # campos desconocidos = error, no se ignoran


class Close(_Model):
    """Precio de cierre de la vela."""

    kind: Literal["close"]


class Moving(_Model):
    """Indicador con periodo: media simple, media exponencial o RSI."""

    kind: Literal["sma", "ema", "rsi"]
    period: int = Field(ge=1, le=500)


Ind = Annotated[Close | Moving, Field(discriminator="kind")]
Operand = Ind | Decimal


class Cmp(_Model):
    type: Literal["cmp"] = "cmp"
    left: Operand
    op: Literal["<", "<=", ">", ">=", "crosses_above", "crosses_below"]
    right: Operand


class AllOf(_Model):
    type: Literal["all"] = "all"
    of: "list[Cond]" = Field(min_length=1)


class AnyOf(_Model):
    type: Literal["any"] = "any"
    of: "list[Cond]" = Field(min_length=1)


Cond = Annotated[Cmp | AllOf | AnyOf, Field(discriminator="type")]
AllOf.model_rebuild()
AnyOf.model_rebuild()


def _depth(c: "Cond") -> int:
    return 1 + max(_depth(x) for x in c.of) if isinstance(c, AllOf | AnyOf) else 1


def _count(c: "Cond") -> int:
    return sum(_count(x) for x in c.of) if isinstance(c, AllOf | AnyOf) else 1


class Strategy(_Model):
    """`size_pct` es lo que la estrategia pide (fracción del capital); el motor de riesgo decide el máximo."""

    name: str = Field(pattern=NAME_PATTERN)
    version: int = Field(1, ge=1)
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,12}/[A-Z0-9]{2,12}$")  # formato del exchange: BASE/QUOTE
    timeframe: str
    entry: Cond
    exit: Cond
    size_pct: Decimal = Field(gt=0, le=1)
    stop_loss_pct: Decimal = Field(
        default=DEFAULT_STOP_LOSS_PCT, gt=0, lt=1
    )  # vende si cae tanto desde la entrada
    # Tamaño por riesgo (opcional): la posición se calcula para que, si salta el stop, la pérdida sea
    # `risk_pct` del capital; `size_pct` pasa a ser el TOPE de la posición. Máx. 5 % de riesgo por operación.
    risk_pct: Decimal | None = Field(default=None, gt=0, le=Decimal("0.05"))
    notes: str = ""

    @property
    def order_fraction(self) -> Decimal:
        """Fracción del capital que pide cada compra (el motor de riesgo puede reducirla o negarla)."""
        if self.risk_pct is None:
            return self.size_pct
        return min(self.size_pct, self.risk_pct / self.stop_loss_pct)

    @field_validator("timeframe")
    @classmethod
    def _check_timeframe(cls, v: str) -> str:
        tf_seconds(v)
        return v

    @model_validator(mode="after")
    def _check_rule_size(self) -> Self:
        for label, rule in (("entry", self.entry), ("exit", self.exit)):
            if _depth(rule) > MAX_RULE_DEPTH:
                raise ValueError(f"{label}: la regla anida más de {MAX_RULE_DEPTH} niveles")
            if _count(rule) > MAX_RULE_CONDITIONS:
                raise ValueError(f"{label}: la regla tiene más de {MAX_RULE_CONDITIONS} comparaciones")
        return self
