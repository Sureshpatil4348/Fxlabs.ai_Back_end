from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field


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
    # Internal flag to avoid re-sending the 5-minute reminder email for this event
    reminder_sent: bool = False


class HeatmapAlertRequest(BaseModel):
    alert_name: str
    user_id: Optional[str] = None  # Add user_id for frontend compatibility
    user_email: str
    pairs: List[str]
    timeframes: List[str]
    selected_indicators: List[str]
    trading_style: str = "dayTrader"
    buy_threshold_min: int = 70
    buy_threshold_max: int = 100
    sell_threshold_min: int = 0
    sell_threshold_max: int = 30
    notification_methods: List[str] = ["email"]
    alert_frequency: str = "once"
    trigger_on_crossing: bool = True


class HeatmapAlertResponse(BaseModel):
    id: str
    alert_name: str
    user_id: Optional[str] = None  # Add user_id for frontend compatibility
    user_email: str
    pairs: List[str]
    timeframes: List[str]
    selected_indicators: List[str]
    trading_style: str
    buy_threshold_min: int
    buy_threshold_max: int
    sell_threshold_min: int
    sell_threshold_max: int
    notification_methods: List[str]
    alert_frequency: str
    trigger_on_crossing: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RSIAlertRequest(BaseModel):
    alert_name: str
    user_id: Optional[str] = None
    user_email: str
    pairs: List[str]
    timeframes: List[str] = ["1H"]
    rsi_period: int = 14
    rsi_overbought_threshold: int = 70
    rsi_oversold_threshold: int = 30
    alert_conditions: List[str]
    cooldown_minutes: Optional[int] = 30
    notification_methods: List[str] = ["email"]
    alert_frequency: str = "once"


class RSIAlertResponse(BaseModel):
    id: str
    alert_name: str
    user_id: Optional[str] = None
    user_email: str
    pairs: List[str]
    timeframes: List[str]
    rsi_period: int
    rsi_overbought_threshold: int
    rsi_oversold_threshold: int
    alert_conditions: List[str]
    cooldown_minutes: int
    notification_methods: List[str]
    alert_frequency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RSICorrelationAlertRequest(BaseModel):
    alert_name: str
    user_id: Optional[str] = None
    user_email: str
    pairs: List[List[str]] = Field(alias="correlation_pairs")  # List of [symbol1, symbol2] pairs
    timeframes: List[str] = ["1H"]
    calculation_mode: str = "rsi_threshold"  # "rsi_threshold" or "real_correlation"
    rsi_period: int = 14
    rsi_overbought_threshold: int = 70
    rsi_oversold_threshold: int = 30
    correlation_window: int = 50
    alert_conditions: List[str]
    strong_correlation_threshold: float = 0.70
    moderate_correlation_threshold: float = 0.30
    weak_correlation_threshold: float = 0.15
    notification_methods: List[str] = ["email"]
    alert_frequency: str = "once"
    
    # Pydantic v2 config
    model_config = {
        "populate_by_name": True
    }


class RSICorrelationAlertResponse(BaseModel):
    id: str
    alert_name: str
    user_id: Optional[str] = None
    user_email: str
    pairs: List[List[str]] = Field(alias="correlation_pairs")
    timeframes: List[str]
    calculation_mode: str
    rsi_period: int
    rsi_overbought_threshold: int
    rsi_oversold_threshold: int
    correlation_window: int
    alert_conditions: List[str]
    strong_correlation_threshold: float
    moderate_correlation_threshold: float
    weak_correlation_threshold: float
    notification_methods: List[str]
    alert_frequency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    # Pydantic v2 config
    model_config = {
        "populate_by_name": True
    }


class HeatmapAlertTrigger(BaseModel):
    alert_id: str
    alert_name: str
    user_email: str
    triggered_pairs: List[Dict[str, Any]]
    trigger_time: datetime
    alert_config: Dict[str, Any]


