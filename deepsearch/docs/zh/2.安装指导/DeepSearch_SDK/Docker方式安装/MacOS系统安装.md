本指南介绍在 MacOS 系统采用 Docker 方式安装 DeepSearch。

## 一、环境准备

请确保机器满足以下要求：

* 硬件：
  * CPU：最低 2 核，推荐 4 核及以上
  * RAM：最低 4GB，推荐 8GB 及以上

* 操作系统：macOS 14.0（Sonoma）及以上

* 软件
  * Git：运行以下命令进行安装：
    ```
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" # 若未安装Homebrew

    brew install git
    ```

  * Docker：推荐使用 Docker Desktop 进行安装，安装方法详见下文

### 安装 Docker Desktop

* 下载：访问 <a href="https://www.docker.com/products/docker-desktop/" rel="nofollow">Docker Desktop 官网</a>，点击 “Download for Mac” 获取 .dmg 安装包；
* 双击下载的文件，将 **Docker** 图标拖拽到 Applications 文件夹；
* 打开 Launchpad，找到并启动 Docker 应用；
* 首次运行时，系统会提示输入 macOS 密码以授权安装虚拟机组件，点击 OK 继续；
* 首次启动需等待 Docker 完成初始化（下载基础镜像，约需几分钟）。

* 至此，Docker Desktop 安装完成。

> **说明**：若安装过程中出现报错，请参考 <a href="https://docs.docker.com/desktop/setup/install/mac-install/" rel="nofollow">Docker Desktop 官方安装指导</a>。


### 安装 MySQL（可选组件）

* **SQLite vs MySQL**：
  * SQLite 无需额外安装和配置，适合开发和测试环境，但功能受限（如不支持高并发写入、无用户权限管理等）。
  * MySQL 功能更完善，能够满足复杂场景的需求，因此在实际工程和生产环境中更推荐使用。

#### MySQL

* **说明**：若需使用 MySQL，请在传入参数中将 `DB_TYPE` 设置为 `mysql`，并按照下列步骤完成 MySQL 的安装和配置。启动命令参考[数据库配置](#mysql-相关参数db_typemysql-时生效)。

* 打开 “终端”，运行以下命令安装MySQL：

  ```
  brew install pkg-config # 若未安装pkg-config（用于发现mysql）
  brew install mysql
  ```

* 安装完成后，打开 “终端”，运行以下命令启动并登录 MySQL：
   
   ```bash
   brew services start mysql
   mysql -u root
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

## 二、DeepSearch 服务安装

### 1. 下载版本包

* 运行以下命令下载版本包：

  ```
  # 下载 x86_64 架构版本包：
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9
  
  # 下载 ARM64 架构版本包：
  docker pull swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-arm64:0.1.9
  ```

* **本地构建镜像**：若无法拉取 SWR 或需与仓库源码一致，在 DeepSearch **源码根目录**执行  
  `docker build -t deepsearch-full -f docker/Dockerfile .`  
  其中 `deepsearch-full` 仅为示例标签（`-t` 可改为任意名称），须与 `docker run` 最后一行镜像名一致。说明见 [Docker 安装总览](./README.md#镜像内的两个-http-服务)。

### 2. 启动 DeepSearch 服务（以 x86_64 架构为例）

* 推荐启动命令如下（SQLite；含 8089 映射，适用于 **DeepSearch** 模式 `search_mode=search`）：

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

  若仅使用 **DeepResearch** 模式（`search_mode=research`），可只映射 `-p 8000:8000`。

  当出现如下信息时，表示主 API 已成功启动：
  ```
  INFO:     Application startup complete.
  ```

    更多参数配置信息请参考[拓展参数](#3-拓展参数)。

### 3. 拓展参数

#### 端口映射

  官方镜像提供 **8000**（DeepResearch）与 **8089**（DeepSearch 模式）。端口映射格式如下：

  ```bash
  -p <宿主机端口>:<容器端口>
  ```

  主 API 示例：

  ```bash
  -p 8000:8000
  ```

  表示将容器内部的 `8000` 端口映射到宿主机的 `8000` 端口，
  服务可通过 `http://localhost:8000` 访问。

  DeepSearch 模式示例：`-p 8089:8089`（`search_mode=search`）。

  如宿主机端口已被占用，也可使用不同端口进行映射，例如：

  ```bash
  -p 9000:8000
  -p 9089:8089
  ```

  > **说明**：下文部分示例仅写出 `-p 8000:8000`。使用 **DeepSearch** 模式时请增加 `-p 8089:8089`，或在同一 Docker 网络内访问容器 8089。


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

  ##### SQLite 相关参数（`DB_TYPE=sqlite` 时生效）

  | 参数                | 说明              |
  | ----------------- | --------------- |
  | `SQLITE_DB_PATH`  | SQLite 数据文件存储目录，默认`data/databases` |
  | `DEEPSEARCH_SQLITE_DB` | SQLite 数据库文件名，默认`agent.db`   |

#### Milvus（知识库向量索引）

本地知识库依赖向量存储时，需设置 `INDEX_MANAGER_TYPE=milvus` 并配置 Milvus 连接。**DeepSearch 服务镜像内不包含 Milvus**，需要在宿主机或其他容器中**单独部署并启动** Milvus（例如 [Milvus 单机 Docker 说明](https://milvus.io/docs/install_standalone-docker.md)、自写 docker-compose，或 Agent Studio 等环境中已提供的 Milvus 容器）。

  ##### Milvus 相关参数（`INDEX_MANAGER_TYPE=milvus` 时生效）

  | 参数 | 说明 | 默认值 |
  |------|------|--------|
  | `INDEX_MANAGER_TYPE` | 向量索引类型，当前为 `milvus` | `milvus` |
  | `MILVUS_HOST` | Milvus 服务地址（运行 DeepSearch 的容器内须能解析） | 见下文 |
  | `MILVUS_PORT` | Milvus 端口（gRPC，一般为 `19530`） | `19530` |
  | `MILVUS_TOKEN` | 启用 Milvus 认证时填写；无认证可留空 | 留空 |

  **网络说明**：

  - **Milvus 在宿主机本机进程监听 `19530`**：DeepSearch 若在 Docker 内，可设 `MILVUS_HOST=host.docker.internal`、`MILVUS_PORT=19530`（或实际监听端口）。
  - **DeepSearch 与 Milvus 在同一 Docker 自定义网络**：将 `MILVUS_HOST` 设为 Milvus 的 **Compose 服务名**或**容器名**，`MILVUS_PORT` 填 **Milvus 容器内**监听端口（通常为 `19530`），无需使用 `host.docker.internal`。
  - **Milvus 在另一个 Docker 容器，且已将端口映射到宿主机**（例如 `docker run -p 19530:19530 ...` 或 Compose 中 `ports: "19530:19530"`）：DeepSearch 容器内仍使用 `MILVUS_HOST=host.docker.internal`，`MILVUS_PORT` 填**宿主机上映射的端口**（未改映射时即为 `19530`）。

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

  ##### 对象存储 OBS（`CHECKPOINTER_TYPE=redis` 时必填）

  分布式（Redis）部署时，知识库文档须写入共享对象存储；下列变量须完整配置，否则服务无法启动。`in_memory` / `persistence` 模式下知识库仅本地存储，无需配置 OBS。

  | 参数 | 说明 | 默认值 |
  |------|------|--------|
  | `OBS_SERVER` | S3 兼容 Endpoint URL | （须配置） |
  | `OBS_REGION` | 区域（`region_name`，如华为云 `cn-north-4`） | （须配置） |
  | `OBS_BUCKET` | 桶名 | （须配置） |
  | `OBS_ACCESS_KEY_ID` | 访问密钥 ID | （须配置） |
  | `OBS_SECRET_ACCESS_KEY` | 访问密钥 Secret | （须配置） |

  **镜像说明**：

  - **官方镜像**：从华为云 SWR 拉取，启动命令最后一行使用已 `docker pull` 的完整镜像名，例如  
    `swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-arm64:0.1.9`（Apple Silicon）或  
    `swr.cn-north-4.myhuaweicloud.com/openjiuwen/deepsearch-studio-server-amd64:0.1.9`（x86_64）。拉取失败多为网络或权限问题，可检查代理、镜像加速或改用本地构建。
  - **本地构建 `deepsearch-full`**：在 DeepSearch **源码仓库根目录**执行  
    `docker build -t deepsearch-full -f docker/Dockerfile .`  
    其中 `deepsearch-full` 仅为示例标签（`-t` 可改为任意名称）。

  下文示例以 **`deepsearch-full`** 为例；使用官方镜像时，将每条命令**最后一行**替换为对应的 SWR 镜像地址。请先单独启动 **Milvus** 与（若使用 redis 模式）**Redis**，并保证端口可从 DeepSearch 容器访问（配置方式见上文「Milvus（知识库向量索引）」与「Redis 模式相关参数」）。

  **使用示例**：

  ```bash
  # 开发测试环境（默认配置，无需额外参数）
  # MySQL
  docker run -p 8000:8000 \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e INDEX_MANAGER_TYPE=milvus \
    -e MILVUS_HOST=host.docker.internal \
    -e MILVUS_PORT=19530 \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    deepsearch-full

  # SQLite
  docker run -p 8000:8000 \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=sqlite \
    -e SQLITE_DB_PATH=data/databases \
    -e DEEPSEARCH_SQLITE_DB=agent.db \
    -e INDEX_MANAGER_TYPE=milvus \
    -e MILVUS_HOST=host.docker.internal \
    -e MILVUS_PORT=19530 \
    deepsearch-full

  # 单机生产环境（persistence 模式）
  # MySQL
  docker run -p 8000:8000 \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=mysql \
    -e DB_HOST=host.docker.internal \
    -e DB_PORT=3306 \
    -e DB_USER=your_user_name \
    -e DB_PASSWORD=your_password \
    -e INDEX_MANAGER_TYPE=milvus \
    -e MILVUS_HOST=host.docker.internal \
    -e MILVUS_PORT=19530 \
    -e DEEPSEARCH_DB_NAME=openjiuwen_deepsearch \
    -e CHECKPOINTER_TYPE=persistence \
    -e CHECKPOINTER_DB_TYPE=sqlite \
    -e CHECKPOINTER_DB_PATH=data/databases/checkpointer.db \
    deepsearch-full

  # SQLite
  docker run -p 8000:8000 \
    -e LLM_SSL_VERIFY=False \
    -e TOOL_SSL_VERIFY=False \
    -e EMBEDDING_SSL_VERIFY=False \
    -e DB_TYPE=sqlite \
    -e SQLITE_DB_PATH=data/databases \
    -e DEEPSEARCH_SQLITE_DB=agent.db \
    -e INDEX_MANAGER_TYPE=milvus \
    -e MILVUS_HOST=host.docker.internal \
    -e MILVUS_PORT=19530 \
    -e CHECKPOINTER_TYPE=persistence \
    -e CHECKPOINTER_DB_TYPE=sqlite \
    -e CHECKPOINTER_DB_PATH=data/databases/checkpointer.db \
    deepsearch-full

  # 分布式生产环境（redis 模式）
  # MySQL
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
    -e INDEX_MANAGER_TYPE=milvus \
    -e MILVUS_HOST=host.docker.internal \
    -e MILVUS_PORT=19530 \
    -e CHECKPOINTER_TYPE=redis \
    -e REDIS_URL=redis://host.docker.internal:6379 \
    -e REDIS_CLUSTER_MODE=false \
    -e REDIS_TTL=7200 \
    -e REDIS_REFRESH_ON_READ=true \
    -e OBS_SERVER=https://your-obs-endpoint \
    -e OBS_REGION=cn-north-4 \
    -e OBS_BUCKET=your-bucket \
    -e OBS_ACCESS_KEY_ID=your_access_key \
    -e OBS_SECRET_ACCESS_KEY=your_secret_key \
    deepsearch-full
  ```

  **注意事项**：
  - `in_memory` 模式：无需额外配置，适用于开发测试环境，不支持分布式部署；知识库文档仅存容器/数据卷本地，不使用对象存储
  - `persistence` 模式：需要确保数据目录有写权限，适用于单机生产环境；知识库文档同样仅存本地，不使用对象存储
  - `redis` 模式：需要先部署 Redis 服务，适用于分布式生产环境；**还须**通过环境变量完整配置 OBS（见上文「对象存储 OBS」），否则服务无法启动。**仅在此模式下**知识库上传会写入对象存储以供多实例共享。Redis 若在宿主机或其它容器且端口已映射到宿主机，可使用 `REDIS_URL=redis://host.docker.internal:6379`（或实际端口）。使用本地知识库时，Milvus 须单独部署，见上文「Milvus（知识库向量索引）」。**多实例时 `DB_TYPE` 必须为 `mysql` 且连接同一 MySQL**（知识库等元数据存于应用数据库；SQLite 为容器内本地文件，实例间不共享，会导致一端创建知识库、另一端查询不到）。
