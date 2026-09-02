import json

import sentry_sdk


def init_sentry(settings) -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        send_default_pii=False,
    )


def capture_validation_failure(exc) -> None:
    sentry_sdk.capture_exception(exc)


def breadcrumb(category: str, message: str, **data) -> None:
    safe_data = {k: v for k, v in data.items() if _is_scrub_safe(k, v)}
    sentry_sdk.add_breadcrumb(
        category=category,
        message=message,
        data=safe_data,
    )


def record_db_call(query: str, duration_ms: float) -> None:
    breadcrumb("db", "database call", query=query[:500], duration_ms=round(duration_ms, 3))


def record_http_request(method: str, path: str, status: int, duration_ms: float) -> None:
    breadcrumb(
        "http",
        "request",
        method=method,
        path=path,
        status=status,
        duration_ms=duration_ms,
    )


def record_ai_call(summary: str, model: str, cost: float, tokens_in: int, tokens_out: int) -> None:
    breadcrumb(
        "ai",
        "ai call",
        prompt_summary=summary[:500],
        model=model,
        cost=cost,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )


def record_ai_result(result) -> None:
    if isinstance(result, str):
        summarized = result[:500]
    else:
        summarized = json.dumps(result, default=str)[:500]
    breadcrumb("ai", "ai result", result=summarized)


_SCRUBBED = {"password", "csrf_secret", "session_id", "token", "authorization"}


def _is_scrub_safe(key: str, value) -> bool:
    if key.lower() in _SCRUBBED or any(s in key.lower() for s in ("secret", "password")):
        return False
    return isinstance(value, (str, int, float, bool)) or value is None