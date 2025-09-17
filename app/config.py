import os

# Environment-driven configuration (no changes to names/semantics)
API_TOKEN = os.environ.get("API_TOKEN", "")
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "")

# News analysis configuration
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "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")
JBLANKED_API_URL = os.environ.get("JBLANKED_API_URL", "https://www.jblanked.com/news/api/forex-factory/calendar/week/")
JBLANKED_API_KEY = os.environ.get("JBLANKED_API_KEY", "32FEvnEZ")
NEWS_UPDATE_INTERVAL_HOURS = int(os.environ.get("NEWS_UPDATE_INTERVAL_HOURS", "24"))
NEWS_CACHE_MAX_ITEMS = int(os.environ.get("NEWS_CACHE_MAX_ITEMS", "100"))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))


