# Docker Install

Build the image yourself. The default entrypoint is the HTTP service; the image
installs both `openjiuwen-search-base` and this package.

## Build

Build context **must** be the monorepo root:

```sh
docker build -f codesearch/docker/Dockerfile -t openjiuwen-codesearch:0.2.0 .
```

## Run (service)

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core

docker run --rm --name codesearch-server \
  -p 8100:8100 \
  -e OPENROUTER_API_KEY \
  -e MILVUS_HOST=host.docker.internal \
  -e MILVUS_PORT=19530 \
  -e CODESEARCH_INDEX_ROOTS=/repo \
  -v /data/repos/agent-core:/repo \
  openjiuwen-codesearch:0.2.0
```

On Linux you may need `--add-host=host.docker.internal:host-gateway`.

```sh
curl -sS -X POST http://127.0.0.1:8100/api/v1/index \
  -H 'Content-Type: application/json' \
  -d '{"repo_path":"/repo","collection":"agent_core","revision":"local","reset":true}'
```

> Unset `CODESEARCH_INDEX_ROOTS` → `/api/v1/index` returns 403 (expected).
> The service has no auth — trusted network / gateway only.
> Index input is the mounted **local** path `/repo`, not a git URL.

## CLI (override entrypoint)

Default `CMD` is `codesearch-server`. For CLI:

```sh
docker run --rm \
  -e OPENROUTER_API_KEY \
  -e MILVUS_HOST=host.docker.internal \
  -v /path/to/your/repo:/repo \
  --entrypoint codesearch \
  openjiuwen-codesearch:0.2.0 \
  index --repo /repo --collection demo --revision local
```
