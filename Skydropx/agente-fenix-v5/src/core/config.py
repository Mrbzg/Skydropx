"""Carga de configuración desde .env. Zero deps externas."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_load_dotenv(_PROJECT_ROOT / ".env")


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- APIs principales ---
    denue_token: str | None = field(default_factory=lambda: os.environ.get("DENUE_TOKEN"))

    # --- Search backends (tiered) ---
    serper_api_key: str | None = field(default_factory=lambda: os.environ.get("SERPER_API_KEY"))
    serper_strategy: str = field(default_factory=lambda: os.environ.get("SERPER_STRATEGY", "reserve"))
    serper_stop_when_paid: bool = field(default_factory=lambda: _bool("SERPER_STOP_WHEN_PAID", "true"))
    searxng_url: str | None = field(default_factory=lambda: os.environ.get("SEARXNG_URL"))
    openserp_url: str | None = field(default_factory=lambda: os.environ.get("OPENSERP_URL"))
    openserp_use_proxies: bool = field(default_factory=lambda: _bool("OPENSERP_USE_PROXIES", "false"))
    search_mode: str = field(default_factory=lambda: os.environ.get("SEARCH_MODE", "cascade"))
    search_avoid_paid: bool = field(default_factory=lambda: _bool("SEARCH_AVOID_PAID", "true"))

    # --- Supabase (cloud DB opcional para dual mode) ---
    supabase_url: str | None = field(default_factory=lambda: os.environ.get("SUPABASE_URL"))
    supabase_key: str | None = field(default_factory=lambda: os.environ.get("SUPABASE_KEY"))
    supabase_auto_sync: bool = field(default_factory=lambda: _bool("SUPABASE_AUTO_SYNC", "false"))

    # --- DB ---
    sqlite_path: str = field(default_factory=lambda: os.environ.get("SQLITE_PATH", "data/fenix.sqlite"))
    database_url: str | None = field(default_factory=lambda: os.environ.get("DATABASE_URL"))

    # --- Output ---
    output_dir: str = field(default_factory=lambda: os.environ.get("OUTPUT_DIR", "output"))
    export_format: str = field(default_factory=lambda: os.environ.get("EXPORT_DEFAULT_FORMAT", "both"))

    # --- Modo ---
    fenix_mode: str = field(default_factory=lambda: os.environ.get("FENIX_MODE", "sin-ia"))
    ai_parser_enabled: bool = field(default_factory=lambda: _bool("AI_PARSER_ENABLED", "false"))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    # --- Mercado Libre ---
    ml_app_id: str | None = field(default_factory=lambda: os.environ.get("ML_APP_ID"))
    ml_client_secret: str | None = field(default_factory=lambda: os.environ.get("ML_CLIENT_SECRET"))

    # --- Compliance ---
    respect_robots_txt: bool = field(default_factory=lambda: _bool("RESPECT_ROBOTS_TXT", "true"))
    user_agent: str = field(default_factory=lambda: os.environ.get(
        "USER_AGENT", "AgenteFenix/5.0 (research; +https://skydropx.com)"
    ))
    rotate_user_agents: bool = field(default_factory=lambda: _bool("ROTATE_USER_AGENTS", "true"))
    max_rps: int = field(default_factory=lambda: int(os.environ.get("MAX_REQUESTS_PER_SECOND", "2")))

    # --- CRM ---
    hubspot_api_key: str | None = field(default_factory=lambda: os.environ.get("HUBSPOT_API_KEY"))
    google_sheets_creds: str | None = field(default_factory=lambda: os.environ.get("GOOGLE_SHEETS_CREDS"))

    # --- Proxies ---
    http_proxy: str | None = field(default_factory=lambda: os.environ.get("HTTP_PROXY"))
    https_proxy: str | None = field(default_factory=lambda: os.environ.get("HTTPS_PROXY"))
    use_free_proxies: bool = field(default_factory=lambda: _bool("USE_FREE_PROXIES", "false"))

    # ---- Helpers ----
    def has_denue(self) -> bool: return bool(self.denue_token)
    def has_searxng(self) -> bool: return bool(self.searxng_url)
    def has_supabase(self) -> bool: return bool(self.supabase_url and self.supabase_key)
    def has_serper(self) -> bool: return bool(self.serper_api_key)
    def has_openserp(self) -> bool: return bool(self.openserp_url)
    def has_proxies(self) -> bool: return bool(self.http_proxy or self.https_proxy)

    def search_backends_configured(self) -> dict[str, bool]:
        return {
            "serper": self.has_serper(),
            "searxng": self.has_searxng(),
            "openserp": self.has_openserp(),
            "ddg": True,  # siempre disponible
        }

    def summary(self) -> dict:
        return {
            "denue_token": self.has_denue(),
            "search_backends": self.search_backends_configured(),
            "search_mode": self.search_mode,
            "search_avoid_paid": self.search_avoid_paid,
            "proxies": self.has_proxies(),
            "use_free_proxies": self.use_free_proxies,
            "sqlite_path": self.sqlite_path,
            "output_dir": self.output_dir,
            "fenix_mode": self.fenix_mode,
            "ai_parser_enabled": self.ai_parser_enabled,
            "rotate_user_agents": self.rotate_user_agents,
            "ml_oauth": bool(self.ml_app_id and self.ml_client_secret),
            "hubspot": bool(self.hubspot_api_key),
        }


settings = Settings()


__all__ = ["settings", "Settings"]
