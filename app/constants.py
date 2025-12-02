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
    # Precious metals & commodities (3)
    "XAUUSDm", "XAGUSDm", "USOILm",
    # Crypto (2)
    "BTCUSDm", "ETHUSDm",
]


# Correlation pairs and window removed per product decision
