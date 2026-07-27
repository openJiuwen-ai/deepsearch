This guide describes a **local** (non-Docker) install of DeepSearch on **Windows**.

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**: Windows 10 or later

* **Software** (install as below)
  * Git 2.40+
  * Python 3.11+ (<3.14)
  * uv 0.5.0+
  * MySQL 8.0+
  * PowerShell 5.1+ (check with `$PSVersionTable.PSVersion`)

## 2. Installation

Install dependencies first, then clone and configure.

#### 1. Dependencies

##### 1.1. Git

* Download [Git for Windows](https://mirrors.huaweicloud.com/git-for-windows/v2.51.0.windows.1/Git-2.51.0-64-bit.exe). Retry on a different network if slow.

* In PowerShell: `git --version` should print the version.

##### 1.2. Python and uv

* Install [Python 3.11.4](https://www.python.org/ftp/python/3.11.4/python-3.11.4-amd64.exe); enable **Add Python to PATH**.

* `python --version` in PowerShell.

* `pip install uv` then `uv --version`.

##### 1.3. MySQL (optional)

* **SQLite vs MySQL**
  * SQLite: no extra install; dev/test; limited concurrency and no DB-level users.
  * MySQL: fuller features; preferred for production.

###### 1.3.1 SQLite

* Default. Keep `DB_TYPE=sqlite` in `.env.example` to start the backend without extra DB setup.

###### 1.3.2 MySQL

* Set `DB_TYPE=mysql` in `.env` (from `.env.example`) and follow:

* [MySQL 8.4 Windows installer](https://dev.mysql.com/get/Downloads/MySQL-8.4/mysql-8.4.7-winx64.msi), **Typical** install.

  > If prompted for Visual Studio 2019 x64 Redistributable, install [VC++ x64](https://aka.ms/vc14/vc_redist.x64.exe).

* Set **root** password; add `C:\Program Files\MySQL\MySQL Server 8.4\bin` to `PATH` (**Win+R** → `rundll32.exe sysdm.cpl,EditEnvironmentVariables`).

* `mysql -u root -p`, then:

  ```sql
  CREATE DATABASE openjiuwen_deepsearch;
  CREATE USER 'your_user_name'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL PRIVILEGES ON openjiuwen_deepsearch.* TO 'your_user_name'@'localhost';
  FLUSH PRIVILEGES;
  ```

#### 2. DeepSearch

##### 2.1. Clone

* Ensure access to the [DeepSearch repo](https://gitcode.com/openJiuwen/deepsearch).

* Configure Git (see repo docs for token screenshots):

  ```bash
  git config --global user.name your_username
  git config --global user.email your_useremail
  ```

  ![image](../../../images/installation_guide/gitcode-token.png)

* Clone (use your GitCode account and personal access token when prompted):

  ```bash
  git config --global credential.helper store

  git clone https://gitcode.com/openJiuwen/deepsearch.git
  cd deepsearch
  ```
  
##### 2.2. Start DeepSearch

* In repo root, PowerShell:

  ```bash
  copy .env.example .env
  ```

* Edit `.env` (do not wipe unrelated keys):

  > **Note**: `HOST` and `BACKEND_PORT` are the API bind address and port. If unset, default host is `0.0.0.0` and port `8000`.  
  > Set `DB_*` to your MySQL instance. If the password has special characters, URL-encode them per the [special character table](#windows-special-char).

  ```env
   # API host/port (example)
   BACKEND_PORT=6000
   HOST=127.0.0.1

   # Database (example)
   DB_HOST=localhost
   DB_PORT=3306
   DB_USER=your_user_name
   DB_PASSWORD=your_password  
   ```

  | Variable | Description | Example |
  |----------|-------------|---------|
  | **BACKEND_PORT** | DeepSearch API port. If using Studio UI, must match Studio `.env` `DEEPSEARCH_AGENT_PORT`. | `6000` |
  | **HOST** | API host. If using Studio, must match Studio `.env` `DEEPSEARCH_AGENT_HOST`. | `127.0.0.1` |
  | **DB_TYPE** | `mysql` / `sqlite` | `sqlite` |
  | **DB_HOST** | MySQL host | `localhost` |
  | **DB_PORT** | MySQL port | `3306` |
  | **DB_USER** | MySQL user | `your_user_name` |
  | **DB_PASSWORD** | MySQL password | `your_password` |
  | **DEEPSEARCH_DB_NAME** | Database name | `openjiuwen_deepsearch` |
  | **SQLITE_DB_PATH** | SQLite directory | `data/databases` |
  | **DEEPSEARCH_SQLITE_DB** | SQLite filename | `agent.db` |
  | **CHECKPOINTER_TYPE** | `in_memory` (dev) / `persistence` (single node) / `redis` (distributed) | `in_memory` |
  | **CHECKPOINTER_DB_TYPE** | `sqlite` / `shelve` when `CHECKPOINTER_TYPE=persistence` | `sqlite` |
  | **CHECKPOINTER_DB_PATH** | Path when `CHECKPOINTER_TYPE=persistence` | `data/databases/checkpointer.db` |
  | **REDIS_URL** | When `CHECKPOINTER_TYPE=redis` | `redis://localhost:6379` |
  | **REDIS_CLUSTER_MODE** | Redis Cluster when `CHECKPOINTER_TYPE=redis` | `false` |
  | **REDIS_TTL** | Session TTL in Redis (seconds) | `7200` |
  | **REDIS_REFRESH_ON_READ** | Refresh TTL on read | `true` |
  | **INDEX_MANAGER_TYPE** | Vector index; currently `milvus` | `milvus` |
  | **MILVUS_HOST** | Milvus host | `localhost` |
  | **MILVUS_PORT** | Milvus port | `19530` |
  | **MILVUS_TOKEN** | Milvus token; empty if none; `run` expects a string | empty or token |
  | **EMBEDDING_SSL_VERIFY** | Verify HTTPS for embedding; unset/blank treated as off | `false` |
  | **EMBEDDING_SSL_CERT** | Custom CA PEM path | empty |
  | **HUAWEICLOUD_KMS_ENABLED** | Huawei Cloud KMS for secrets | `false` |
  | **OBS_ACCESS_KEY_ID** | Object storage key | empty |
  | **OBS_SECRET_ACCESS_KEY** | Object storage secret | empty |
  | **OBS_SERVER** | S3-compatible endpoint | empty |
  | **OBS_REGION** | Region name | empty |
  | **OBS_BUCKET** | Bucket name | empty |

  > **Checkpointer** holds workflow session state (pause/resume/persist).  
  > - `in_memory`: dev/test; not distributed; KB uploads stay on server disk; no OBS.  
  > - `persistence`: single-node prod; writable DB dir; KB still local; no OBS.  
  > - `redis`: distributed; **must** set `OBS_SERVER`, `OBS_BUCKET`, `OBS_REGION`, `OBS_ACCESS_KEY_ID`, `OBS_SECRET_ACCESS_KEY` or the service will not start. **Only then** KB files go to object storage for multi-instance. **Multi-instance requires `DB_TYPE=mysql` on one shared MySQL** (metadata in app DB; SQLite files are not shared across processes).

* In repo root:

  ```bash
  uv venv
  uv sync --group backend
  ```

  > If it hangs >20 minutes, Ctrl+C and change `[[tool.uv.index]]` URL in `pyproject.toml`, then rerun `uv sync --group backend`.

  > If sync fails on HTTPS, try: `uv sync --group backend --native-tls`

  ```bash
  # Terminal 1: main API
  uv run start_backend.py
  ```

  For **DeepSearch** mode (`search_mode=search`), in a **second terminal**:

  ```bash
  uv run python -m server.telemetry_event_server
  ```

  Docker installs: see [Docker installation](../Docker%20Installation/README.md). **DeepResearch** only needs terminal 1.

  You should see `Application startup complete` on the main API terminal.

## 3. FAQ

<a id="windows-special-char"></a>
### Special character URL encoding

| Char | Encode | Char | Encode | Char | Encode | Char | Encode | Char | Encode |
|------|--------|------|--------|------|--------|------|--------|------|--------|
| space | %20 | " | %22 | # | %23 | % | %25 | & | %26 |
| ( | %28 | ) | %29 | + | %2B | , | %2C | / | %2F |
| : | %3A | ; | %3B | < | %3C | = | %3D | > | %3E |
| ? | %3F | @ | %40 | \ | %5C | \| | %7C | - | - |

### Why local install defaults to HTTP

Local installs use HTTP by default to simplify dev/test (no cert setup). Docker images ship with HTTPS. For HTTPS locally, configure certificates yourself.
