"""Tests for match_name and contain_name utilities."""

from openjiuwen_search_base.codegraph.parser.resolver.passes._utils import contain_name, match_name


class TestMatchName:
    """Tests for match_name()."""

    def setup_method(self):
        match_name.cache_clear()

    @staticmethod
    def test_exact_short():
        assert match_name("SaveImg", "SaveImg") is True

    @staticmethod
    def test_short_mismatch():
        assert match_name("Other", "SaveImg") is False

    @staticmethod
    def test_qualified_exact():
        assert match_name("ArrayHelper.SaveImg", "SaveImg", "ArrayHelper.SaveImg") is True

    @staticmethod
    def test_qualified_mismatch():
        assert match_name("ArrayHelper.Other", "SaveImg", "ArrayHelper.SaveImg") is False

    @staticmethod
    def test_overload_suffix():
        assert match_name("ArrayHelper.SaveImg(int[][], String)", "SaveImg", "ArrayHelper.SaveImg") is True

    @staticmethod
    def test_overload_suffix_no_match():
        assert match_name("ArrayHelper.SaveImgLike(int[][], String)", "SaveImg", "ArrayHelper.SaveImg") is False

    @staticmethod
    def test_short_matches_without_qualified():
        assert match_name("process", "process") is True

    @staticmethod
    def test_qualified_not_checked_when_none():
        assert match_name("Foo.process", "process") is False

    @staticmethod
    def test_init_overload():
        assert match_name("Foo.<init>(int, String)", "<init>", "Foo.<init>") is True

    @staticmethod
    def test_parenthesis_in_short_no_false_positive():
        assert match_name("ArrayHelper.SaveImg(int)", "SaveImg(int)") is False

    @staticmethod
    def test_cache_works():
        match_name("a", "a")
        match_name("a", "a")
        info = match_name.cache_info()
        assert info.hits >= 1


class TestContainName:
    """Tests for contain_name()."""

    @staticmethod
    def test_short_in_set():
        assert contain_name("append", "MyList.append", {"append", "pop"}) is True

    @staticmethod
    def test_qualified_in_set():
        assert contain_name("append", "MyList.append", {"MyList.append"}) is True

    @staticmethod
    def test_overload_in_set():
        names = {"ArrayHelper.SaveImg(int[][], String, int, int, int, boolean)"}
        assert contain_name("SaveImg", "ArrayHelper.SaveImg", names) is True

    @staticmethod
    def test_no_match():
        assert contain_name("missing", "Foo.missing", {"bar", "Foo.bar"}) is False

    @staticmethod
    def test_short_only():
        assert contain_name("foo", None, {"foo", "bar"}) is True

    @staticmethod
    def test_short_only_miss():
        assert contain_name("baz", None, {"foo", "bar"}) is False

    @staticmethod
    def test_empty_collection():
        assert contain_name("foo", "Bar.foo", set()) is False

    @staticmethod
    def test_prefix_no_false_positive():
        names = {"ArrayHelper.SaveImgLike(int[][], String)"}
        assert contain_name("SaveImg", "ArrayHelper.SaveImg", names) is False
