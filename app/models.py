from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel


class Timeframe(str, Enum):
    M1 = "1M"
    M5 = "5M"
    M15 = "15M"
    M30 = "30M"
    H1 = "1H"
    H4 = "4H"
    D1 = "1D"
    W1 = "1W"


class Tick(BaseModel):
    symbol: str
    time: int
    time_iso: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    flags: Optional[int] = None


class OHLC(BaseModel):
    symbol: str
    timeframe: str
    time: int
    time_iso: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


class SubscriptionInfo(BaseModel):
    symbol: str
    timeframe: Timeframe
    subscription_time: datetime
    data_types: List[str]


class NewsItem(BaseModel):
    headline: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    currency: Optional[str] = None
    impact: Optional[str] = None
    time: Optional[str] = None


class NewsAnalysis(BaseModel):
    headline: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    currency: Optional[str] = None
    time: Optional[str] = None
    analysis: Dict[str, str]
    analyzed_at: datetime


