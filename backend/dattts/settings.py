"""Cấu hình DatTTS đọc từ biến môi trường (không có trạng thái toàn cục)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

__all__ = ["Settings", "DEFAULT_ALLOWED_ORIGINS"]

DEFAULT_ALLOWED_ORIGINS = (
    "https://dattts.com,https://www.dattts.com,http://localhost:8000,http://127.0.0.1:8000"
)


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _opt(value: str | None) -> str | None:
    return (value or "").strip() or None


def _default_web_dir() -> str | None:
    # backend/dattts/settings.py → <repo>/web
    p = Path(__file__).resolve().parents[2] / "web"
    return str(p) if p.is_dir() else None


@dataclass
class Settings:
    free_daily_chars: int = 20000
    sync_max_chars: int = 3000
    job_max_chars: int = 100000
    api_keys: frozenset[str] = field(default_factory=frozenset)
    admin_token: str | None = None
    trust_proxy: bool = False
    allowed_origins: list[str] = field(default_factory=lambda: _split(DEFAULT_ALLOWED_ORIGINS))
    web_dir: str | None = None
    model_dir: str = "./models"
    data_dir: str = "./.data"
    gpu: str | None = None
    # Job không có tiến triển trong khoảng này (giây) → coi như hỏng (máy chủ khởi động lại…)
    job_stale_seconds: int = 900
    # Job còn XẾP HÀNG (chưa chạy) lâu hơn khoảng này → coi như mất (máy chủ khởi động lại…).
    # Phải lớn hơn thời gian chạy của các job đứng trước (job 100.000 ký tự trên CPU ~ 1 giờ).
    job_queue_stale_seconds: int = 4 * 3600
    # Số proxy tin cậy đứng trước app (khi TRUST_PROXY=1): IP khách = phần tử thứ N tính TỪ PHẢI
    # của X-Forwarded-For (phần bên trái do khách tự gửi, không tin được).
    trust_proxy_hops: int = 1
    # ── Thanh toán qua Polar (SPEC Phần 2). Thiếu POLAR_ACCESS_TOKEN → billing tắt. ──
    polar_server: str = "sandbox"
    polar_api_base: str | None = None
    polar_access_token: str | None = None
    polar_organization_id: str | None = None
    polar_webhook_secret: str | None = None
    polar_product_personal: str | None = None
    polar_product_unlimited: str | None = None
    polar_benefit_personal: str | None = None
    polar_benefit_unlimited: str | None = None
    plan_personal_monthly_chars: int = 1_000_000
    plan_unlimited_monthly_chars: int = 20_000_000
    public_base_url: str = "https://dattts.com"

    @property
    def billing_enabled(self) -> bool:
        return bool(self.polar_access_token)

    @property
    def polar_base_url(self) -> str:
        if self.polar_api_base:
            return self.polar_api_base.rstrip("/")
        if (self.polar_server or "").strip().lower() == "production":
            return "https://api.polar.sh"
        return "https://sandbox-api.polar.sh"

    @property
    def public_base(self) -> str:
        return (self.public_base_url or "https://dattts.com").rstrip("/")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides) -> "Settings":
        e = os.environ if env is None else env
        s = cls(
            free_daily_chars=_int(e.get("FREE_DAILY_CHARS"), 20000),
            sync_max_chars=_int(e.get("SYNC_MAX_CHARS"), 3000),
            job_max_chars=_int(e.get("JOB_MAX_CHARS"), 100000),
            api_keys=frozenset(_split(e.get("API_KEYS"))),
            admin_token=(e.get("ADMIN_TOKEN") or "").strip() or None,
            trust_proxy=_bool(e.get("TRUST_PROXY")),
            allowed_origins=_split(e.get("ALLOWED_ORIGINS") or DEFAULT_ALLOWED_ORIGINS),
            web_dir=(e.get("WEB_DIR") or "").strip() or _default_web_dir(),
            model_dir=(e.get("DATTTS_MODEL_DIR") or "").strip() or "./models",
            data_dir=(e.get("DATTTS_DATA_DIR") or "").strip() or "./.data",
            gpu=(e.get("DATTTS_GPU") or "").strip() or None,
            job_stale_seconds=_int(e.get("JOB_STALE_SECONDS"), 900),
            job_queue_stale_seconds=_int(e.get("JOB_QUEUE_STALE_SECONDS"), 4 * 3600),
            trust_proxy_hops=max(1, _int(e.get("TRUST_PROXY_HOPS"), 1)),
            polar_server=(e.get("POLAR_SERVER") or "").strip().lower() or "sandbox",
            polar_api_base=_opt(e.get("POLAR_API_BASE")),
            polar_access_token=_opt(e.get("POLAR_ACCESS_TOKEN")),
            polar_organization_id=_opt(e.get("POLAR_ORGANIZATION_ID")),
            polar_webhook_secret=_opt(e.get("POLAR_WEBHOOK_SECRET")),
            polar_product_personal=_opt(e.get("POLAR_PRODUCT_PERSONAL")),
            polar_product_unlimited=_opt(e.get("POLAR_PRODUCT_UNLIMITED")),
            polar_benefit_personal=_opt(e.get("POLAR_BENEFIT_PERSONAL")),
            polar_benefit_unlimited=_opt(e.get("POLAR_BENEFIT_UNLIMITED")),
            plan_personal_monthly_chars=max(0, _int(e.get("PLAN_PERSONAL_MONTHLY_CHARS"), 1_000_000)),
            plan_unlimited_monthly_chars=max(0, _int(e.get("PLAN_UNLIMITED_MONTHLY_CHARS"), 20_000_000)),
            public_base_url=_opt(e.get("PUBLIC_BASE_URL")) or "https://dattts.com",
        )
        for k, v in overrides.items():
            if not hasattr(s, k):
                raise TypeError(f"unknown setting: {k}")
            setattr(s, k, v)
        return s
