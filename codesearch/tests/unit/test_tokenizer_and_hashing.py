# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.indexing.hashing import deterministic_chunk_id, file_content_hash
from openjiuwen_codesearch.retrieval.tokenizer import generate_char_trigrams, tokenise_code_string


class TestTokeniseCodeString:
    def test_camel_case(self):
        assert tokenise_code_string("getUserName") == "get user name"

    def test_pascal_and_snake(self):
        assert tokenise_code_string("HTTPServer_config") == "http server config"

    def test_empty(self):
        assert tokenise_code_string("") == ""


class TestTrigrams:
    def test_hex_encoding(self):
        # "abcd" → trigrams "abc","bcd" → hex
        assert generate_char_trigrams("abcd") == "616263 626364"

    def test_short_string_hex(self):
        assert generate_char_trigrams("ab") == "6162"

    def test_case_sensitive(self):
        assert generate_char_trigrams("ABC") != generate_char_trigrams("abc")

    def test_length_cap_exact(self):
        out = generate_char_trigrams("a" * 1000, max_chars=20)
        assert len(out) <= 20

    def test_empty(self):
        assert generate_char_trigrams("") == ""


class TestHashing:
    def test_file_hash_depends_on_path_and_content(self):
        h1 = file_content_hash("a.py", b"data")
        assert h1 == file_content_hash("a.py", b"data")
        assert h1 != file_content_hash("b.py", b"data")
        assert h1 != file_content_hash("a.py", b"other")

    def test_chunk_id_deterministic_int64(self):
        cid = deterministic_chunk_id("hash", 1, 10, "f")
        assert cid == deterministic_chunk_id("hash", 1, 10, "f")
        assert cid != deterministic_chunk_id("hash", 1, 11, "f")
        assert 0 <= cid < 2**63
