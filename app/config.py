import os
from .tenancy import get_tenant_config

# Auto-load .env if present (non-intrusive)
try:
    from dotenv import load_dotenv, find_dotenv
    # Load the closest .env without overriding existing environment
    load_dotenv(find_dotenv(), override=False)
except Exception:
    # If python-dotenv is unavailable, continue without failing
    pass

# Environment-driven configuration (no changes to names/semantics)
API_TOKEN = os.environ.get("API_TOKEN", "")
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "")

# News analysis configuration
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "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")
JBLANKED_API_URL = os.environ.get("JBLANKED_API_URL", "https://www.jblanked.com/news/api/forex-factory/calendar/week/")
JBLANKED_API_KEY = os.environ.get("JBLANKED_API_KEY", "OZaABMUo")
NEWS_UPDATE_INTERVAL_HOURS = int(os.environ.get("NEWS_UPDATE_INTERVAL_HOURS", "1"))
NEWS_CACHE_MAX_ITEMS = int(os.environ.get("NEWS_CACHE_MAX_ITEMS", "500"))

# Supabase configuration (tenant-aware)
_ten = get_tenant_config()
SUPABASE_URL = _ten.supabase_url
SUPABASE_SERVICE_KEY = _ten.supabase_service_key

# Filesystem-backed news cache
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
NEWS_CACHE_FILE = os.environ.get("NEWS_CACHE_FILE", os.path.join(BASE_DIR, "news_cache.json"))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# Email service configuration (tenant-aware)
SENDGRID_API_KEY = _ten.sendgrid_api_key
FROM_EMAIL = _ten.from_email
FROM_NAME = _ten.from_name

# Public URL (for links in emails)
PUBLIC_BASE_URL = _ten.public_base_url

# Daily brief schedule (timezone and local send time)
DAILY_TZ_NAME = _ten.daily_tz_name
DAILY_SEND_LOCAL_TIME = _ten.daily_send_local_time
