from typing import Iterable, List, Optional, Sequence

from .models import OHLC


def calculate_rsi_series(closes: Sequence[float], period: int = 14) -> List[float]:
    """Return Wilder-smoothed RSI values for each bar after warm-up."""
    if period <= 0:
        raise ValueError("RSI period must be positive")
    n = len(closes)
    if n < period + 1:
        return []

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsis: List[float] = []
    if avg_loss == 0:
        rsis.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsis.append(100 - (100 / (1 + rs)))

    for idx in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))

    return rsis


def calculate_rsi_latest(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Return the most recent Wilder-smoothed RSI value."""
    series = calculate_rsi_series(closes, period)
    return series[-1] if series else None


def closed_closes(bars: Iterable[OHLC]) -> List[float]:
    """Return closes for bars flagged as closed, preserving order."""
    return [bar.close for bar in bars if getattr(bar, "is_closed", None) is not False]
