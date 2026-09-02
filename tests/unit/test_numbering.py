import pytest

from app.scenes import _base_number, _next_suffix

pytestmark = pytest.mark.epic(6)


class TestBaseNumber:
    async def test_plain_number(self):
        assert _base_number("2") == "2"

    async def test_suffixed_number(self):
        assert _base_number("2A") == "2"

    async def test_multi_digit_suffix(self):
        assert _base_number("10B") == "10"

    async def test_none_or_empty(self):
        assert _base_number(None) == "1"
        assert _base_number("") == "1"

    async def test_letters_only_falls_back(self):
        assert _base_number("ABC") == "1"


class TestNextSuffix:
    async def test_first_suffix(self):
        assert _next_suffix(set()) == "A"

    async def test_next_letter(self):
        assert _next_suffix({"A"}) == "B"
        assert _next_suffix({"A", "B"}) == "C"

    async def test_lowercase_input(self):
        assert _next_suffix({"a"}) == "A"