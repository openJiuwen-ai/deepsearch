# Quick Guide

Choose how to install CodeSearch.

> **Note**: This product depends on sibling `openjiuwen-search-base` (`base/`)
> and `openjiuwen-codesearch` (`codesearch/`). Source and Docker builds include
> base automatically; official wheel releases ship **two** wheels that must
> both be installed.

- [Source install](./Source%20Install.md): for developers who need editable
  installs of `base` + `codesearch`.
- [Docker install](./Docker%20Install.md): build the image yourself from the
  Dockerfile (base is baked in).
- [Wheel install](./Wheel%20Install.md): download both wheels from the official
  release URL — no source tree required.

Shared requirements, env vars, Milvus, **local target repos**, and HTTP API notes:
[Installation overview](./README.md).

> **Important**
>
> - Indexing currently supports **Python (`.py`) only**. Non-Python repos yield
>   0 files — expected, not a broken install.
> - Index input is a **local directory**; clone remotes first. Search uses the
>   **collection name** from indexing, not a git URL.
> - The HTTP service has **no authentication**. Set `CODESEARCH_INDEX_ROOTS`
>   (empty → `/api/v1/index` returns 403 by design). Deploy on a trusted network
>   or behind an access-controlled gateway.
