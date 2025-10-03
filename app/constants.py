from typing import List


# Fixed, broker-suffixed symbols used for all RSI-based trackers (MT5 compatibility)
RSI_SUPPORTED_SYMBOLS: List[str] = [
    # Majors (7)
    "EURUSDm", "GBPUSDm", "USDJPYm", "USDCHFm", "AUDUSDm", "USDCADm", "NZDUSDm",
    # EUR crosses (6)
    "EURGBPm", "EURJPYm", "EURCHFm", "EURAUDm", "EURCADm", "EURNZDm",
    # GBP crosses (5)
    "GBPJPYm", "GBPCHFm", "GBPAUDm", "GBPCADm", "GBPNZDm",
    # AUD crosses (4)
    "AUDJPYm", "AUDCHFm", "AUDCADm", "AUDNZDm",
    # NZD crosses (3)
    "NZDJPYm", "NZDCHFm", "NZDCADm",
    # CAD crosses (2)
    "CADJPYm", "CADCHFm",
    # CHF crosses (1)
    "CHFJPYm",
    # Precious metals (2)
    "XAUUSDm", "XAGUSDm",
    # Crypto (2)
    "BTCUSDm", "ETHUSDm",
]


# Fixed set of correlation pair keys monitored by the RSI Correlation dashboard
# Pair key format: "SYMBOL_A_SYMBOL_B" using broker-suffixed symbols
RSI_CORRELATION_PAIR_KEYS: List[str] = [
    # Positive correlations (10)
    "EURUSDm_GBPUSDm",
    "EURUSDm_AUDUSDm",
    "EURUSDm_NZDUSDm",
    "GBPUSDm_AUDUSDm",
    "AUDUSDm_NZDUSDm",
    "USDCHFm_USDJPYm",
    "XAUUSDm_XAGUSDm",
    "XAUUSDm_EURUSDm",
    "BTCUSDm_ETHUSDm",
    "BTCUSDm_XAUUSDm",
    # Negative correlations (7)
    "EURUSDm_USDCHFm",
    "GBPUSDm_USDCHFm",
    "USDJPYm_EURUSDm",
    "USDJPYm_GBPUSDm",
    "USDCADm_AUDUSDm",
    "USDCHFm_AUDUSDm",
    "XAUUSDm_USDJPYm",
]


# Sign classification for correlation pairs as per Calculations Reference
# Values: 'positive' or 'negative'
RSI_CORRELATION_PAIR_SIGNS = {
    # Negative pairs
    "EURUSDm_USDCHFm": "negative",
    "GBPUSDm_USDCHFm": "negative",
    "USDJPYm_EURUSDm": "negative",
    "USDJPYm_GBPUSDm": "negative",
    "USDCADm_AUDUSDm": "negative",
    "USDCHFm_AUDUSDm": "negative",
    "XAUUSDm_USDJPYm": "negative",
    # Positive pairs
    "EURUSDm_GBPUSDm": "positive",
    "EURUSDm_AUDUSDm": "positive",
    "EURUSDm_NZDUSDm": "positive",
    "GBPUSDm_AUDUSDm": "positive",
    "AUDUSDm_NZDUSDm": "positive",
    "USDCHFm_USDJPYm": "positive",
    "XAUUSDm_XAGUSDm": "positive",
    "XAUUSDm_EURUSDm": "positive",
    "BTCUSDm_ETHUSDm": "positive",
    "BTCUSDm_XAUUSDm": "positive",
}

# Fixed correlation window for Real Correlation mode across the system
RSI_CORRELATION_WINDOW: int = 50

