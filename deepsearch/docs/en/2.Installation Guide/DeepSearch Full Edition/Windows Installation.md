This guide describes installing the DeepSearch full stack on **Windows** using Docker (one-command bring-up).

## 1. Environment preparation

Ensure the machine meets:

* **Hardware**
  * CPU: minimum 2 cores; 4+ recommended
  * RAM: minimum 4 GB; 8 GB+ recommended

* **OS**: Windows 10 or later

* **Software**
  * Git: [download](https://mirrors.huaweicloud.com/git-for-windows/v2.51.0.windows.1/Git-2.51.0-64-bit.exe) and install with default options
  * Docker: Docker Desktop recommended; see below

### Install Docker Desktop

*On Windows, Docker Desktop works best with WSL 2 (Windows Subsystem for Linux 2) as the backend: better compatibility, lower overhead than LinuxKit, and avoids known “zombie container” issues.*

**1. Install WSL**

On supported Windows (Windows 10 version 2004+ / build 19041+, or Windows 11), run `wsl --install` for a one-step WSL setup.

* Press Windows + S, search for PowerShell.
* Right-click **Windows PowerShell** → **Run as administrator**.
* Run:

  ```
  wsl --install
  ```

  When you see that changes will take effect after restart, reboot. First install may take a while.

Older Windows may need extra steps; see [Install Linux on Windows with WSL](https://learn.microsoft.com/en-us/windows/wsl/install).

**2. Install Docker Desktop**

* Download the Windows installer from the [Docker Desktop site](https://www.docker.com/products/docker-desktop/) (choose AMD64 on x86). Prefer a recent version meeting:
  * Docker Engine 20.10+
  * Docker Compose v2.19.1+
* Run the installer: **only** check **Use WSL 2 instead of Hyper-V** and **Add shortcut to desktop**, then **OK**.
* Reboot when prompted.
* Open Docker Desktop after reboot; first launch may take 5–10 minutes.
* For trials you can use **Continue without signing in**; for ongoing use see [Docker sign-in](https://docs.docker.com/desktop/setup/sign-in).

> For errors or official steps, see [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

## 2. Install DeepSearch

### 1. Download the package (skip if you already have it)

* Download the matching architecture:

  x86_64: [DeepSearch v0.1.9](https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/deployTool_0.1.9_amd64.zip)

  ARM64: [DeepSearch v0.1.9](https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/deployTool_0.1.9_arm64.zip)

### 2. Docker Desktop: Virtual file shares

* Create an install folder, e.g. `D:\DeepSearch`.
* Docker Desktop → **Settings** (gear).
* **Resources** → **File sharing**: add your DeepSearch install path, click **+**, then **Apply & restart**.

### 3. Start DeepSearch

* Put the zip in the install folder and extract.
* Open **Git Bash** in the folder containing *service.sh* and verify Docker:

  ```bash
  docker info >nul 2>&1 && (echo Docker Desktop is running) || (echo Docker Desktop is not running)
  ```

  > If not running, see [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

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

* On success you’ll see **Local access:** with a URL.

  > More container details: [deployment tool manual](https://gitcode.com/openJiuwen/agent-studio/blob/main/scripts/README.md#openjiuwen-agent-studio-%E9%83%A8%E7%BD%B2%E5%B7%A5%E5%85%B7%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C).

### 4. Open the app

Paste the **Local access** URL into your browser.

* You may see “Your connection is not private” because HTTPS uses a self-signed certificate. That warns the cert is not from a public CA, not necessarily that the site is unsafe.
* Use **Advanced** → proceed to the site.
* In **Task space**, select the DeepSearch agent.

## 3. FAQ

### How to stop the service

```
./service.sh down
```
