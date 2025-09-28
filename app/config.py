import os

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

# Supabase configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh5YWp3aHRrd2xkcm1saGZpdXdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NjI5NjUzNCwiZXhwIjoyMDcxODcyNTM0fQ.UDqYHY5Io0o-fQTswCYQmMdC6UCPQI2gf3aTb9o09SE")

# Filesystem-backed news cache
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
NEWS_CACHE_FILE = os.environ.get("NEWS_CACHE_FILE", os.path.join(BASE_DIR, "news_cache.json"))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# Email service configuration
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "alerts@fxlabs.ai")
FROM_NAME = os.environ.get("FROM_NAME", "FX Labs Alerts")

# Public URL (for links in emails)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

# Daily brief schedule (timezone and local send time)
# Example:
#   DAILY_TZ_NAME=Asia/Kolkata
#   DAILY_SEND_LOCAL_TIME=09:00
DAILY_TZ_NAME = os.environ.get("DAILY_TZ_NAME", "Asia/Kolkata")
DAILY_SEND_LOCAL_TIME = os.environ.get("DAILY_SEND_LOCAL_TIME", "09:00")
