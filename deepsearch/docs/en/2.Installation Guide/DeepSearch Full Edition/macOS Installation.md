This guide describes installing the DeepSearch full stack on **macOS** using Docker (one-command bring-up).

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**: macOS 14.0 (Sonoma) or later

* **Software**
  * Git:

    ```
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" # if Homebrew is missing

    brew install git
    ```

  * Docker: Docker Desktop recommended; see below

### Install Docker Desktop

* Download from [Docker Desktop](https://www.docker.com/products/docker-desktop/), **Download for Mac** (.dmg).
* Requirements:
  * Docker Engine 20.10+
  * Docker Compose v2.19.1+
* Open the .dmg and drag **Docker** to **Applications**.
* Launch from Launchpad; enter your password when asked.
* First start may take a few minutes to pull base images.

> For issues, see [Docker Desktop on Mac](https://docs.docker.com/desktop/setup/install/mac-install/).

## 2. Install DeepSearch

### 1. Download the package (skip if you already have it)

* x86_64: [DeepSearch v0.1.9](https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/deployTool_0.1.9_amd64.zip)
* ARM64: [DeepSearch v0.1.9](https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/deployTool_0.1.9_arm64.zip)

### 2. Start DeepSearch

* Create an install folder, extract the zip there, and `cd` into it.

* Upgrade Bash (recommended before running scripts):

  ```
  brew install bash
  ```

* To change the web UI port, see [this guide](https://gitcode.com/openJiuwen/agent-studio/blob/main/scripts/README.md#如何修改前端页面服务的端口号).

- Locate and edit the .env.custom file in the deployment tool directory, then add the following configuration item according to your actual runtime environment:
```
IP=<local IP address of the machine running the deployment tool>
```

* In Terminal, from the folder containing *service.sh*:

  ```bash
  ./service.sh up
  ```

  > Network issues may show “up Plugin + Sandbox Server failed”; run `./service.sh up` again.

* On success you’ll see **Local access:** (and often a network URL).

  > More container details: [deployment tool manual](https://gitcode.com/openJiuwen/agent-studio/blob/main/scripts/README.md#openjiuwen-agent-studio-%E9%83%A8%E7%BD%B2%E5%B7%A5%E5%85%B7%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C).

### 3. Open the app

* Local: use **Local access** in a browser.
* Remote: use the **network access** URL if shown.

* Self-signed HTTPS may trigger a privacy warning; use **Advanced** to continue.
* In **Task space**, select the DeepSearch agent.

## 3. FAQ

### How to stop the service

```
./service.sh down
```
