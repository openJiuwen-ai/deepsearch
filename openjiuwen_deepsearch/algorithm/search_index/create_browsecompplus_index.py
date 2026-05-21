import logging
import os
import re
from dataclasses import dataclass

import requests
from pymilvus import DataType, Function, FunctionType, MilvusClient
from tqdm import tqdm
from transformers import AutoTokenizer, PreTrainedTokenizer

from openjiuwen_deepsearch.algorithm.search_index.index_utils import (
    chunked_iterable,
    populate_datetime_str,
    read_jsonl,
    update_dict_lists,
    update_dict_str,
)
from openjiuwen_deepsearch.algorithm.search_index.tokenizer_chunker import TokenizerChunker
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.embedder import (
    AbstractEmbedder,
    OpenJiuwenAPIEmbedder,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


# 配置可从环境变量读取（如 docker/.env）；EMBED_API_*、EMBED_MODEL_NAME、HUGGINGFACE_MODEL_NAME 无代码内默认，须自行设置
DATA_LOCATION = _env("DATA_LOCATION", "browsecompplus.jsonl")
BATCH_SIZE = _env_int("BATCH_SIZE", 10)
MILVUS_URI = _env("MILVUS_URI", "http://localhost:19530")
MILVUS_TOKEN = _env("MILVUS_TOKEN", "root:Milvus")
MILVUS_DB_NAME = _env("MILVUS_DB_NAME", "deepsearch_benchmarks")
MILVUS_COLLECTION_NAME = _env("MILVUS_COLLECTION_NAME", "browsecompplus_with_bm25")
HUGGINGFACE_MODEL_NAME = _env("HUGGINGFACE_MODEL_NAME", "Qwen/Qwen3-Embedding-8B")
EMBED_MODEL_NAME = _env("EMBED_MODEL_NAME", "")

# Embedding API 配置（必填，无默认值；未填写运行时会报错）
EMBED_API_URL = _env("EMBED_API_URL", "")
EMBED_API_KEY = _env("EMBED_API_KEY", "")
EMBED_TIMEOUT = _env_int("EMBED_TIMEOUT", 60)

# 仅索引前 N 条记录（用于测试），0 表示不限制
INDEX_MAX_RECORDS = _env_int("INDEX_MAX_RECORDS", 0)


class BrowseCompChunker:
    def __init__(
        self,
        tokenizer: PreTrainedTokenizer,
        chunk_size: int = 2048,
        chunk_overlap: int = 50,
    ):
        self.splitter = TokenizerChunker(
            chunk_size=chunk_size,  # desired max tokens
            chunk_overlap=chunk_overlap,  # overlap tokens
            tokenizer=tokenizer,  # use your tokenizer for accurate token counting
        )

    def __call__(
        self,
        text: str,
        prefix: str | None = None,
        use_prefix: bool = False,
        *args,
        **kwargs,
    ) -> list[str]:
        chunks = self.splitter.chunk_text(text)
        if use_prefix and prefix is None:
            raise ValueError(f"Input prefix cannot be None when {use_prefix=}")
        if use_prefix:
            for chunk_idx in range(1, len(chunks)):
                chunks[chunk_idx] = f"{prefix}{chunks[chunk_idx]}"
        return chunks


def process_text_content(
    text: str, splitter: BrowseCompChunker | None = None
) -> list[dict[str, str | list[str] | dict[str, int] | None]]:
    """
    Extracts title, authors, and date from YAML-like front matter (--- or --) near the start,
    and always returns the remaining content.
    :param text:
    :param splitter:
    :return:
    """
    text = text.strip()

    # Match YAML-style front matter block near start
    front_matter_match = re.search(
        r"^\s*[-]{2,3}\s*\n?(.*?)\n?[-]{2,3}", text, flags=re.DOTALL
    )

    if front_matter_match:
        front = front_matter_match.group(1)
        prefix_match = re.search(
            r"^\s*([-]{2,3}\s*\n?.*?\n?[-]{2,3}\s*)", text, flags=re.DOTALL
        )
        prefix = prefix_match.group(1)
        content = text[front_matter_match.end():].strip()
    else:
        front = text[:200]
        prefix = front
        content = text

    title_match = re.search(r"\btitle\s*:\s*([^\n\r]+)", front, flags=re.IGNORECASE)
    date_match = re.search(
        r"\bdate\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", front, flags=re.IGNORECASE
    )

    author_block_match = re.search(
        r"\bauthors?\s*:\s*(?:([^\n\r]+)|((?:\n\s*-\s*[^\n\r]+)+))",
        front,
        flags=re.IGNORECASE,
    )

    title = title_match.group(1).strip() if title_match else None
    date = date_match.group(1).strip() if date_match else None
    datetime_text = populate_datetime_str(date)
    authors: list[str] = []

    if author_block_match:
        if author_block_match.group(1):
            raw_authors = author_block_match.group(1).strip()
            authors = [
                a.strip() for a in re.split(r"[,;]\s*", raw_authors) if a.strip()
            ]
        else:
            authors = [
                a.strip()
                for a in re.findall(r"-\s*([^\n\r]+)", author_block_match.group(2))
                if a.strip()
            ]

    if splitter is not None:
        return [
            {
                "title": title,
                "date": date,
                "datetime_text": datetime_text,
                "authors": authors,
                "chunk": sp,
                "chunk_idx": sp_idx,
                "source": {
                    "main_body": {"start_idx": text.find(content)},
                    "prefix": prefix,
                },
            }
            for sp_idx, sp in enumerate(splitter(text, prefix=prefix, use_prefix=True))
        ]
    else:
        return [
            {
                "title": title,
                "date": date,
                "datetime_text": datetime_text,
                "authors": authors,
                "chunk": text,
                "chunk_idx": 0,
                "source": {
                    "main_body": {"start_idx": text.find(content)},
                    "prefix": prefix,
                },
            }
        ]


def setup_milvus_collection(
    milvus_client: MilvusClient, collection_name: str, embed_dim: int
):
    """
    Creates the collection if it doesn't exist.
    """
    if milvus_client.has_collection(collection_name):
        logger.info(f"Collection {collection_name} already exists.")
        return

    # Define Schema
    # auto_id=False so we can use our custom ID string
    # enable_dynamic_field=True allows storing extra metadata in a JSON-like 'dynamic' field automatically
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)

    # 1. Primary Key: Combine docid + chunk_idx
    schema.add_field(
        field_name="id", datatype=DataType.VARCHAR, max_length=512, is_primary=True
    )

    # 2. Vector Field
    schema.add_field(
        field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=embed_dim
    )

    # 3. Scalar Fields (Explicitly defined for filtering speed, others go into dynamic)
    schema.add_field(field_name="docid", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=10000)
    schema.add_field(
        field_name="content",
        datatype=DataType.VARCHAR,
        max_length=60000,
        enable_analyzer=True,
    )
    schema.add_field(field_name="content_sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field(field_name="datetime", datatype=DataType.VARCHAR, max_length=64)

    # Array fields for authors and query IDs
    schema.add_field(
        field_name="authors",
        datatype=DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=50,
        max_length=256,
    )
    schema.add_field(
        field_name="gold_query_id",
        datatype=DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=100,
        max_length=128,
    )
    schema.add_field(
        field_name="evidence_query_id",
        datatype=DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=100,
        max_length=128,
    )

    # Add BM25 Function
    bm25_function = Function(
        name="text_bm25_emb",  # Function name
        input_field_names=[
            "content"
        ],  # Name of the VARCHAR field containing raw text data
        output_field_names=[
            "content_sparse"
        ],  # Name of the SPARSE_FLOAT_VECTOR field reserved to store generated embeddings
        function_type=FunctionType.BM25,  # Set to `BM25`
    )

    schema.add_function(bm25_function)

    # Create Collection
    index_params = milvus_client.prepare_index_params()

    # Add index to the vector field
    index_params.add_index(
        field_name="embedding",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )

    index_params.add_index(
        field_name="content_sparse",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
    )

    milvus_client.create_collection(
        collection_name=collection_name, schema=schema, index_params=index_params
    )
    logger.info(f"Created Milvus collection: {collection_name}")


@dataclass
class IndexDocumentsConfig:
    """Configuration for indexing documents to Milvus."""

    input_docs: list[dict]
    milvus_client: MilvusClient
    collection_name: str
    encoder_model: AbstractEmbedder
    chunk_splitter: BrowseCompChunker | None = None
    doc_id2gold_query_id: dict[str, list[str]] | None = None
    doc_id2evidence_query_id: dict[str, list[str]] | None = None
    update_existing_docs: bool = False
    batch_size: int = BATCH_SIZE


def index_documents_milvus(config: IndexDocumentsConfig) -> None:
    num_batches = (len(config.input_docs) // config.batch_size) + min(
        len(config.input_docs) % config.batch_size, 1
    )

    for batch in tqdm(
        chunked_iterable(config.input_docs, config.batch_size),
        total=num_batches,
        desc=f"Indexing {len(config.input_docs)} docs to Milvus",
    ):
        batched_texts = [doc["text"] for doc in batch]
        processed_docs = [
            process_text_content(snippet, splitter=config.chunk_splitter)
            for snippet in batched_texts
        ]

        embedded_texts = []
        processed_meta = []
        batched_titles = []
        batched_dates = []
        batched_date_exp = []
        batched_authors = []

        # Flatten the list of lists
        flat_doc_mapping = []  # To map back to original batch index if needed

        for idx, _ in enumerate(processed_docs):
            for chunk in processed_docs[idx]:
                processed_meta.append(
                    {
                        "title": chunk["title"],
                        "authors": chunk["authors"],
                        "source": chunk["source"],
                    }
                )
                processed_meta[-1]["source"]["docid"] = batch[idx]["docid"]
                processed_meta[-1]["source"]["chunk_idx"] = chunk["chunk_idx"]
                embedded_texts.append(chunk["chunk"])

                batched_titles.append(chunk["title"] or "")
                batched_dates.append(chunk["date"] or "")
                batched_date_exp.append(chunk["datetime_text"] or "")
                batched_authors.append(chunk["authors"])
                flat_doc_mapping.append(idx)

        # Generate Embeddings
        if len(embedded_texts) > 0:
            try:
                batched_embed = config.encoder_model.encode(
                    input_texts=embedded_texts,
                    is_query=False,
                )
            except CustomValueException:
                raise
            except requests.exceptions.HTTPError:
                if LogManager.is_sensitive():
                    logger.error("Embedding API Error, skipping batch")
                else:
                    logger.error(
                        "Embedding API Error, skipping batch.",
                        exc_info=not LogManager.is_sensitive(),
                    )
                continue

            # Prepare Data for Milvus Insert
            data_rows = []
            for chunk_idx, chunk_meta in enumerate(processed_meta):
                tmp_docid = chunk_meta["source"]["docid"]
                chunk_num = chunk_meta["source"]["chunk_idx"]

                # Construct Unique ID
                unique_id = f"{tmp_docid}__{chunk_num}"

                # Check for updates if required (Milvus upsert handles replacements if ID matches)
                if not config.update_existing_docs:
                    # For simplicity here, we use upsert
                    pass

                gold_query_id = (config.doc_id2gold_query_id or {}).get(tmp_docid, [])
                evidence_query_id = (config.doc_id2evidence_query_id or {}).get(
                    tmp_docid, []
                )

                row = {
                    "id": unique_id,
                    "docid": tmp_docid,
                    "embedding": batched_embed[
                        chunk_idx
                    ],  # Ensure this is List[float] or numpy
                    "content": embedded_texts[
                        chunk_idx
                    ],  # Truncate to fit VarChar limit if extreme
                    "title": (
                        batched_titles[chunk_idx] if batched_titles[chunk_idx] else ""
                    ),
                    "datetime": batched_dates[chunk_idx],
                    "datetime_text": batched_date_exp[
                        chunk_idx
                    ],  # Goes to dynamic field
                    "authors": batched_authors[chunk_idx],
                    "gold_query_id": gold_query_id,
                    "evidence_query_id": evidence_query_id,
                    "metadata": chunk_meta,  # Goes to dynamic field
                }
                data_rows.append(row)

            # Insert into Milvus
            if data_rows:
                try:
                    config.milvus_client.upsert(
                        collection_name=config.collection_name, data=data_rows
                    )
                except Exception as e:
                    if LogManager.is_sensitive():
                        logger.error("Failed to insert batch to Milvus.")
                    else:
                        logger.error(
                            "Failed to insert batch to Milvus: %s",
                            e,
                            exc_info=not LogManager.is_sensitive(),
                        )


if __name__ == "__main__":
    # 1. Load Client
    client = MilvusClient(
        uri=MILVUS_URI, token=MILVUS_TOKEN, database=MILVUS_DB_NAME
    )

    # 2. Connect to Client
    try:
        dbs = client.list_databases()
        if MILVUS_DB_NAME not in dbs:
            logger.info(f"Creating database: {MILVUS_DB_NAME}")
            client.create_database(MILVUS_DB_NAME)
        else:
            logger.info(f"Database {MILVUS_DB_NAME} exists.")

        client.using_database(MILVUS_DB_NAME)
        logger.info(f"Using database: {MILVUS_DB_NAME}")
    except Exception as e:
        if LogManager.is_sensitive():
            logger.warning("Database operation failed (might be running Milvus Lite?).")
        else:
            logger.warning(
                "Database operation failed (might be running Milvus Lite?): %s",
                e,
            )

    # 3. Setup Embedding Model
    if not EMBED_API_URL or not EMBED_API_KEY:
        raise ValueError(
            "EMBED_API_URL 与 EMBED_API_KEY 为必填项，请在 create_browsecompplus_index.py 顶部填写。"
            " 示例：EMBED_API_URL = 'http://localhost:11450/v1/embeddings'"
        )
    if not EMBED_MODEL_NAME:
        raise ValueError(
            "EMBED_MODEL_NAME 为必填项，请通过环境变量 EMBED_MODEL_NAME 设置 Embedding 模型 id。"
        )
    if not HUGGINGFACE_MODEL_NAME:
        raise ValueError(
            "HUGGINGFACE_MODEL_NAME 为必填项，请通过环境变量设置用于分块的 Hugging Face tokenizer id。"
        )
    timeout = EMBED_TIMEOUT if EMBED_TIMEOUT and EMBED_TIMEOUT > 0 else 60
    model = OpenJiuwenAPIEmbedder(
        pretrained_model=EMBED_MODEL_NAME,
        api_token=EMBED_API_KEY,
        api_url=EMBED_API_URL,
        timeout=timeout,
    )

    tokenizer = AutoTokenizer.from_pretrained(
        HUGGINGFACE_MODEL_NAME, padding_side="left"
    )

    # 4. Load Data
    data_instances = read_jsonl(DATA_LOCATION)
    if INDEX_MAX_RECORDS > 0:
        data_instances = data_instances[:INDEX_MAX_RECORDS]
        logger.info(f"Limited to first {INDEX_MAX_RECORDS} records (index test mode).")

    doc_id2doc = {}
    doc_id2gold_query_id = {}
    doc_id2evidence_query_id = {}

    for item in data_instances:
        query_id = item["query_id"]
        for doc in item["gold_docs"]:
            tmp_docid = doc["docid"]
            update_dict_lists(
                key=tmp_docid, value=query_id, input_dict=doc_id2gold_query_id
            )
            update_dict_str(
                key=tmp_docid, value=doc, input_dict=doc_id2doc, check_conflict=True
            )

        for doc in item["evidence_docs"]:
            tmp_docid = doc["docid"]
            update_dict_lists(
                key=tmp_docid, value=query_id, input_dict=doc_id2evidence_query_id
            )
            update_dict_str(
                key=tmp_docid, value=doc, input_dict=doc_id2doc, check_conflict=True
            )

        for doc in item["negative_docs"]:
            tmp_docid = doc["docid"]
            update_dict_str(
                key=tmp_docid, value=doc, input_dict=doc_id2doc, check_conflict=True
            )

    # Cleaning-up
    for docid in doc_id2gold_query_id:
        doc_id2gold_query_id[docid] = sorted(doc_id2gold_query_id[docid])
    for docid in doc_id2evidence_query_id:
        doc_id2evidence_query_id[docid] = sorted(doc_id2evidence_query_id[docid])

    logger.info(f"Total number of docs to be included: {len(doc_id2doc.keys())}")

    # 6. Setup Milvus Collection
    setup_milvus_collection(client, MILVUS_COLLECTION_NAME, model.embed_dim)

    # 7. Index Docs
    docs_to_indexed = [doc_id2doc[docid] for docid in doc_id2doc][:]
    chunk_splitter = BrowseCompChunker(tokenizer=tokenizer)

    index_documents_milvus(
        IndexDocumentsConfig(
            input_docs=docs_to_indexed,
            milvus_client=client,
            collection_name=MILVUS_COLLECTION_NAME,
            encoder_model=model,
            chunk_splitter=chunk_splitter,
            doc_id2gold_query_id=doc_id2gold_query_id,
            doc_id2evidence_query_id=doc_id2evidence_query_id,
            batch_size=BATCH_SIZE,
        )
    )

    logger.info("Indexing complete.")