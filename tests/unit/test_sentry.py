import pytest

from app import sentry


class TestInitSentry:
    def test_noop_without_dsn(self, monkeypatch):
        called = []
        monkeypatch.setattr(sentry.sentry_sdk, "init", lambda **kw: called.append(kw))

        settings = type("Settings", (), {"sentry_dsn": ""})()
        sentry.init_sentry(settings)
        assert called == []

    def test_init_with_dsn(self, monkeypatch):
        called = []
        monkeypatch.setattr(sentry.sentry_sdk, "init", lambda **kw: called.append(kw))

        settings = type("Settings", (), {"sentry_dsn": "https://key@host/id"})()
        sentry.init_sentry(settings)
        assert called
        assert called[0]["dsn"] == "https://key@host/id"
        assert called[0]["traces_sample_rate"] == 0.1
        assert called[0]["send_default_pii"] is False


class TestBreadcrumb:
    def test_scrubs_secrets(self, monkeypatch):
        seen = {}

        def add_breadcrumb(**kw):
            seen.update(kw)

        monkeypatch.setattr(sentry.sentry_sdk, "add_breadcrumb", add_breadcrumb)
        sentry.breadcrumb(
            "db",
            "write",
            password="hunter2",
            csrf_secret="abc",
            session_id="s1",
            token="t",
            authorization="Bearer x",
            duration_ms=4.2,
            query="SELECT 1",
        )
        data = seen["data"]
        assert "password" not in data
        assert "csrf_secret" not in data
        assert "session_id" not in data
        assert "token" not in data
        assert "authorization" not in data
        assert data["duration_ms"] == 4.2

    def test_drops_non_scalar_and_nested_values(self, monkeypatch):
        seen = {}

        def add_breadcrumb(**kw):
            seen.update(kw)

        monkeypatch.setattr(sentry.sentry_sdk, "add_breadcrumb", add_breadcrumb)
        sentry.breadcrumb("ai", "call", payload={"nested": True}, count=3)
        assert seen["data"] == {"count": 3}


class TestScrubSafe:
    @pytest.mark.parametrize(
        ("key", "value", "expected"),
        [
            ("password", "x", False),
            ("csrf_secret", "x", False),
            ("session_id", "x", False),
            ("token", "x", False),
            ("authorization", "x", False),
            ("api_secret_key", "x", False),
            ("display_name", "Alice", True),
            ("page_count", 12, True),
            ("ratio", 0.5, True),
            ("enabled", True, True),
            ("nullable", None, True),
            ("meta", ["a"], False),
            ("obj", {"k": 1}, False),
        ],
    )
    def test_scrub_rules(self, key, value, expected):
        assert sentry._is_scrub_safe(key, value) is expected


class TestScopedCalls:
    def test_record_db_call_truncates_query(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sentry.sentry_sdk, "add_breadcrumb", lambda **kw: seen.update(kw))
        sentry.record_db_call("SELECT " + "x" * 1000, 12.3456)
        assert len(seen["data"]["query"]) == 500
        assert seen["data"]["duration_ms"] == 12.346
        assert seen["category"] == "db"

    def test_record_ai_call_truncates_summary(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sentry.sentry_sdk, "add_breadcrumb", lambda **kw: seen.update(kw))
        sentry.record_ai_call("S" * 1000, "claude-3", 0.0042, 1200, 55)
        assert len(seen["data"]["prompt_summary"]) == 500
        assert seen["data"]["cost"] == 0.0042
        assert seen["data"]["input_tokens"] == 1200
        assert seen["data"]["output_tokens"] == 55

    def test_record_ai_result(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sentry.sentry_sdk, "add_breadcrumb", lambda **kw: seen.update(kw))
        sentry.record_ai_result({"suggestion": "x"})
        assert seen["data"]["result"] == '{"suggestion": "x"}'

    def test_record_ai_result_long_string_truncated(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sentry.sentry_sdk, "add_breadcrumb", lambda **kw: seen.update(kw))
        sentry.record_ai_result("x" * 1000)
        assert len(seen["data"]["result"]) == 500

    def test_capture_validation_failure(self, monkeypatch):
        captured = []
        monkeypatch.setattr(sentry.sentry_sdk, "capture_exception", captured.append)
        exc = ValueError("nope")
        sentry.capture_validation_failure(exc)
        assert captured == [exc]