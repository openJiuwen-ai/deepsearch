This guide describes a **local** install of DeepSearch on **Linux**.

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**
  * Ubuntu: minimum 20.04; **22.04 (Jammy)+ recommended**
    > **Note**: Deadsnakes PPA and mainstream support no longer target Ubuntu 20.04 and below.
  * EulerOS: Huawei Cloud EulerOS 2.0+

* **Software**
  * Git 2.40+
  * Python 3.11+ (<3.14)
  * uv 0.5.0+
  * MySQL 8.0+

## 2. Installation

#### 1. Dependencies (example: Ubuntu 22.04)

##### 1.1. Git

  ```bash
  sudo apt update
  sudo apt install git
  ```

##### 1.2. Python and uv

  ```bash
  sudo add-apt-repository ppa:deadsnakes/ppa

  sudo apt update
  sudo apt install python3.11 python3-pip
  ```

  > On Ubuntu 20.04 and below, use [Miniconda](https://www.anaconda.com/docs/getting-started/miniconda/install) or another way to get Python 3.11.

  ```bash
  pip3 install uv
  ```

  > If that fails, see the [uv install docs](https://docs.astral.sh/uv/getting-started/installation/).

##### 1.3. MySQL (optional)

* **SQLite**: default; keep `DB_TYPE=sqlite` in `.env.example`.
* **MySQL**: set `DB_TYPE=mysql` and:

  ```bash
  sudo apt update
  sudo apt install mysql-server
  sudo apt install libmysqlclient-dev pkg-config build-essential python3-dev
  ```

  ```bash
  sudo mysql -u root
  ```

  ```sql
  CREATE DATABASE openjiuwen_deepsearch;
  CREATE USER 'your_user_name'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL PRIVILEGES ON openjiuwen_deepsearch.* TO 'your_user_name'@'localhost';
  FLUSH PRIVILEGES;
  ```

#### 2. DeepSearch

##### 2.1. Clone

* [DeepSearch repo](https://gitcode.com/openJiuwen/deepsearch) access required.

  ```bash
  git config --global user.name your_username
  git config --global user.email your_useremail
  ```

  ![image](../../../images/installation_guide/gitcode-token.png)

  ```bash
  git config --global credential.helper store

  git clone https://gitcode.com/openJiuwen/deepsearch.git
  cd deepsearch
  ```

##### 2.2. Start

  ```bash
  cp .env.example .env
  ```

  > **Note**: `HOST` / `BACKEND_PORT` default to `0.0.0.0` / `8000` if unset. Encode special characters in `DB_PASSWORD` per the [special character table](#linux-special-char).

  ```env
   BACKEND_PORT=6000
   HOST=127.0.0.1

   DB_HOST=localhost
   DB_PORT=3306
   DB_USER=your_user_name
   DB_PASSWORD=your_password 
   ```

  | Variable | Description | Example |
  |----------|-------------|---------|
  | **BACKEND_PORT** | API port; match Studio `DEEPSEARCH_AGENT_PORT` if using Studio | `6000` |
  | **HOST** | API host; match Studio `DEEPSEARCH_AGENT_HOST` | `127.0.0.1` |
  | **DB_TYPE** | `mysql` / `sqlite` | `sqlite` |
  | **DB_HOST** | MySQL host | `localhost` |
  | **DB_PORT** | MySQL port | `3306` |
  | **DB_USER** | MySQL user | `your_user_name` |
  | **DB_PASSWORD** | MySQL password | `your_password` |
  | **DEEPSEARCH_DB_NAME** | Database name | `openjiuwen_deepsearch` |
  | **SQLITE_DB_PATH** | SQLite directory | `data/databases` |
  | **DEEPSEARCH_SQLITE_DB** | SQLite file | `agent.db` |
  | **CHECKPOINTER_TYPE** | `in_memory` / `persistence` / `redis` | `in_memory` |
  | **CHECKPOINTER_DB_TYPE** | When `persistence` | `sqlite` |
  | **CHECKPOINTER_DB_PATH** | When `persistence` | `data/databases/checkpointer.db` |
  | **REDIS_URL** | When `redis` | `redis://localhost:6379` |
  | **REDIS_CLUSTER_MODE** | When `redis` | `false` |
  | **REDIS_TTL** | When `redis` | `7200` |
  | **REDIS_REFRESH_ON_READ** | When `redis` | `true` |
  | **INDEX_MANAGER_TYPE** | `milvus` | `milvus` |
  | **MILVUS_HOST** | Milvus host | `localhost` |
  | **MILVUS_PORT** | Milvus port | `19530` |
  | **MILVUS_TOKEN** | Milvus token | empty or value |
  | **EMBEDDING_SSL_VERIFY** | Embedding TLS verify | `false` |
  | **EMBEDDING_SSL_CERT** | CA PEM path | empty |
  | **HUAWEICLOUD_KMS_ENABLED** | Huawei Cloud KMS | `false` |
  | **OBS_ACCESS_KEY_ID** | Object storage access key ID | empty |
  | **OBS_SECRET_ACCESS_KEY** | Object storage secret | empty |
  | **OBS_SERVER** | S3-compatible endpoint URL | empty |
  | **OBS_REGION** | Region name | empty |
  | **OBS_BUCKET** | Bucket name | empty |

  > Checkpointer modes: same as [Windows local guide](./Windows%20Installation.md#23-start-deepsearch) (`in_memory` / `persistence` / `redis` + OBS for `redis`).

  ```bash
  uv venv
  uv sync --group backend
  ```

  > Hang >20 min: Ctrl+C, edit `pyproject.toml` `[[tool.uv.index]]`, retry.  
  > TLS issues: `uv sync --group backend --native-tls`

  ```bash
  # Terminal 1: main API (default http://127.0.0.1:8000)
  uv run start_backend.py
```

For **DeepSearch** mode (`search_mode=search`, not **DeepResearch** `research`), start Telemetry in a **second terminal** (default `http://127.0.0.1:8089`):

```bash
  # Terminal 2
  uv run python -m server.telemetry_event_server
```

Docker installs start both processes; see [Docker installation](../Docker%20Installation/README.md).

Expect `Application startup complete` on the main API terminal.

## 3. FAQ

<a id="linux-special-char"></a>
### Special character URL encoding

| Char | Encode | Char | Encode | Char | Encode | Char | Encode | Char | Encode |
|------|--------|------|--------|------|--------|------|--------|------|--------|
| space | %20 | " | %22 | # | %23 | % | %25 | & | %26 |
| ( | %28 | ) | %29 | + | %2B | , | %2C | / | %2F |
| : | %3A | ; | %3B | < | %3C | = | %3D | > | %3E |
| ? | %3F | @ | %40 | \ | %5C | \| | %7C | - | - |

### Why HTTP by default locally

Same as [Windows local guide](./Windows%20Installation.md#why-local-install-defaults-to-http).
