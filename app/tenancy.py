import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TenantConfig:
    name: str
    supabase_url: str
    supabase_service_key: str
    sendgrid_api_key: str
    from_email: str
    from_name: str
    public_base_url: str
    daily_tz_name: str
    daily_send_local_time: str


def _get(env_key: str, default: str = "") -> str:
    return os.environ.get(env_key, default) or ""


def _resolve_tenant_from_env() -> str:
    # TENANT can be "FXLabs" or "HexTech"; default to FXLabs
    tenant = os.environ.get("TENANT", "FXLabs").strip()
    if not tenant:
        return "FXLabs"
    # Normalize casing to match our keys
    low = tenant.lower()
    if low in ("fx", "fxlabs", "fx-labs"):
        return "FXLabs"
    if low in ("hex", "hextech", "hex-tech"):
        return "HexTech"
    # Unknown -> treat as FXLabs to remain backward compatible
    return "FXLabs"


def load_tenant_config() -> TenantConfig:
    tenant = _resolve_tenant_from_env()

    if tenant == "FXLabs":
        return TenantConfig(
            name="FXLabs",
            supabase_url=_get("FXLABS_SUPABASE_URL", _get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")),
            supabase_service_key=_get("FXLABS_SUPABASE_SERVICE_KEY", _get("SUPABASE_SERVICE_KEY", "")),
            sendgrid_api_key=_get("FXLABS_SENDGRID_API_KEY", _get("SENDGRID_API_KEY", "")),
            from_email=_get("FXLABS_FROM_EMAIL", _get("FROM_EMAIL", "alerts@fxlabs.ai")),
            from_name=_get("FXLABS_FROM_NAME", _get("FROM_NAME", "FX Labs Alerts")),
            public_base_url=_get("FXLABS_PUBLIC_BASE_URL", _get("PUBLIC_BASE_URL", "")),
            daily_tz_name=_get("FXLABS_DAILY_TZ_NAME", _get("DAILY_TZ_NAME", "Asia/Kolkata")),
            daily_send_local_time=_get("FXLABS_DAILY_SEND_LOCAL_TIME", _get("DAILY_SEND_LOCAL_TIME", "09:00")),
        )

    # HexTech (placeholders if not provided)
    return TenantConfig(
        name="HexTech",
        supabase_url=_get("HEXTECH_SUPABASE_URL", _get("SUPABASE_URL", "")),
        supabase_service_key=_get("HEXTECH_SUPABASE_SERVICE_KEY", _get("SUPABASE_SERVICE_KEY", "")),
        sendgrid_api_key=_get("HEXTECH_SENDGRID_API_KEY", _get("SENDGRID_API_KEY", "")),
        from_email=_get("HEXTECH_FROM_EMAIL", _get("FROM_EMAIL", "")),  # TODO: set HexTech sender
        from_name=_get("HEXTECH_FROM_NAME", _get("FROM_NAME", "")),      # TODO: set HexTech display name
        public_base_url=_get("HEXTECH_PUBLIC_BASE_URL", _get("PUBLIC_BASE_URL", "")),
        daily_tz_name=_get("HEXTECH_DAILY_TZ_NAME", _get("DAILY_TZ_NAME", "Asia/Dubai")),
        daily_send_local_time=_get("HEXTECH_DAILY_SEND_LOCAL_TIME", _get("DAILY_SEND_LOCAL_TIME", "09:00")),
    )


# Singleton style accessor to avoid repeated env parsing
_TENANT_CONFIG: Optional[TenantConfig] = None


def get_tenant_config() -> TenantConfig:
    global _TENANT_CONFIG
    if _TENANT_CONFIG is None:
        _TENANT_CONFIG = load_tenant_config()
    return _TENANT_CONFIG


