# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Collection schema 与索引参数。schema 变更必须递增 MilvusConfig.schema_version。"""

from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

OUTPUT_FIELDS = ["text", "file_path", "start_line", "end_line", "kind", "original_name"]

SPARSE_INDEX_PARAMS = {
    "metric_type": "BM25",
    "index_type": "SPARSE_INVERTED_INDEX",
    "params": {
        "inverted_index_algo": "DAAT_MAXSCORE",
        "bm25_k1": 1.2,
        "bm25_b": 0.75,
    },
}

DENSE_INDEX_PARAMS = {"metric_type": "COSINE", "index_type": "AUTOINDEX", "params": {}}


def versioned_collection_name(base_name: str, schema_version: str, prefix: str = "cs_") -> str:
    """产品前缀隔离共用实例的命名空间；版本后缀隔离 schema 演进。"""
    return f"{prefix}{base_name}__{schema_version}"


def build_schema(
    use_dense: bool, embed_dim: int, max_char_limit: int, max_num_calls: int
) -> CollectionSchema:
    fields = [FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False)]
    if use_dense:
        fields.append(
            FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=embed_dim)
        )
    fields.extend(
        [
            FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema(name="file_hash", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(
                name="instance_ids",
                dtype=DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_capacity=1024,
                max_length=256,
            ),
            FieldSchema(name="repo", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(
                name="commits",
                dtype=DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_capacity=1024,
                max_length=64,
            ),
            FieldSchema(name="file_path", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="start_line", dtype=DataType.INT64),
            FieldSchema(name="end_line", dtype=DataType.INT64),
            FieldSchema(name="kind", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="original_name", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(
                name="name",
                dtype=DataType.VARCHAR,
                max_length=512,
                enable_analyzer=True,
                analyzer_params={"type": "english"},
            ),
            FieldSchema(
                name="text",
                dtype=DataType.VARCHAR,
                max_length=max_char_limit,
                enable_analyzer=True,
                analyzer_params={"type": "standard"},
            ),
            FieldSchema(
                name="text_trigram",
                dtype=DataType.VARCHAR,
                max_length=max_char_limit,
                enable_analyzer=True,
                analyzer_params={"tokenizer": "whitespace"},
            ),
            FieldSchema(name="sparse_vector_trigram", dtype=DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema(
                name="calls",
                dtype=DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_capacity=max_num_calls,
                max_length=2048,
            ),
        ]
    )

    schema = CollectionSchema(fields, description="CodeSearch multi-revision code index")
    schema.add_function(
        Function(
            name="name_bm25_emb",
            input_field_names=["text"],
            output_field_names=["sparse_vector"],
            function_type=FunctionType.BM25,
        )
    )
    schema.add_function(
        Function(
            name="trigram_bm25_emb",
            input_field_names=["text_trigram"],
            output_field_names=["sparse_vector_trigram"],
            function_type=FunctionType.BM25,
        )
    )
    return schema
