This guide describes installing DeepSearch on **macOS** using Docker.

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**: macOS 14.0 (Sonoma)+

* **Software**
  * Git:

    ```
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" # if Homebrew missing

    brew install git
    ```

  * Docker: Docker Desktop; see below

### Install Docker Desktop

* [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) → **Download for Mac** (.dmg).
* Drag **Docker** to **Applications**, launch, enter password when prompted.
* First start downloads images (a few minutes).

> Issues: [Docker Desktop on Mac](https://docs.docker.com/desktop/setup/install/mac-install/).

### Install MySQL (optional)

* **SQLite vs MySQL**: SQLite for simple dev/test; MySQL for fuller production scenarios.

#### MySQL

* Set `DB_TYPE=mysql` and follow below. Examples: [Database / storage](#database--storage-configuration).

  ```
  brew install pkg-config
  brew install mysql
  ```

* Start and log in:

   ```bash
   brew services start mysql
   mysql -u root
   ```

* Create DB and user:

  ```sql
  CREATE DATABASE openjiuwen_deepsearch;
  CREATE USER 'your_user_name'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL PRIVILEGES ON openjiuwen_deepsearch.* TO 'your_user_name'@'localhost';
  FLUSH PRIVILEGES;
  ```

## 2. Install DeepSearch service

### 1. Pull the image

  ```
  # x86_64
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9

  # ARM64
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-arm64:0.1.9
  ```

### 2. Start DeepSearch (x86_64 example)

Recommended SQLite run (maps **8000** and **8089** for **DeepSearch** mode, `search_mode=search`):

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

See [Docker overview](./README.md#two-http-services-in-one-container). **DeepResearch** only needs `-p 8000:8000`.

Success:

  ```
  INFO:     Application startup complete.
  ```

More: [Extended parameters](#3-extended-parameters).

### 3. Extended parameters

#### Port mapping

  ```bash
  -p <host_port>:<container_port>
  ```

Examples: `-p 8000:8000` (DeepResearch), `-p 8089:8089` (DeepSearch mode), or `-p 9000:8000` / `-p 9089:8089` if ports are busy.

#### Database / storage

##### `DB_TYPE`

* **Values**: `sqlite` / `mysql`

##### MySQL (`DB_TYPE=mysql`)

| Variable | Description |
| -------- | ----------- |
| `DB_HOST` | Host |
| `DB_PORT` | Port |
| `DB_USER` | User |
| `DB_PASSWORD` | Password |
| `DEEPSEARCH_DB_NAME` | DB name |

**Note**: Use `host.docker.internal` to reach MySQL on the Mac host:

  ```bash
  docker run \
    -p 8000:8000 \
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
| `SQLITE_DB_PATH` | Default `data/databases` |
| `DEEPSEARCH_SQLITE_DB` | Default `agent.db` |

#### Checkpointer

##### `CHECKPOINTER_TYPE`

* `in_memory` / `persistence` / `redis` (default `in_memory`)

##### Persistence / Redis / OBS

Same semantics as [Linux Docker guide](./Linux%20Installation.md#3-extended-parameters). When `CHECKPOINTER_TYPE=redis`, set all `OBS_*` variables.

**Examples**:

  ```bash
  docker run -p 8000:8000 \
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
- `in_memory` / `persistence`: KB files stay local; no OBS.
- `redis`: OBS mandatory; shared KB uploads; multi-instance needs shared MySQL.
