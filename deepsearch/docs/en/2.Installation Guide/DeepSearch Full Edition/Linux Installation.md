This guide describes installing the DeepSearch full stack on **Linux** using Docker (one-command bring-up).

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**
  * Ubuntu: minimum 20.04; **22.04 (Jammy) or newer recommended**
    > **Note**: Ubuntu 20.04 (Focal) and older are out of mainstream support.
  * EulerOS: Huawei Cloud EulerOS 2.0 or newer

* **Software**
  * Docker and Docker Compose: see below

  > Docker images, containers, volumes, and networks default to `/var/lib/docker/`. Many distros do not mount `/var` on a separate disk; it shares the root volume. Consider a dedicated disk or partition for `/var` so a full `/var` does not take down the OS.

### Install Docker and Docker Compose

* Follow the [Docker Engine install guide](https://docs.docker.com/engine/install/) and [Docker Compose install guide](https://docs.docker.com/compose/install/).

* Versions:
  * Docker Engine 20.10+
  * Docker Compose v2.19.1+

* Verify:

    ```
    docker version
    docker-compose version
    ```

## 2. Install DeepSearch (example: Ubuntu 22.04)

### 1. Download the package (skip if you already have it)

* x86_64:

    ```
    wget https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/deployTool_0.1.9_amd64.zip
    ```

* ARM64:

    ```
    wget https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/deployTool_0.1.9_arm64.zip
    ```

### 2. Start DeepSearch

* Place the zip in your install directory.

* Install unzip:

  ```bash
  sudo apt update && sudo apt install unzip -y
  ```

* Extract:

  - x86_64: `unzip deployTool_0.1.9_amd64.zip`
  - ARM64: `unzip deployTool_0.1.9_arm64.zip`

* Enter the `deployTool_0.1.9_*64` folder and check Docker:

  ```bash
  sudo systemctl start docker
  sudo systemctl status docker
  ```

  > If **inactive**, see the Docker install guides above.

* To change the web UI port, see [this guide](https://gitcode.com/openJiuwen/agent-studio/blob/main/scripts/README.md#如何修改前端页面服务的端口号).

- Locate and edit the .env.custom file in the deployment tool directory, then add the following configuration item according to your actual runtime environment:
```
IP=<local IP address of the machine running the deployment tool>
```

* Start:

  ```bash
  ./service.sh up
  ```

  > Network issues may show “up Plugin + Sandbox Server failed”; run `./service.sh up` again.

* On success you’ll see **Local access:** (and often a network URL).

  > More container details: [deployment tool manual](https://gitcode.com/openJiuwen/agent-studio/blob/main/scripts/README.md#openjiuwen-agent-studio-%E9%83%A8%E7%BD%B2%E5%B7%A5%E5%85%B7%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C).

### 3. Open the app

* On the same machine: open **Local access** in a browser.
* From another machine: use the **network access** URL if shown.

* “Not private” warnings are expected with self-signed HTTPS; use **Advanced** to continue.
* In **Task space**, select the DeepSearch agent.

## 3. FAQ

### How to stop the service

```
./service.sh down
```
