# LLM 多渠道接入设计

日期:2026-07-11
状态:已确认
背景:当前 `agent_config` 单行配置只有一组 `provider/base_url/api_key_enc`,主模型与视觉模型共用同一渠道。用户需要文本与视觉分别接入不同渠道(例:文本走 OpenRouter 的 qwen3.7-max,视觉走支持图像输入的其他渠道/模型)。

## 目标

- 渠道(provider + base_url + api_key)成为可管理的一等实体,可增删多条。
- 主模型槽与视觉模型槽各自选择「渠道 + 模型名」。
- 升级无感:现有配置自动迁移为一条默认渠道,不需重新填 key。

## 非目标(YAGNI)

- 渠道级限流/配额
- 每渠道多 key 轮换
- 按工具/按任务选择模型

## 1. 数据模型

新表(追加到 `backend/database.py` SCHEMA 末尾,append-only 约定):

```sql
CREATE TABLE IF NOT EXISTS llm_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai','anthropic')),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_enc TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`_migrate()` 为 `agent_config` 追加两列:

- `channel_id INTEGER` —— 主模型渠道
- `vision_channel_id INTEGER` —— 视觉渠道;NULL = 跟随主渠道

旧的 `provider/base_url/api_key_enc` 三列保留为休眠列(与 `agent_runs` 同策略),代码不再读写。

**启动迁移**:`_migrate()` 中,若 `llm_channels` 为空且 `agent_config.api_key_enc` 非空,
把现有 `provider/base_url/api_key_enc` 复制为一条 `name='默认渠道'` 的记录,并将
`channel_id` 指向它;`vision_channel_id` 留 NULL(跟随主渠道,与迁移前行为等价)。
迁移幂等:仅在 `llm_channels` 为空时执行。

## 2. 后端

### agent/config.py

- 新增 `Channel` dataclass:`id, name, provider, base_url, api_key`(解密后)。
- `AgentConfig` 增加 `main_channel: Channel | None` 与 `vision_channel: Channel | None`;
  `load_config()` 做 JOIN 解析,`vision_channel_id` 为 NULL 时 `vision_channel = main_channel`。
- 渠道 CRUD:`list_channels() / get_channel(id) / save_channel(data) / delete_channel(id)`。
  key 写入语义沿用现状:`None` = 不改动,`""` = 显式清除。
- `save_config` 的 FIELDS 更新为含 `channel_id / vision_channel_id`(均允许显式置 NULL),
  移除 `provider / base_url / api_key` 的写入路径。

### agent/llm.py

`build_model(channel, model_name)`:从渠道对象 + 模型名构建。渠道缺失或无 key 时抛
`ValueError`(沿用现有错误路径)。调用点共三处,一并迁移:

- `chat/runtime.py`:`build_model(cfg.main_channel, cfg.model)`
- `chat/vision.py describe_image`:`build_model(cfg.vision_channel, cfg.vision_model)`
- `api/agent.py` 探测端点

### api/agent.py

- `GET /agent/channels` —— 列表;每条含 `has_api_key`,不回传明文。
- `POST /agent/channels` / `PUT /agent/channels/{id}` —— 建/改。
- `DELETE /agent/channels/{id}` —— 被 `channel_id` 或 `vision_channel_id` 引用时返回 409。
- `POST /agent/models` —— body 含 `channel_id` 或 inline `provider/base_url/api_key`
  覆盖(支持未保存渠道先测),按该渠道拉模型列表。
- `POST /agent/test` —— 主槽用「主渠道×主模型」跑 `_probe_text`,视觉槽用
  「视觉渠道×视觉模型」跑 `_probe_vision`;结果各自带渠道名,如
  `视觉 ❌ [DashScope] ...`。
- `AgentConfigBody`:`provider/base_url/api_key` 替换为
  `channel_id: Optional[int]` / `vision_channel_id: Optional[int]`。

### 红线不变

`backend/agent/` import 链禁止下单函数;渠道 key 用与交易凭据相同的 Fernet 加密
(`trading.credentials.encrypt_secret/decrypt_secret`),轮换 `SECRET_KEY` 作废已存 key
的运维行为保持不变。

## 3. 前端 Settings(Agent 配置区重排)

- **渠道管理卡片**(新增,置于 Agent 区顶部):渠道列表展示名称/provider/base_url/key
  状态;行内新增、编辑、删除;每行「测试」钮做纯文本探测(inline 覆盖,支持未保存先测)。
- **主模型槽**:渠道下拉 + 模型名输入 + 现有模型选择器(picker 以所选渠道调 `/models`)。
- **视觉槽**:渠道下拉(首项「跟随主渠道」)+ 模型名输入 + picker。
- 「测试连接」按钮与结果格式不变,两行结果各自标注渠道名。
- `api/client.js` 增加渠道 CRUD 方法;`/models`、`/test` 请求体按新 ProbeBody 调整。

## 4. 错误处理

- 槽位引用的渠道不存在 / 渠道无 key:`load_config` 解析为 `main_channel=None`,
  `run_turn` 抛 RuntimeError → 走现有可重试 assistant 错误消息路径,不新增机制。
- 视觉渠道失效仅影响视觉中继与 `capture_chart` 注册(`cfg.vision_model` 且
  `vision_channel` 可用才注册),不拖垮主对话。

## 5. 测试

pytest 覆盖:

1. 迁移:旧配置存在 → 自动生成默认渠道且两槽位指向正确;重复启动幂等。
2. 渠道 CRUD:key `None`/`""`/新值三种语义;`has_api_key` 不泄露明文。
3. 删除被引用渠道 → 409;未引用 → 删除成功。
4. `load_config`:vision_channel_id NULL 回落主渠道;引用失效渠道 → main_channel None。
5. `build_model`:openai(带/不带 base_url)与 anthropic 两条构建路径。
