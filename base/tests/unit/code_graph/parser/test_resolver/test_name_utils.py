"""Tests for match_name and contain_name utilities."""

from openjiuwen_search_base.codegraph.parser.resolver.passes._utils import contain_name, match_name


class TestMatchName:
    """Tests for match_name()."""

    def setup_method(self):
        match_name.cache_clear()

    def test_exact_short(self):
        assert match_name("SaveImg", "SaveImg") is True

    def test_short_mismatch(self):
        assert match_name("Other", "SaveImg") is False

    def test_qualified_exact(self):
        assert match_name("ArrayHelper.SaveImg", "SaveImg", "ArrayHelper.SaveImg") is True

    def test_qualified_mismatch(self):
        assert match_name("ArrayHelper.Other", "SaveImg", "ArrayHelper.SaveImg") is False

    def test_overload_suffix(self):
        assert match_name("ArrayHelper.SaveImg(int[][], String)", "SaveImg", "ArrayHelper.SaveImg") is True

    def test_overload_suffix_no_match(self):
        assert match_name("ArrayHelper.SaveImgLike(int[][], String)", "SaveImg", "ArrayHelper.SaveImg") is False

    def test_short_matches_without_qualified(self):
        assert match_name("process", "process") is True

    def test_qualified_not_checked_when_none(self):
        assert match_name("Foo.process", "process") is False

    def test_init_overload(self):
        assert match_name("Foo.<init>(int, String)", "<init>", "Foo.<init>") is True

    def test_parenthesis_in_short_no_false_positive(self):
        assert match_name("ArrayHelper.SaveImg(int)", "SaveImg(int)") is False

    def test_cache_works(self):
        match_name("a", "a")
        match_name("a", "a")
        info = match_name.cache_info()
        assert info.hits >= 1


class TestContainName:
    """Tests for contain_name()."""

    def test_short_in_set(self):
        assert contain_name("append", "MyList.append", {"append", "pop"}) is True

    def test_qualified_in_set(self):
        assert contain_name("append", "MyList.append", {"MyList.append"}) is True

    def test_overload_in_set(self):
        names = {"ArrayHelper.SaveImg(int[][], String, int, int, int, boolean)"}
        assert contain_name("SaveImg", "ArrayHelper.SaveImg", names) is True

    def test_no_match(self):
        assert contain_name("missing", "Foo.missing", {"bar", "Foo.bar"}) is False

    def test_short_only(self):
        assert contain_name("foo", None, {"foo", "bar"}) is True

    def test_short_only_miss(self):
        assert contain_name("baz", None, {"foo", "bar"}) is False

    def test_empty_collection(self):
        assert contain_name("foo", "Bar.foo", set()) is False

    def test_prefix_no_false_positive(self):
        names = {"ArrayHelper.SaveImgLike(int[][], String)"}
        assert contain_name("SaveImg", "ArrayHelper.SaveImg", names) is False
