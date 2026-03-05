本指南介绍在 Linux 系统采用 Docker 方式安装 DeepSearch。

## 一、环境准备

请确保机器满足以下要求：

* 硬件：
  * CPU：最低 2 核，推荐 4 核及以上
  * RAM：最低 4GB，推荐 8GB 及以上

* 操作系统：
  * Ubuntu：最低 Ubuntu 20.04，推荐 Ubuntu 22.04 (Jammy) 及以上
    > **注意**：Ubuntu 官方与主流软件源已停止支持 Ubuntu 20.04 (Focal) 及以下版本系统。
  * EulerOS：Huawei Cloud EulerOS 2.0及以上

* 软件
  * Docker 和 Docker Compose：安装方法详见下文

### 安装 Docker 和 Docker Compose

* 请参照 <a href="https://docs.docker.com/engine/install/" target="_blank" rel="nofollow noopener noreferrer">Docker 官方安装指南</a> 以及 <a href="https://docs.docker.com/compose/install/" target="_blank" rel="nofollow noopener noreferrer">Docker Compose 官方安装指南</a> 完成配置。

* 验证 Docker 和 Docker Compose 安装:

    ```
    docker version
    docker-compose version
    ```

### 安装 MySQL（可选组件）

* **SQLite vs MySQL**：
  * SQLite 无需额外安装和配置，适合开发和测试环境，但功能受限（如不支持高并发写入、无用户权限管理等）。
  * MySQL 功能更完善，能够满足复杂场景的需求，因此在实际工程和生产环境中更推荐使用。

#### MySQL

* **说明**：若需使用 MySQL，请在传入参数中将 `DB_TYPE` 设置为 `mysql`，并按照下列步骤完成 MySQL 的安装和配置。启动命令参考[数据库配置](#mysql-相关参数db_typemysql-时生效)。

* 运行以下命令安装 MySQL：

  ```bash
  sudo apt update
  sudo apt install mysql-server
  sudo apt install libmysqlclient-dev pkg-config build-essential python3-dev
  ```

* 安装完成后，运行以下命令登录 MySQL：
   
  ```bash
  sudo mysql -u root
  ```

* 在 MySQL 中执行以下命令创建数据库：
  > 说明：`your_user_name`、`your_password` 需自行设置，后续启动命令将会用到。

  ```sql
  # 新建数据库
  CREATE DATABASE openjiuwen_deepsearch;
  # 新建 MySQL 用户
  CREATE USER 'your_user_name'@'localhost' IDENTIFIED BY 'your_password';
  # 用户授权并刷新
  GRANT ALL PRIVILEGES ON openjiuwen_deepsearch.* TO 'your_user_name'@'localhost';
  FLUSH PRIVILEGES;
  ```

## 二、DeepSearch 服务安装（以下以 Ubuntu 22.04 为例）

### 1. 下载版本包

* 运行以下命令下载版本包：

  ```
  # 下载 x86_64 架构版本包：
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.1
  
  # 下载 ARM64 架构版本包：
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-arm64:0.1.1
  ```

### 2. 启动 DeepSearch 服务（以 x86_64 架构为例）

* 最小化可运行的启动命令如下（以SQLite作为数据库）：

  ```
  docker run \
    -p 8000:8000 \ 
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \ 
    -e DB_TYPE=sqlite \ 
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.1
  ```

  当出现如下信息时，表示服务已成功启动：
  ```
  INFO:     Application startup complete.
  ```

    更多参数配置信息请参考[拓展参数](#3-拓展参数)。

### 3. 拓展参数

#### 端口映射

  服务可以通过端口映射将其暴露到宿主机的指定端口。 端口映射参数格式如下：

  ```bash
  -p <宿主机端口>:<容器端口>
  ```

  示例：

  ```bash
  -p 8000:8000
  ```

  表示将容器内部的 `8000` 端口映射到宿主机的 `8000` 端口，
  服务可通过 `http://localhost:8000` 访问。

  如宿主机端口已被占用，也可使用不同端口进行映射，例如：

  ```bash
  -p 9000:8000
  ```


#### 数据库 / 存储配置

  数据库相关参数通过环境变量传入（`-e`），用于控制数据存储方式及连接信息。

  ##### `DB_TYPE`

  * **作用**：选择数据库类型
  * **可选值**：`sqlite` / `mysql`


  ##### MySQL 相关参数（`DB_TYPE=mysql` 时生效）

  | 参数              | 说明         |
  | --------------- | ---------- |
  | `DB_HOST`       | MySQL 服务地址 |
  | `DB_PORT`       | MySQL 服务端口 |
  | `DB_USER`       | 数据库用户名     |
  | `DB_PASSWORD`   | 数据库密码      |
  | `DEEPSEARCH_DB_NAME` | 数据库名称      |

  **注意**：在 Docker 部署环境下，需要显式配置容器访问宿主机的网络地址 `host.docker.internal` 从而访问 MySQL 服务。使用示例：

  ```bash
  docker run \
    -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.1
  ```

  ##### SQLite 相关参数（`DB_TYPE=sqlite` 时生效）

  | 参数                | 说明              |
  | ----------------- | --------------- |
  | `SQLITE_DB_PATH`  | SQLite 数据文件存储目录，默认`data/databases` |
  | `DEEPSEARCH_SQLITE_DB` | SQLite 数据库文件名，默认`agent.db`   |

#### Checkpointer 配置

  Checkpointer 用于管理 Agent 工作流的会话状态，支持工作流的暂停、恢复和状态持久化。

  ##### `CHECKPOINTER_TYPE`

  * **作用**：选择 Checkpointer 类型
  * **可选值**：`in_memory` / `persistence` / `redis`
  * **默认值**：`in_memory`

  ##### Persistence 模式相关参数（`CHECKPOINTER_TYPE=persistence` 时生效）

  | 参数 | 说明 | 默认值 |
  |------|------|--------|
  | `CHECKPOINTER_DB_TYPE` | 数据库类型（sqlite / shelve） | `sqlite` |
  | `CHECKPOINTER_DB_PATH` | 数据库文件路径 | `data/databases/checkpointer.db` |

  ##### Redis 模式相关参数（`CHECKPOINTER_TYPE=redis` 时生效）

  | 参数 | 说明 | 默认值 |
  |------|------|--------|
  | `REDIS_URL` | Redis 连接 URL | `redis://localhost:6379` |
  | `REDIS_CLUSTER_MODE` | 是否启用 Redis Cluster 模式 | `false` |
  | `REDIS_TTL` | 会话状态过期时间 | `7200` |
  | `REDIS_REFRESH_ON_READ` | 每次读取时是否刷新 TTL | `true` |

  **使用示例**：

  ```bash
  # 开发测试环境（默认配置，无需额外参数）
  docker run -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.1

  # 单机生产环境（persistence 模式）
  docker run -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    -e CHECKPOINTER_TYPE=persistence \
    -e CHECKPOINTER_DB_TYPE=sqlite \
    -e CHECKPOINTER_DB_PATH=data/databases/checkpointer.db \
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.1

  # 分布式生产环境（redis 模式）
  docker run -p 8000:8000 \
    --add-host=host.docker.internal:host-gateway \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
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
    swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.1
  ```

  **注意事项**：
  - `in_memory` 模式：无需额外配置，适用于开发测试环境，不支持分布式部署
  - `persistence` 模式：需要确保数据目录有写权限，适用于单机生产环境
  - `redis` 模式：需要先部署 Redis 服务，适用于分布式生产环境。如果 Redis 在宿主机上，可以使用 `host.docker.internal` 作为 Redis 主机地址