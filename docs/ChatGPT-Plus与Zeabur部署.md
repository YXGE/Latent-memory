# ChatGPT Plus + Zeabur 部署

记录 Latent-memory 部署到 Zeabur，并通过 ChatGPT GPT Actions 接入 External Memory 的完整流程。本文合并了正式部署说明与实际部署排错记录，按“准备 → 部署 → 导入 → 接入 → 验收”的顺序整理。

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
/data Persistent Volume
    |
    v
持久化 memory 数据
```

---

## 隐私边界

公开仓库和 Docker 镜像里只能放通用程序。以下内容绝不能提交：

- `memory/`
- `AGENTS.md`
- `CLAUDE.md`
- `persona.md`
- `threads.jsonl`
- `init_state.json`
- `mcp-config.json`
- `.weights.json`
- `.retractions.json`
- `.entities.json`
- `.embed_cache.json`
- `.env`
- API key
- 记忆备份压缩包

这些路径应同时写入 `.gitignore` 与 `.dockerignore`，但提交前仍需检查：

```bash
git status
git diff --cached
```

公开 Git 历史不是秘密存储。

---

## 1. Git 部署准备

### 1.1 使用真实仓库作为部署源

不要在空目录中直接初始化一个新的 Git 仓库并把它当成部署源。应从真实仓库 clone：

```bash
git clone <仓库地址>
cd <仓库目录>
```

检查当前仓库是否有提交历史：

```bash
git log --oneline -5
```

如果部署改动是在另一个临时目录里完成的，建议先在本地做安全备份，再把需要的提交整理到一个干净分支中，避免把无关历史一并推送。

### 1.2 Git 提交身份错误

错误：

```text
Author identity unknown
```

优先为当前仓库单独配置身份：

```bash
git config user.name "name"
git config user.email "email"
```

只有确定希望对所有仓库使用同一身份时，才使用：

```bash
git config --global user.name "name"
git config --global user.email "email"
```

### 1.3 从 GitHub 部署到 Zeabur

1. 在 Zeabur 新建或选择一个 Project。
2. 创建 GitHub Service。
3. 选择 Latent-memory 仓库和需要部署的分支。
4. Zeabur 读取仓库根目录的 `Dockerfile`。
5. 等待构建与部署完成。

如果免费计划不允许手动调整资源上限，保留默认配置即可。默认零依赖检索不会加载本地 embedding 模型。

---

## 2. 挂载持久卷

在导入记忆前，先为服务挂载 Volume。

在 Service → Volumes 中创建：

```text
Volume ID: memory-data
Mount Directory: /data
```

作用：

- 保存 `/data/memory`
- 服务重启后数据仍存在
- 重新部署代码时不覆盖记忆数据

注意：

- 新 Volume 默认是空的。
- 首次挂载后，目标目录内容可能被 Volume 覆盖。
- 必须先挂载，再导入记忆。
- Zeabur 的 Backup & Restore 可能需要付费计划，不能代替本地备份。

---

## 3. 环境变量

在 Service → Variables 中配置：

```env
MEMORY_ACTION_API_KEY=<至少 24 字符的高强度随机密钥>
MEMORY_ACTION_BASE_URL=${ZEABUR_WEB_URL}
```

PowerShell 可生成随机密钥：

```powershell
[guid]::NewGuid().ToString("N")
```

说明：

- `MEMORY_ACTION_API_KEY` 是这项私人服务的访问密码。
- 它不是 OpenAI API key。
- 不会产生 OpenAI API 调用费用。
- 不要把真实密钥提交到 GitHub。
- 不要把真实密钥粘贴到公开聊天或文档中。

Zeabur 会自动注入 `PORT`。Dockerfile 若已监听 `0.0.0.0`，通常无需额外配置 `HOST`。

可选的云端 embedding 变量仅在明确启用云端 embedding 时配置：

```env
MEMORY_EMBED_PROVIDER=cloud
MEMORY_EMBED_ENDPOINT=<服务商 /v1/embeddings 地址>
MEMORY_EMBED_MODEL=<模型名>
MEMORY_EMBED_API_KEY=<服务商 key>
```

默认零依赖路线不需要这些变量，也不要为了“填完整”添加空值。

### API Key 同步注意事项

重新部署本身不应被假定为一定会改变 API Key，但在以下情况后应重新核对当前环境变量：

- 手动修改 Variables
- 重新生成密钥
- 重建服务
- 切换项目或环境

GPT Actions 中保存的 Bearer Token 必须与 Zeabur 当前的 `MEMORY_ACTION_API_KEY` 完全一致，否则会返回：

```text
Unauthorized
```

---

## 4. 域名与 HTTPS

在 Service → Domains 中绑定一个 `*.zeabur.app` 域名。

确认以下地址能通过 HTTPS 访问：

```text
https://<domain>/healthz
https://<domain>/openapi.json
```

说明：

- `healthz` 用于确认服务是否运行。
- `openapi.json` 用于导入 GPT Actions。
- 这两个地址不应暴露私人记忆内容。
- 所有记忆读写端点都必须要求：

```http
Authorization: Bearer <MEMORY_ACTION_API_KEY>
```

---

## 5. OpenAPI 与 GPT Actions

### 5.1 创建私有 GPT

1. 打开 GPT 编辑器。
2. 新建 GPT。
3. Visibility 设置为 `Only me`。
4. 在 Instructions 中加入稳定人格规则与记忆工具调用约定。
5. Actions → Create new action。
6. 导入：

```text
https://<domain>/openapi.json
```

### 5.2 OpenAPI 导入失败

错误：

```text
schemas subsection is not an object
```

原因：

ChatGPT Actions 要求 `components.schemas` 存在，并且必须是 object。

正确结构示例：

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
  },
  "security": [
    {
      "bearerAuth": []
    }
  ]
}
```

注意：

- `schemas` 必须是 `{}`，不能省略或写成其他类型。
- `securitySchemes` 中定义 Bearer。
- 根级别 `security` 应引用 `bearerAuth`。
- 最好在服务端生成的 `openapi.json` 中永久修正，而不是每次导入后手动修改。

### 5.3 GPT Actions 鉴权配置

在 GPT Actions 的 Authentication 中选择：

```text
类型：API Key
认证方式：Bearer
值：填写 MEMORY_ACTION_API_KEY 的实际密钥值
```

不要填写：

```text
Bearer xxxxx
```

只填写密钥本身。GPT Actions 会自动发送：

```http
Authorization: Bearer <API_KEY>
```

### 5.4 Unauthorized 排查

如果 GPT Actions 测试返回：

```text
Unauthorized
```

先直接测试服务端鉴权。

PowerShell：

```powershell
$key = Read-Host "粘贴 Zeabur 当前的 MEMORY_ACTION_API_KEY"

Invoke-RestMethod `
  -Method Get `
  -Uri "https://<domain>/v1/session/start" `
  -Headers @{ Authorization = "Bearer $key" }
```

如果返回类似：

```text
记忆库是空的，没有可召回的内容
```

说明：

- 服务正在运行
- API Key 有效
- Bearer Header 格式正确
- 问题在 GPT Actions 中保存的密钥或鉴权配置

此时重新检查：

- GPT Actions 是否选择 API Key
- 是否选择 Bearer
- 是否只填了密钥本身
- GPT Actions 中的密钥是否与 Zeabur 当前值一致

---

## 6. 导入已有 memory 数据

### 6.1 现象

服务正常，但查询返回：

```text
记忆库是空的，没有可召回的内容
```

原因：

新挂载的 `/data` Volume 默认没有旧记忆数据。

### 6.2 本地打包

进入包含 `memory/` 文件夹的目录：

```bash
tar -cf sevis-memory.tar memory
```

归档中必须包含顶层：

```text
memory/
```

可选包含：

```text
threads.jsonl
```

不要把备份压缩包提交到 GitHub。

### 6.3 上传

通过 Zeabur File Management 上传到：

```text
/tmp/sevis-memory.tar
```

### 6.4 解压前检查

先确认文件存在并查看大小：

```bash
ls -lh /tmp/sevis-memory.tar
```

检查归档内容：

```bash
tar -tf /tmp/sevis-memory.tar | head -20
```

应看到类似：

```text
memory/
memory/.weights.json
memory/index/
memory/timeline/
```

### 6.5 解压到 Volume

```bash
tar -xf /tmp/sevis-memory.tar -C /data
```

也可以使用 Python：

```bash
python -m tarfile -e /tmp/sevis-memory.tar /data
```

### 6.6 解压后检查

```bash
find /data/memory -maxdepth 2 -type f | head -20
```

确认存在实际记忆文件后，重启服务。

### 6.7 持久化验证

1. 通过 GPT Actions 成功召回旧记忆。
2. 重启 Zeabur 服务。
3. 再次召回同一条记忆。
4. 确认数据没有消失。

---

## 7. 文件上传与解压失败

可能出现：

```text
wget: not found
curl: not found
gzip: stdin: unexpected end of file
tar: Child returned status 1
tar: Error is not recoverable
```

这类错误可能表示：

- 基础镜像未安装 `curl` 或 `wget`
- Zeabur 上传/解压辅助流程依赖下载工具
- 文件上传不完整
- 归档流被截断
- 自动解压失败

处理顺序：

1. 优先通过 File Management 上传文件。
2. 检查 `/tmp/sevis-memory.tar` 的实际大小。
3. 使用 `tar -tf` 验证归档。
4. 验证通过后再手动解压到 `/data`。

如果镜像需要 `curl`，在 Dockerfile 中添加：

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
```

重新部署后验证：

```bash
curl --version
```

注意：

安装 `curl` 只能解决镜像缺少工具的问题。若文件本身未完整上传，仍需重新上传并再次检查归档。

---

## 8. GPT 主动使用记忆

GPT Actions 只提供调用能力，不会自动保证模型在正确时机调用。

在 GPT Instructions 中加入明确规则：

```text
新会话第一次实质回复前调用 startMemorySession。

谈到过去事件、约定、日期、地点、人名或拿不准的细节时，先调用
searchLongTermMemory；查过仍没有可靠命中才如实说明记录里没找到。

出现新约定、重要事件、状态变化或明确的“记住”要求时，立刻调用
appendLongTermMemory，并填写准确的 current_state，不要拖到聊天结束。

用户指出旧记录错误或过时时，先检索并逐字取得唯一 quote，再调用
correctLongTermMemory。

用户明确结束聊天时调用 closeMemorySession；不要把重要写入推迟到这一步，
因为直接关页时未必还有工具调用机会。

自然使用记忆，除非被问到机制，否则不要播报调用过程。
```

对应操作通常包括：

- `startMemorySession`
- `searchLongTermMemory`
- `appendLongTermMemory`
- `correctLongTermMemory`
- `closeMemorySession`

---

## 9. 验收

### 基础服务

- [x] Zeabur 服务状态为 Running
- [x] HTTPS 域名可访问
- [x] `/healthz` 可访问
- [x] `/openapi.json` 可访问
- [x] Volume 挂载到 `/data`

### GPT Actions

- [x] OpenAPI 成功导入
- [x] `components.schemas` 为 object
- [x] Bearer 鉴权成功
- [x] Action 测试调用成功

### 记忆数据

- [x] `memory` 数据成功导入
- [x] GPT 能召回旧记忆
- [x] GPT 能写入新记忆
- [x] GPT 能更正旧记忆
- [x] 服务重启后数据仍存在
- [x] GPT 能主动调用记忆工具

### 建议的完整验收流程

1. 在全新会话里询问一件只存在于旧记忆中的事，不提示工具名。
2. 说一个无害且独特的新事实，并明确要求记住。
3. 新开会话，换一种说法询问该事实。
4. 更正该事实。
5. 再次查询，确认旧版本不会作为当前记忆返回。
6. 重启 Zeabur 服务，再重复一次查询。
7. 确认重启后数据仍然存在。

如果第 1 步失败，先检查 GPT Instructions 中的主动检索约定，不要先修改检索算法。

---

## 当前状态

当前部署架构：

```text
Latent-memory
+
Zeabur Service
+
Persistent Volume
+
GPT Actions
+
External Memory
```

第一版稳定部署完成。

后续可继续扩展：

- 自动备份
- 管理页面
- memory 可视化
- 收藏馆接入
- 日记自动归档
