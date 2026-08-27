"""Config loading from .env. Single source of truth for settings -- everything
else imports `settings` from here rather than reading os.environ directly."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("intel_brief.config")

load_dotenv()


def _int(name, default):
    return int(os.environ.get(name, default))


def _csv(name: str) -> tuple[str, ...]:
    """Comma-separated env var -> tuple, or () when unset or blank.

    Empty rather than None so callers can write `settings.x or DEFAULT` and
    get the default for both "not set" and "set to an empty string".
    """
    raw = os.environ.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    user_locality: str
    user_region: str
    user_country: str
    user_country_code: str
    user_timezone: str

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_classify_model: str
    llm_max_concurrent: int
    llm_timeout_seconds: int
    llm_unload_url: str

    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str

    db_path: Path
    # Separate file from db_path on purpose -- see markets/store.py for why.
    markets_db_path: Path
    markets_ingest_interval_hours: int
    markets_llm_time_budget_seconds: int
    dashboard_host: str
    dashboard_port: int
    fetch_user_agent: str
    fetch_delay_seconds: int
    pipeline_run_time: str
    analyze_time_budget_seconds: int
    stale_article_hours: int
    archive_start_date: str
    backfill_time_budget_seconds: int
    gpu_guard: str
    gpu_pause_check_interval_seconds: int
    gpu_pause_max_total_seconds: int

    # Geography, from LOCAL_PLACES/COUNTRY_TERMS/STRONG_COUNTRY_TERMS in .env.
    # No default: an unset LOCAL_PLACES means nothing is local rather than
    # quietly picking a region for the operator. See geo.py and uk_regions.py.
    local_places: tuple[str, ...]
    country_terms: tuple[str, ...]
    strong_country_terms: tuple[str, ...]

    # Which crypto assets get a price instrument on the Markets pages.
    crypto_assets: tuple[str, ...]

    feeds_path: Path
    data_dir: Path
    logs_dir: Path


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DB_PATH", "./data/intel.db")).resolve().parent
    return Settings(
        user_locality=os.environ["USER_LOCALITY"],
        user_region=os.environ["USER_REGION"],
        user_country=os.environ["USER_COUNTRY"],
        user_country_code=os.environ["USER_COUNTRY_CODE"],
        user_timezone=os.environ["USER_TIMEZONE"],
        # Optional on purpose: an empty LLM_BASE_URL runs intel-brief as a
        # reader and archive with no interpretation layer (see ai.py). Note
        # the empty-string default rather than None -- passing None to the
        # OpenAI client silently defaults it to api.openai.com, and an
        # unconfigured install must never call out to a hosted API.
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        # "auto" derives llama-swap's /unload from LLM_BASE_URL; a URL is
        # used as given; empty (the default) means the server has no such
        # call and pausing just stops us issuing requests.
        llm_unload_url=os.environ.get("LLM_UNLOAD_URL", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_classify_model=os.environ.get("LLM_CLASSIFY_MODEL", ""),
        llm_max_concurrent=_int("LLM_MAX_CONCURRENT", 2),
        llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 180),
        embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", ""),
        embedding_api_key=os.environ.get("EMBEDDING_API_KEY", ""),
        embedding_model=os.environ.get("EMBEDDING_MODEL", ""),
        db_path=Path(os.environ.get("DB_PATH", "./data/intel.db")).resolve(),
        markets_db_path=Path(
            os.environ.get("MARKETS_DB_PATH", str(data_dir / "markets.db"))
        ).resolve(),
        markets_ingest_interval_hours=_int("MARKETS_INGEST_INTERVAL_HOURS", 6),
        # Own budget so the market stages can't run away the way analyze()
        # once did (5.8 hours unbounded). 15 min covers 4 tab briefs plus
        # the correlation pass with room to spare.
        markets_llm_time_budget_seconds=_int("MARKETS_LLM_TIME_BUDGET_SECONDS", 900),
        dashboard_host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=_int("DASHBOARD_PORT", 8300),
        # The comment must carry a URL. FRED's edge silently drops connections
        # from a bare product token -- "PersonalIntelBrief/1.0" timed out on
        # every request while curl's default UA got a 200, which left every
        # FRED-backed instrument (the whole Monetary System tab, plus Brent,
        # natural gas and the HY spread) collecting nothing at all. A UA with
        # a "+https://" contact URL is accepted; prose in the comment is not.
        fetch_user_agent=os.environ.get(
            "FETCH_USER_AGENT", "PersonalIntelBrief/1.0 (+https://example.org)"
        ),
        fetch_delay_seconds=_int("FETCH_DELAY_SECONDS", 2),
        pipeline_run_time=os.environ.get("PIPELINE_RUN_TIME", "05:30"),
        analyze_time_budget_seconds=_int("ANALYZE_TIME_BUDGET_SECONDS", 3600),
        stale_article_hours=_int("STALE_ARTICLE_HOURS", 48),
        # The date the archive is meant to be complete from. Articles before
        # it are kept and searchable, they're just not counted as gaps to fill.
        # Set this to the first date you actually expect coverage from.
        # Anything lost before the archive was running properly -- a boot-time
        # network window, a service that was not yet installed -- can never be
        # recovered from RSS, so counting those days as gaps just means a
        # backfill that retries forever and a Status page that never goes
        # green.
        archive_start_date=os.environ.get("ARCHIVE_START_DATE", "2026-08-01"),
        backfill_time_budget_seconds=_int("BACKFILL_TIME_BUDGET_SECONDS", 1800),
        gpu_guard=os.environ.get("GPU_GUARD", "auto"),
        gpu_pause_check_interval_seconds=_int("GPU_PAUSE_CHECK_INTERVAL_SECONDS", 120),
        gpu_pause_max_total_seconds=_int("GPU_PAUSE_MAX_TOTAL_SECONDS", 14400),
        local_places=_csv("LOCAL_PLACES"),
        country_terms=_csv("COUNTRY_TERMS"),
        strong_country_terms=_csv("STRONG_COUNTRY_TERMS"),
        # BTC only by default. Every asset added here is a statement about what
        # the person running this holds an interest in, and a shipped default
        # should not make that statement on their behalf -- XMR in particular
        # is opt-in for exactly that reason. See registry.CRYPTO_PRICES.
        # Presence matters, not just content: unset means the BTC default,
        # while CRYPTO_ASSETS="" is an explicit "none" that removes the tab.
        crypto_assets=(
            _csv("CRYPTO_ASSETS") if "CRYPTO_ASSETS" in os.environ else ("BTC",)
        ),
        feeds_path=Path(os.environ.get("FEEDS_PATH", "./feeds.yaml")).resolve(),
        data_dir=data_dir,
        logs_dir=data_dir / "logs",
    )


def _check_user_agent(ua: str) -> None:
    """Warn when FETCH_USER_AGENT carries no contact URL.

    Not a style preference. FRED's edge silently drops connections from a bare
    product token: every request hangs until it times out, while the identical
    request with a "+https://" URL in the UA returns 200 in under a second. It
    fails as a timeout rather than a refusal, so it looks like a network fault
    from every angle except this one.

    This cost six days of silent collection loss. `.env.example` and the
    default below both carry the URL, but an existing .env overrides both --
    install.sh never overwrites one, by design -- so a deployment can inherit a
    bare token from an older install and nothing anywhere says so. Hence a
    warning at load rather than a comment nobody reads.

    A warning, not an error: this is one provider's edge behaviour, and it must
    never be the reason the service refuses to start.
    """
    if "+http" not in ua:
        log.warning(
            "FETCH_USER_AGENT=%r has no contact URL. FRED will time out on every "
            "request until this includes one, e.g. "
            '"PersonalIntelBrief/1.0 (+https://example.org)"', ua,
        )


settings = load_settings()
_check_user_agent(settings.fetch_user_agent)
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
