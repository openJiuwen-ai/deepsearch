This guide describes installing DeepSearch on **Windows** using Docker.

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**: Windows 10 or later

* **Software**
  * Git: [download](https://mirrors.huaweicloud.com/git-for-windows/v2.51.0.windows.1/Git-2.51.0-64-bit.exe) and install
  * Docker: Docker Desktop recommended; see below

### Install Docker Desktop

Docker Desktop on Windows should use **WSL 2** as the backend.

**1. Enable WSL 2**

On Windows 10 2004+ (build 19041+) or Windows 11, run:

* Windows + S → search **PowerShell** → **Run as administrator**
* Run:

  ```
  wsl --install
  ```

  Then reboot. Older Windows: [Install WSL](https://learn.microsoft.com/en-us/windows/wsl/install).

**2. Install Docker Desktop**

* Download the Windows installer from [Docker Desktop](https://www.docker.com/products/docker-desktop/) (AMD64 on x86).
* Installer: enable **Use WSL 2 instead of Hyper-V** and **Add shortcut to desktop**, then **OK**.
* Reboot, open Docker Desktop; first launch may take 5–10 minutes.
* Trial: **Continue without signing in**; long-term: [sign in](https://docs.docker.com/desktop/setup/sign-in).

> Errors or details: [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

### Install MySQL (optional)

* **SQLite vs MySQL**
  * SQLite: no extra install; good for dev/test; limited concurrency and no fine-grained DB users.
  * MySQL: richer features; preferred for production.

#### MySQL

* To use MySQL, set `DB_TYPE=mysql` and follow the steps below. Startup examples: [Database configuration](#database--storage-configuration).

* Download [MySQL 8.4 for Windows](https://dev.mysql.com/get/Downloads/MySQL-8.4/mysql-8.4.7-winx64.msi).

* Run the installer; **Typical** is fine.

  > If you see “This application requires Visual Studio 2019 x64 Redistributable”, install the [latest VC++ x64 redistributable](https://aka.ms/vc14/vc_redist.x64.exe).

* Set and remember the **root** password.

* **Win+R** → run:

  ```
  rundll32.exe sysdm.cpl,EditEnvironmentVariables
  ```

* Add MySQL `bin` to `PATH` (default: `C:\Program Files\MySQL\MySQL Server 8.4\bin`).

* In PowerShell, log in:

  ```bash
  mysql -u root -p
  ```

* Create DB and user (`your_user_name`, `your_password` are yours; use them in Docker `-e`):

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

Recommended SQLite run (includes 8089 for **DeepSearch** mode, `search_mode=search`):

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

See [Docker overview](./README.md#two-http-services-in-one-container). For **DeepResearch** only (`search_mode=research`), omit `-p 8089:8089`.

Success looks like:

  ```
  INFO:     Application startup complete.
  ```

More options: [Extended parameters](#3-extended-parameters).

### 3. Extended parameters

#### Port mapping

The image exposes **8000** (DeepResearch) and **8089** (DeepSearch mode).

Format:

  ```bash
  -p <host_port>:<container_port>
  ```

Main API:

  ```bash
  -p 8000:8000
  ```

Telemetry:

  ```bash
  -p 8089:8089
  ```

Maps container `8000` to host `8000` → `http://localhost:8000`.

If 8000 is taken:

  ```bash
  -p 9000:8000
  ```


#### Database / storage

Use `-e` for DB settings.

##### `DB_TYPE`

* **Purpose**: database backend
* **Values**: `sqlite` / `mysql`


##### MySQL parameters (when `DB_TYPE=mysql`)

| Variable | Description |
| -------- | ----------- |
| `DB_HOST` | MySQL host |
| `DB_PORT` | MySQL port |
| `DB_USER` | DB user |
| `DB_PASSWORD` | DB password |
| `DEEPSEARCH_DB_NAME` | Database name |

**Note**: From a container, reach host MySQL via `host.docker.internal`:

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

##### SQLite parameters (when `DB_TYPE=sqlite`)

| Variable | Description |
| -------- | ----------- |
| `SQLITE_DB_PATH` | SQLite directory; default `data/databases` |
| `DEEPSEARCH_SQLITE_DB` | SQLite filename; default `agent.db` |

#### Checkpointer

Checkpointer stores workflow session state for pause/resume and persistence.

##### `CHECKPOINTER_TYPE`

* **Purpose**: checkpointer backend
* **Values**: `in_memory` / `persistence` / `redis`
* **Default**: `in_memory`

##### Persistence (`CHECKPOINTER_TYPE=persistence`)

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `CHECKPOINTER_DB_TYPE` | `sqlite` / `shelve` | `sqlite` |
| `CHECKPOINTER_DB_PATH` | DB file path | `data/databases/checkpointer.db` |

##### Redis (`CHECKPOINTER_TYPE=redis`)

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `REDIS_URL` | Redis URL | `redis://localhost:6379` |
| `REDIS_CLUSTER_MODE` | Redis Cluster | `false` |
| `REDIS_TTL` | Session TTL (seconds) | `7200` |
| `REDIS_REFRESH_ON_READ` | Refresh TTL on read | `true` |

##### Object storage OBS (required when `CHECKPOINTER_TYPE=redis`)

For distributed (Redis) deploys, knowledge-base files must go to shared object storage. All variables below are required or the service will not start. `in_memory` / `persistence` modes use local disk only—no OBS.

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `OBS_SERVER` | S3-compatible endpoint URL | (required) |
| `OBS_REGION` | Region (`region_name`, e.g. Huawei `cn-north-4`) | (required) |
| `OBS_BUCKET` | Bucket name | (required) |
| `OBS_ACCESS_KEY_ID` | Access key ID | (required) |
| `OBS_SECRET_ACCESS_KEY` | Secret key | (required) |

**Examples**:

  ```bash
  # Dev/test (defaults, no extra flags)
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

  # Single-node production (persistence)
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

  # Distributed production (Redis)
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
- `in_memory`: no extra config; dev/test; not for distributed deploys; KB files stay in container/volume, no object storage.
- `persistence`: writable data dir; single-node production; KB still local, no OBS.
- `redis`: deploy Redis first; distributed production; **must** set OBS env vars as above or the service will not start. **Only in this mode** KB uploads use object storage for multi-instance sharing. If Redis runs on the host, use `host.docker.internal` as the Redis host. **Multi-instance requires `DB_TYPE=mysql` pointing at one shared MySQL** (app metadata for KB lives in MySQL; SQLite per container does not share—KB created on one instance won’t be visible on others).
