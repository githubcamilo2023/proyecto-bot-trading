_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def tf_seconds(timeframe: str) -> int:
    """'15m' -> 900, '4h' -> 14400. Lanza ValueError si el formato no es válido."""
    n, unit = timeframe[:-1], timeframe[-1:]
    if unit not in _UNITS or not n.isdigit() or int(n) <= 0:
        raise ValueError(f"timeframe inválido: {timeframe!r}")
    return int(n) * _UNITS[unit]
