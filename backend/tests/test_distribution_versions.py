import pytest

from distribution.versions import newest, parse


def test_parse_and_compare():
    assert parse("0.1.0") == (0, 1, 0)
    assert parse("v0.1.0") == (0, 1, 0)
    assert parse("0.10.0") > parse("0.9.9")


@pytest.mark.parametrize("text", ["", "abc", "1.2", "1.2.3.4", "1.2.x", "v"])
def test_parse_garbage_is_none(text):
    assert parse(text) is None


def test_newest():
    assert newest(["0.9.9", "0.10.0", "junk", "v0.2.0"]) == "0.10.0"
    assert newest(["junk"]) is None
    assert newest([]) is None
