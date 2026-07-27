This guide describes a **local** install of DeepSearch on **macOS**.

## 1. Environment preparation

* **Hardware**: CPU 2+ cores (4+ recommended); RAM 4 GB+ (8 GB recommended).
* **OS**: macOS 14.0 (Sonoma)+
* **Software**: Git 2.40+, Python 3.11+ (<3.14), uv 0.5.0+, MySQL 8.0+

## 2. Installation

#### 1. Dependencies

##### 1.1. Git

  ```
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" # if Homebrew missing

  brew install git
  ```

  `git --version`

##### 1.2. Python and uv

  ```
  brew install python@3.11
  ```

  `python3 --version`

  ```bash
  brew install uv
  uv --version
  ```

##### 1.3. MySQL (optional)

* **SQLite**: default `DB_TYPE=sqlite`.
* **MySQL**:

  ```
  brew install pkg-config
  brew install mysql
  ```

  ```bash
  brew services start mysql
  mysql -u root
  ```

  ```sql
  CREATE DATABASE openjiuwen_deepsearch;
  CREATE USER 'your_user_name'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL PRIVILEGES ON openjiuwen_deepsearch.* TO 'your_user_name'@'localhost';
  FLUSH PRIVILEGES;
  ```

#### 2. DeepSearch

##### 2.1. Clone

* [DeepSearch repo](https://gitcode.com/openJiuwen/deepsearch)

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
  open .env
  ```

  > Encode special characters in passwords per the [special character table](#macos-special-char).

  ```env
   BACKEND_PORT=6000
   HOST=127.0.0.1

   DB_HOST=localhost
   DB_PORT=3306
   DB_USER=your_user_name
   DB_PASSWORD=your_password  
   ```

  Use the same variable table as [Linux local guide](./Linux%20Installation.md#23-start) (BACKEND_PORT through OBS_*).

  > Checkpointer / OBS behavior: same as [Windows local guide](./Windows%20Installation.md#23-start-deepsearch).

  ```bash
  uv venv
  uv sync --group backend
  ```

  > Hang / TLS: same notes as Linux guide.

  ```bash
  # Terminal 1: main API
  uv run start_backend.py
  ```

  For **DeepSearch** mode (`search_mode=search`), in a **second terminal**:

  ```bash
  uv run python -m server.telemetry_event_server
  ```

  > If you see `No module named 'greenlet'`, see the [FAQ](../../../5.FAQ/README.md).

  Docker installs: see [Docker installation](../Docker%20Installation/README.md). **DeepResearch** only needs terminal 1.

  Expect `Application startup complete` on the main API terminal.

## 3. FAQ

<a id="macos-special-char"></a>
### Special character URL encoding

| Char | Encode | Char | Encode | Char | Encode | Char | Encode | Char | Encode |
|------|--------|------|--------|------|--------|------|--------|------|--------|
| space | %20 | " | %22 | # | %23 | % | %25 | & | %26 |
| ( | %28 | ) | %29 | + | %2B | , | %2C | / | %2F |
| : | %3A | ; | %3B | < | %3C | = | %3D | > | %3E |
| ? | %3F | @ | %40 | \ | %5C | \| | %7C | - | - |

### Why HTTP by default locally

Same as [Windows local guide](./Windows%20Installation.md#why-local-install-defaults-to-http).
