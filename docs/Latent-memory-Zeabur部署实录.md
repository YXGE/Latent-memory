# Latent-memory Zeabur 部署指南

记录 Latent-memory 部署到 Zeabur，并接入 ChatGPT GPT Actions 与 External Memory 的完整流程。
## 最终架构
```text
ChatGPT GPT
    |
    v
GPT Actions
    |
    v
Zeabur Latent-memory 服务
    |
    v
/data Volume
    |
    v
持久化 memory 数据
```

## 1. Git 部署准备

### 问题：git 没有提交记录

现象：

```bash
git log
```

没有历史提交。

原因：

部署目录不是从原仓库 clone，或者初始化后没有正确提交。
解决：
重新 clone 原仓库：
git clone https://github.com/XXX/XXX.git
不要直接在空目录初始化新的仓库作为部署源。
问题：git commit 身份错误
错误：
Author identity unknown
解决：
配置 Git 用户：
git config --global user.name "name"
git config --global user.email "email"
2. Zeabur 环境变量配置
需要配置：
MEMORY_ACTION_API_KEY=<随机密钥>
MEMORY_ACTION_BASE_URL=${ZEABUR_WEB_URL}
生成密钥：
PowerShell 生成：

```powershell
[guid]::NewGuid().ToString("N")
```
注意：

重新部署服务后，如果 API KEY 环境变量发生变化，
ChatGPT Actions 中保存的 Bearer Token 也需要同步更新。
否则会返回：

Unauthorized（未授权）
## 3. Volume 持久化配置

创建 Volume：

Volume ID:

memory-data

Mount Directory:

/data

作用：

保证服务重启、重新部署后 memory 数据不会丢失。

注意：

不要依赖 Zeabur 的 Backup & Restore 功能，
该功能可能需要付费计划。

直接使用 Volume 保存 /data 数据即可。
4. OpenAPI Action 配置
问题：OpenAPI 导入失败
错误：
schemas subsection is not an object
原因：
OpenAPI components 结构不符合规范。

ChatGPT Actions 要求：
components.schemas 必须存在，并且必须是 object。

解决：

在 components 中增加：

"schemas": {}
最终：
```json
{
  "components": {
    "schemas": {},
    "securitySchemes": {
      "bearerAuth": {
        "type": "http",
        "scheme": "bearer"
      }
    }
  }
}
```
## 4. GPT Actions 鉴权失败排查

### 问题：Unauthorized

现象：

GPT Actions 测试返回：

Unauthorized

### 排查：

1. 使用 Zeabur 环境变量中的 `MEMORY_ACTION_API_KEY`
直接测试服务接口。

如果返回：

记忆库是空的，没有可召回的内容

说明：

- 服务运行正常
- API Key 有效
- Volume 和 memory 服务正常

问题位于 GPT Actions 鉴权配置。

### 解决：

GPT Actions → 身份验证：

类型：

API Key

认证方式：

Bearer

值：
填写 MEMORY_ACTION_API_KEY 的实际值

注意：

不要填写：
Bearer xxxxx
只填写密钥本身。
GPT Actions 会自动添加：
Authorization: Bearer <API_KEY>

如果重新部署 Zeabur 服务后重新生成了 API Key，
需要同步更新 GPT Actions 中保存的密钥。
5. 导入已有 memory 数据
问题：服务正常但没有记忆
现象：
记忆库是空的，没有可召回的内容
原因：
新的 Zeabur Volume 默认为空。
导入步骤
本地进入包含 memory 文件夹的目录：

```bash
tar -cf sevis-memory.tar memory
```
生成备份文件：
sevis-memory.tar
上传到容器：
/tmp/sevis-memory.tar
（可通过 Zeabur File Management 上传）
检查压缩包：
```bash
tar -tf /tmp/sevis-memory.tar | head
```
确认存在：
memory/
memory/index/
memory/timeline/
解压到 Volume：
tar -xf /tmp/sevis-memory.tar -C /data
检查恢复结果：
find /data/memory -maxdepth 2 -type f
确认存在
/data/memory/
6. 上传文件失败
错误：
wget: not found
curl: not found
gzip: stdin: unexpected end of file
tar: Child returned status 1
原因：
当前部署镜像内未预装 curl/wget。
如果需要在线下载文件，基础镜像可能缺少 curl/wget。
优先使用 Zeabur File Management 上传，避免依赖容器内下载工具。
解决：
Dockerfile 添加：
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
重新部署后验证：
curl --version
## 7. GPT 不主动读取记忆

问题：

GPT Action 测试成功，但 GPT 不会主动查询 memory。

原因：

GPT Actions 只提供调用能力，不会自动触发。

解决：

在 GPT Instructions 中增加调用规则：

- 新会话开始时读取 memory
- 涉及历史信息、已保存偏好、过去事件时调用 search
- 用户明确要求保存信息时调用 append
- 用户指出记忆错误时先调用 correct
## 8. 最终验证清单

部署完成后确认：

- [x] Zeabur 服务状态：Running
- [x] OpenAPI 可访问
- [x] Bearer 鉴权成功
- [x] GPT Action 调用成功
- [x] memory 数据恢复成功
- [x] 服务重启后数据仍存在
- [x] GPT 可以主动读取记忆
- [x] Volume 数据持久化验证

## 当前部署架构

Latent-memory

+
Zeabur 服务

+
Persistent Volume（持久化存储）

+
GPT Actions

+
External Memory

当前已完成第一版稳定部署。
后续可以继续：
自动备份
管理页面
memory 可视化
收藏馆接入
日记自动归档
