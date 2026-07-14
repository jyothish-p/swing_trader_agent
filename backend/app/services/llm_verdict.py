"""Optional LLM-powered one-line verdict generation."""
import json
import logging
import time
from typing import Any

from app.config import (
    GEMINI_API_KEY,
    GEMINI_VERDICTS_ENABLED,
    GEMINI_VERDICTS_MAX_OUTPUT_TOKENS,
    GEMINI_VERDICTS_MODEL,
    LLM_VERDICTS_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_VERDICTS_ENABLED,
    OPENAI_VERDICTS_MAX_OUTPUT_TOKENS,
    OPENAI_VERDICTS_MODEL,
    OPENAI_VERDICTS_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
except Exception:  # pragma: no cover - optional dependency
    genai = None

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None

_gemini_client = None
_openai_client = None


def _gemini_available() -> bool:
    return bool(GEMINI_VERDICTS_ENABLED and GEMINI_API_KEY and genai is not None)


def _openai_available() -> bool:
    return bool(OPENAI_VERDICTS_ENABLED and OPENAI_API_KEY and OpenAI is not None)


def _preferred_provider() -> str:
    provider = (LLM_VERDICTS_PROVIDER or "auto").strip().lower()
    if provider in {"gemini", "openai", "auto"}:
        return provider
    return "auto"


def _active_provider() -> str | None:
    provider = _preferred_provider()
    if provider == "gemini":
        return "gemini" if _gemini_available() else None
    if provider == "openai":
        return "openai" if _openai_available() else None
    if _gemini_available():
        return "gemini"
    if _openai_available():
        return "openai"
    return None


def llm_verdicts_available() -> bool:
    return _active_provider() is not None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None and _gemini_available():
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None and _openai_available():
        _openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_VERDICTS_TIMEOUT_SEC)
    return _openai_client


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", "") or ""
    if text:
        return text.strip()

    output = getattr(response, "output", None) or []
    for item in output:
        for content in getattr(item, "content", None) or []:
            content_text = getattr(content, "text", None)
            if content_text:
                return str(content_text).strip()
    return ""


def _gemini_text(response: Any) -> str:
    text = getattr(response, "text", "") or ""
    if text:
        return str(text).strip()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text).strip()
    return ""


def _build_payload(
    symbol: str,
    raw: dict,
    titan: dict,
    swing_ai: dict,
    king: dict,
    backtest: dict,
    composite: dict,
    trade_plans: dict,
    fallback_verdict: str,
) -> dict[str, Any]:
    scanner_plan = trade_plans.get("scanner_plan", {})
    retest_zone = scanner_plan.get("entry_retest_zone") or []

    return {
        "symbol": symbol,
        "seed_verdict": fallback_verdict,
        "composite": {
            "score": composite.get("composite_score"),
            "probability": composite.get("composite_probability"),
            "verdict": composite.get("consensus_verdict"),
            "agreement": composite.get("agreement"),
        },
        "scanner_action": scanner_plan.get("action"),
        "levels": {
            "cmp": raw.get("cmp"),
            "trigger": raw.get("trigger"),
            "stop_loss": raw.get("invalidation"),
            "retest_zone": retest_zone if len(retest_zone) == 2 else None,
        },
        "setup": {
            "pattern": raw.get("pattern_name"),
            "daily_bias": raw.get("daily_bias"),
            "weekly_structure": raw.get("weekly_structure"),
            "phase": raw.get("phase"),
            "overhead_supply": raw.get("overhead_supply"),
            "sl_pct": raw.get("sl_pct"),
            "rsi": raw.get("rsi"),
            "vol_ratio": raw.get("vol_ratio"),
            "delivery_proxy": raw.get("delivery_proxy"),
            "delivery_trend": raw.get("delivery_trend"),
            "upper_wick_heavy": raw.get("upper_wick_heavy"),
        },
        "sector_context": {
            "sector_index": raw.get("sector_index"),
            "sector_momentum_score": raw.get("sector_momentum_score"),
            "sector_weekly_rsi": raw.get("sector_weekly_rsi"),
            "sector_structure": raw.get("sector_structure"),
            "sector_positive_peers": raw.get("sector_positive_peers"),
            "sector_peer_avg_perf_1m": raw.get("sector_peer_avg_perf_1m"),
        },
        "sentiment": {
            "news_tone": raw.get("news_tone"),
            "market_mood": raw.get("market_mood"),
            "retail_psych": raw.get("retail_psych"),
        },
        "model_verdicts": {
            "titan": titan.get("verdict"),
            "swing_ai": swing_ai.get("verdict"),
            "king": king.get("verdict"),
            "backtest": backtest.get("verdict"),
        },
        "backtest": {
            "score": backtest.get("scanner_score"),
            "quality_grade": backtest.get("quality_grade"),
            "data_status": backtest.get("data_status"),
            "sample_size": backtest.get("sample_size"),
            "metrics": backtest.get("metrics"),
        },
    }


def _generate_gemini_one_line_verdict(
    symbol: str,
    payload: dict[str, Any],
    fallback_verdict: str,
) -> tuple[str, str]:
    client = _get_gemini_client()
    if client is None:
        return fallback_verdict, "rules"

    prompt = (
        "You write a single-line swing-trading verdict for Indian NSE stocks. "
        "Use only the supplied JSON facts. Keep it to one sentence, under 45 words, no markdown, no bullet points, no disclaimers. "
        "Mention the stock symbol, setup status, the biggest caution if any, and the best immediate action. "
        "Use rupee levels only if they are already provided. Do not invent indicators, prices, targets, or news.\n\n"
        "JSON facts:\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=GEMINI_VERDICTS_MODEL,
                contents=prompt,
            )
            verdict = _gemini_text(response)
            if verdict:
                return " ".join(verdict.split()), "gemini"
        except Exception as exc:  # pragma: no cover - network / auth dependent
            last_error = exc
            message = str(exc)
            transient = any(token in message for token in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"))
            if transient and attempt < 3:
                time.sleep(attempt * 2)
                continue
            break

    if last_error is not None:
        logger.warning("Gemini verdict generation failed for %s: %s", symbol, last_error)

    return fallback_verdict, "rules"


def _generate_openai_one_line_verdict(
    symbol: str,
    payload: dict[str, Any],
    fallback_verdict: str,
) -> tuple[str, str]:
    client = _get_openai_client()
    if client is None:
        return fallback_verdict, "rules"

    developer_prompt = (
        "You write a single-line swing-trading verdict for Indian NSE stocks. "
        "Use only the supplied facts. Keep it to one sentence, under 45 words, no markdown, no bullet points, no disclaimers. "
        "Mention the stock symbol, setup status, the most important caution if any, and the best immediate action. "
        "Use rupee levels only if they are already provided. Do not invent indicators, prices, targets, or news."
    )
    user_prompt = (
        "Rewrite the seeded verdict into a cleaner one-line verdict using the JSON facts below. "
        "If the seed and JSON disagree, trust the JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    try:
        response = client.responses.create(
            model=OPENAI_VERDICTS_MODEL,
            input=[
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": developer_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            max_output_tokens=OPENAI_VERDICTS_MAX_OUTPUT_TOKENS,
        )
        verdict = _response_text(response)
        if verdict:
            return " ".join(verdict.split()), "openai"
    except Exception as exc:  # pragma: no cover - network / auth dependent
        logger.warning("OpenAI verdict generation failed for %s: %s", symbol, exc)

    return fallback_verdict, "rules"


def generate_llm_one_line_verdict(
    symbol: str,
    raw: dict,
    titan: dict,
    swing_ai: dict,
    king: dict,
    backtest: dict,
    composite: dict,
    trade_plans: dict,
    fallback_verdict: str,
) -> tuple[str, str]:
    provider = _active_provider()
    if provider is None:
        return fallback_verdict, "rules"

    payload = _build_payload(symbol, raw, titan, swing_ai, king, backtest, composite, trade_plans, fallback_verdict)
    if provider == "gemini":
        return _generate_gemini_one_line_verdict(symbol, payload, fallback_verdict)
    if provider == "openai":
        return _generate_openai_one_line_verdict(symbol, payload, fallback_verdict)
    return fallback_verdict, "rules"
