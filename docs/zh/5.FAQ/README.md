# FAQ

## 一、安装环境问题

### 1. uv 下载python超时报错

**> 报错信息**：

	Caused by: error decoding response body
	Caused by: request or response body error
	Caused by: operation timed out
![image1.png](https://raw.gitcode.com/user-images/assets/8895323/4153cb74-f65e-4dfe-b0f7-1118402f0348/image1.png 'image1.png')

**> 解决方案**：设置环境变量

① 配置镜像源
```sh
# 配置python下载镜像源（阿里源）
UV_PYTHON_INSTALL_MIRROR="https://registry.npmmirror.com/-/binary/python-build-standalone" uv python install
# 或者
uv python install --index https://registry.npmmirror.com/-/binary/python-build-standalone

其它可选源：
https://python-standalone.org/mirror/astral-sh/python-build-standalone/
```

② 或者增加超时时间
```sh
# 增加超时时间为300s
UV_HTTP_TIMEOUT=300 uv python install <可指定版本>
```


### 2. uv 下载python三方包超时报错

**> 报错信息**：

	╰─▶ I/O operation failed during extraction
	╰─▶ Failed to download distribution due to network timeout. Try increasing UV_HTTP_TIMEOUT (current value: 30s).

![image.png](https://raw.gitcode.com/user-images/assets/8895323/29f0f4a7-6e5d-4876-bc3d-8f6fad4b2367/image.png 'image.png')

**> 解决方案**：配置python三方包镜像源
```sh
# 使用镜像源下载三方 python package（设置环境变量）
UV_INDEX_URL=http://mirrors.aliyun.com/pypi/simple/ uv add <package>
```


### 3. uv 下载ssl证书报错（针对未知网址域名）
  
**> 报错信息**：

	Caused by: client error (Connect)
	Caused by: invalid peer certificate: UnknownIssuer
![image3.png](https://raw.gitcode.com/user-images/assets/8895323/15933480-3a39-4b9a-9c1c-2ed743b2e029/image3.png 'image3.png')

**> 解决方案**：临时忽略不安全域名的证书认证，允许与目标主机的不安全连接

```sh
# --allow-insecure-host 也可替换为 --trusted-host
uv sync --allow-insecure-host github.com --allow-insecure-host pypi.org --allow-insecure-host files.pythonhosted.org
```

## 二、日志定位问题
### 1. 日志路径
openJiuwen-deepsearch运行日志文件通常位于项目根路径的 **output/logs/common** 下，系统实现了日志分流，包含两类日志：
- warnning级别以上（方便快速定位错误日志）：**common_warnning.log**
- 全部级别日志：**common.log**
### 2. 节点异常影响范围
部分环节的模型调用重试失败影响不是很大，部分关键节点的重试失败可能有较大影响。可以判断日志的关键字眼来判断是否有影响。

- 影响较大的节点：可能会影响报告生成的完整性
```
entry：影响是否进行报告生成
outliner：影响整个报告大纲生成
planner：影响报告某一章节的任务规划，进而影响该章节的生成
sub_reportor：影响报告某一章节的生成
reportor：影响最后整个报告的的生成
```
- 影响较小的节点：对报告生成不会产生严重影响的
```
summary：某次搜索任务的总结，只影响当次搜索结果
reflection：某次搜索任务的反思，只影响当次搜索的深度
citation verify：某条搜索结果的溯源效验
```

### 3. 错误码信息
包含公共类型错误、业务节点相关错误码：[详细错误码链接](https://gitcode.com/zmh0531/test-deepsearch/blob/dev/jiuwen_deepsearch/common/status_code.py)