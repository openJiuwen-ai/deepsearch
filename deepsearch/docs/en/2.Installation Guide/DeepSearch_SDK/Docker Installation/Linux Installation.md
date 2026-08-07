This guide describes installing DeepSearch on **Linux** using Docker.

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**
  * Ubuntu: minimum 20.04; **22.04 (Jammy)+ recommended**
    > **Note**: Ubuntu 20.04 (Focal) and below are out of mainstream support.
  * EulerOS: Huawei Cloud EulerOS 2.0+

* **Software**
  * Docker and Docker Compose: see below

### Install Docker and Docker Compose

* Follow [Docker Engine install](https://docs.docker.com/engine/install/) and [Docker Compose install](https://docs.docker.com/compose/install/).

* Verify:

    ```
    docker version
    docker-compose version
    ```

### Install MySQL (optional)

* **SQLite vs MySQL**
  * SQLite: no extra install; dev/test; limited features.
  * MySQL: production-oriented.

#### MySQL

* Set `DB_TYPE=mysql` and follow below. Examples: [Database / storage](#database--storage-configuration).

  ```bash
  sudo apt update
  sudo apt install mysql-server
  sudo apt install libmysqlclient-dev pkg-config build-essential python3-dev
  ```

* Log in:

  ```bash
  sudo mysql -u root
  ```

* Create DB and user:

  ```sql
  CREATE DATABASE openjiuwen_deepsearch;
  CREATE USER 'your_user_name'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL PRIVILEGES ON openjiuwen_deepsearch.* TO 'your_user_name'@'localhost';
  FLUSH PRIVILEGES;
  ```

## 2. Install DeepSearch service (example: Ubuntu 22.04)

### 1. Pull the image

  ```
  # x86_64
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9

  # ARM64
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-arm64:0.1.9
  ```

### 2. Start DeepSearch (x86_64 example)

Recommended SQLite run (maps 8000 and 8089 when you need **DeepSearch** mode, `search_mode=search`):

  ```
  docker run \
    -p 8000:8000 \
    -p 8089:8089 \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \ 
    -e DB_TYPE=sqlite \ 
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9
  ```

For **DeepResearch** only (`search_mode=research`), `-p 8000:8000` is enough.

See [Docker overview](./README.md#two-http-services-in-one-container).

Success:

  ```
  INFO:     Application startup complete.
  ```

More: [Extended parameters](#3-extended-parameters).

### 3. Extended parameters

#### Port mapping

The image exposes **8000** (DeepResearch) and **8089** (DeepSearch mode / Telemetry).

  ```bash
  -p <host_port>:<container_port>
  ```

Main API:

  ```bash
  -p 8000:8000
  ```

DeepSearch mode (`search_mode=search`, e.g. `POST /runs`):

  ```bash
  -p 8089:8089
  ```

Alternate host ports:

  ```bash
  -p 9000:8000
  -p 9089:8089
  ```

> Later examples may show only `-p 8000:8000` for brevity. Add `-p 8089:8089` (or use the same Docker network) when using **DeepSearch** mode.


#### Database / storage

##### `DB_TYPE`

* **Purpose**: database type
* **Values**: `sqlite` / `mysql`


##### MySQL (`DB_TYPE=mysql`)

| Variable | Description |
| -------- | ----------- |
| `DB_HOST` | MySQL host |
| `DB_PORT` | MySQL port |
| `DB_USER` | User |
| `DB_PASSWORD` | Password |
| `DEEPSEARCH_DB_NAME` | Database name |

**Note**: On Linux, map `host.docker.internal` to the host gateway so the container can reach host MySQL:

  ```bash
  docker run \
    -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9
  ```

##### SQLite (`DB_TYPE=sqlite`)

| Variable | Description |
| -------- | ----------- |
| `SQLITE_DB_PATH` | Directory; default `data/databases` |
| `DEEPSEARCH_SQLITE_DB` | Filename; default `agent.db` |

#### Checkpointer

##### `CHECKPOINTER_TYPE`

* **Values**: `in_memory` / `persistence` / `redis`
* **Default**: `in_memory`

##### Persistence

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `CHECKPOINTER_DB_TYPE` | `sqlite` / `shelve` | `sqlite` |
| `CHECKPOINTER_DB_PATH` | Path | `data/databases/checkpointer.db` |

##### Redis

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `REDIS_URL` | URL | `redis://localhost:6379` |
| `REDIS_CLUSTER_MODE` | Cluster mode | `false` |
| `REDIS_TTL` | TTL (s) | `7200` |
| `REDIS_REFRESH_ON_READ` | Refresh on read | `true` |

##### OBS (`CHECKPOINTER_TYPE=redis` required)

| Variable | Description |
| -------- | ----------- |
| `OBS_SERVER` | S3 endpoint (required) |
| `OBS_REGION` | Region (required) |
| `OBS_BUCKET` | Bucket (required) |
| `OBS_ACCESS_KEY_ID` | Key ID (required) |
| `OBS_SECRET_ACCESS_KEY` | Secret (required) |

**Examples**:

  ```bash
  docker run -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9

  docker run -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    -e CHECKPOINTER_TYPE=persistence \
    -e CHECKPOINTER_DB_TYPE=sqlite \
    -e CHECKPOINTER_DB_PATH=data/databases/checkpointer.db \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9

  docker run -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    -e CHECKPOINTER_TYPE=redis \
    -e REDIS_URL=redis://redis-host:6379 \
    -e REDIS_CLUSTER_MODE=false \
    -e REDIS_TTL=7200 \
    -e REDIS_REFRESH_ON_READ=true \
    -e OBS_SERVER=https://your-obs-endpoint \
    -e OBS_REGION=cn-north-4 \
    -e OBS_BUCKET=your-bucket \
    -e OBS_ACCESS_KEY_ID=your_access_key \
    -e OBS_SECRET_ACCESS_KEY=your_secret_key \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9
  ```

**Notes**:
- `in_memory`: dev/test; not distributed; KB local only.
- `persistence`: single-node prod; KB local; needs writable dir.
- `redis`: distributed; **OBS required**; **only this mode** uses object storage for KB uploads. Host Redis: `host.docker.internal`. **Multi-instance: shared MySQL** (`DB_TYPE=mysql`).
