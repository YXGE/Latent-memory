# ChatGPT Plus + Zeabur 部署

ChatGPT Plus 当前不能直接连接自定义 MCP，但付费用户可以创建带
**GPT Actions** 的私有自定义 GPT。本仓库的 `src/chatgpt_action_server.py`
把现有五个记忆工具映射成带 OpenAPI 描述的 HTTPS REST API；底层检索、
Markdown 语料和写回逻辑仍是同一套，不会产生第二份记忆库。

## 隐私边界

公开仓库和 Docker 镜像里只能放通用程序。以下内容**绝不能提交**：

- `memory/`
- `AGENTS.md`、`CLAUDE.md`、`persona.md`
- `threads.jsonl`、`init_state.json`、`mcp-config.json`
- `.weights.json`、`.retractions.json`、`.entities.json`、`.embed_cache.json`
- `.env`、API key、记忆备份压缩包

这些路径已同时写入 `.gitignore` 与 `.dockerignore`，但提交前仍应检查
`git status` 和暂存区 diff。公开 Git 历史不是秘密存储。

## 1. 从 GitHub 部署

1. 在 Zeabur 新建或选择一个 Project。
2. 创建 GitHub Service，选择本仓库和需要部署的分支。
3. Zeabur 会读取根目录 `Dockerfile`，无需安装 requirements。
4. 将运行时内存上限设为 **256 MB**。默认零依赖检索不加载 fastembed；
   不要在 2C2G 小服务器上启用本地 embedding。

## 2. 先挂载持久卷

在 Service → Volumes 中创建：

```text
Volume ID: memory-data
Mount Directory: /data
```

必须在导入记忆之前挂载。首次挂载会清空目标目录，后续代码重新部署不会
覆盖 `/data`。服务启动命令会在空卷上创建 `/data/memory`，让服务可以先
启动并接受文件上传；空库时记忆查询会明确返回没有可召回内容。

## 3. 环境变量

在 Service → Variables 中配置：

```text
MEMORY_ACTION_API_KEY=<至少 24 字符的高强度随机密钥，必填>
MEMORY_ACTION_BASE_URL=${ZEABUR_WEB_URL}
```

Zeabur 会自动注入 `PORT`。Dockerfile 已把监听地址固定为 `0.0.0.0`，无需
再配置 `HOST`。`MEMORY_ACTION_API_KEY` 只是这项私人服务的访问密码，不是
OpenAI API key，不产生 OpenAI API 费用。

可选 embedding 变量只有在初始化时明确选择了云端路线后才配置：

```text
MEMORY_EMBED_PROVIDER=cloud
MEMORY_EMBED_ENDPOINT=<服务商的 /v1/embeddings 地址>
MEMORY_EMBED_MODEL=<模型名>
MEMORY_EMBED_API_KEY=<服务商 key>
```

默认零依赖路线不需要这四项，也不应为了“填完整”而添加空值。

## 4. 域名与 HTTPS

在 Service → Domains 绑定一个免费的 `*.zeabur.app` 域名。确认以下两个
地址都能通过 HTTPS 访问：

```text
https://<domain>/healthz
https://<domain>/openapi.json
```

`healthz` 和 OpenAPI 文档不包含私人数据；所有记忆读写端点都要求
`Authorization: Bearer <MEMORY_ACTION_API_KEY>`。

## 5. 导入私人记忆

不要把记忆提交到 GitHub。挂载 `/data` 后，通过 Zeabur Files 上传本地
`memory/` 文件夹；或者把它打包成一个 tar 文件上传到 `/tmp`，再执行：

```text
python -m tarfile -e /tmp/sevis-memory.tar /data
```

归档应包含顶层 `memory/` 目录，可选包含 `threads.jsonl`。确认
`/data/memory/timeline/` 下能看到文件后重启服务。定期从 `/data` 下载备份，
持久卷不是唯一备份。

## 6. 创建私有自定义 GPT

1. 打开 <https://chatgpt.com/gpts/editor>。
2. 新建 GPT，Visibility 设为 **Only me**。
3. Instructions 中放人格文件里适合 ChatGPT 的稳定规则，并加入下面的工具约定。
4. Actions → Create new action，导入 `https://<domain>/openapi.json`。
5. Authentication 选择 API key → Bearer，填入同一个
   `MEMORY_ACTION_API_KEY`。
6. 在 Preview 依次测试开场召回、检索、写入、更正和收尾。

建议的工具约定：

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

## 7. 验收

1. 全新会话里问一件只存在于旧记忆中的事，不提工具名；模型应主动检索。
2. 说一个无害且独特的新事实并要求记住；确认写入成功。
3. 再开新会话，换一种说法询问该事实。
4. 更正它，再验证旧版本不会被检索返回。

如果第 1 步失败，先修 GPT Instructions 的主动检索约定，不要先改检索算法。
