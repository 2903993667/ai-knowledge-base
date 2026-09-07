# AI 知识库 (AI Knowledge Base)

AI 驱动的智能数据库软件，支持多格式文件导入、智能搜索、知识库问答。

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python -m uvicorn app:app --host 127.0.0.1 --port 8800 --reload

# 访问界面
# http://127.0.0.1:8800
```

## 功能概览

### 📥 智能导入
- 拖放上传文件，支持 20+ 格式：CSV, JSON, JSONL, Excel, PDF, Word, Markdown, HTML, XML, YAML, TXT
- **未知格式自动学习**：AI 分析文件结构 + 联网搜索解析方法 → 自动生成解析器技能 → 保存复用
- AI 将原始数据整理为结构化 Markdown，分块存储

### 🔍 多模式搜索
- **关键词搜索**：SQLite FTS5 全文检索
- **语义搜索**：Embedding 向量余弦相似度匹配（需配置 Embedding 模型）
- **混合搜索**：关键词 + 语义，RRF 融合排序
- **AI 智能搜索**：AI 拆解查询 → 多关键词 + 语义搜索 → AI 重排序

### 💬 知识库问答
- RAG 模式：检索相关文档分块 → AI 生成带引用的答案
- 支持流式输出 (SSE)
- 纯生成模式：无需知识库的通用 AI 对话

### 🧩 技能系统
- **内置技能**：CSV, JSON, Excel, PDF, Word, Markdown, HTML, XML, YAML, Text
- **AI 学习技能**：遇到未知格式时，AI 自动学习解析方法并保存为可复用技能
- 技能热加载，支持自定义扩展

### ⚙️ 灵活配置
- AI 对话模型：任意 OpenAI 兼容 API（OpenAI, DeepSeek, Ollama, vLLM, SiliconFlow 等）
- Embedding 模型：可选，用于语义搜索
- Reranker 模型：可选，用于搜索结果重排序（推荐 BAAI/bge-reranker-v2-m3）
- 搜索 API：Tavily / Bing / 自定义端点
- 所有配置可在页面内设置并测试连接

## 目录结构

```
数据库/
├── app.py                    # FastAPI 主应用
├── core/
│   ├── database.py           # SQLite 数据层
│   ├── ai.py                 # AI 客户端（对话/Embedding/Reranker/搜索）
│   ├── search.py             # 搜索引擎（关键词/语义/混合/AI）
│   ├── chat.py               # RAG 问答（含 Reranker 优化）
│   ├── importer.py           # 文件导入管线（智能分块+重叠）
│   └── skill_loader.py       # 技能动态加载器
├── skills/
│   ├── builtin/              # 内置解析技能
│   └── learned/              # AI 学习生成的技能
├── static/
│   └── index.html            # 前端界面
├── data/
│   ├── database.sqlite       # 数据库文件（运行后生成）
│   ├── uploads/              # 上传文件存储
│   ├── images/               # 提取的图片
│   └── logs/                 # 运行日志
├── requirements.txt
└── README.md
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/documents/upload | 上传导入文件 |
| GET | /api/documents | 文档列表 |
| GET | /api/documents/:id | 文档详情 |
| DELETE | /api/documents/:id | 删除文档 |
| GET | /api/search?q=&mode= | 搜索 |
| POST | /api/chats | 创建对话 |
| GET | /api/chats | 对话列表 |
| POST | /api/chats/:id/messages | 发送消息 (支持 SSE) |
| GET/POST | /api/settings | 读写设置 |
| POST | /api/settings/test-ai | 测试 AI 连接 |
| POST | /api/settings/test-embedding | 测试 Embedding |
| POST | /api/settings/test-reranker | 测试 Reranker |
| POST | /api/settings/test-search | 测试搜索 API |
| GET | /api/skills | 技能列表 |
| GET | /api/stats | 统计信息 |

## 技术栈

- **后端**：Python 3.11+, FastAPI, SQLite (FTS5), httpx
- **前端**：原生 HTML/CSS/JavaScript, SSE
- **AI**：OpenAI 兼容 API (支持多模型)
- **搜索**：FTS5 全文检索 + Embedding 语义搜索 + RRF 混合排序 + Reranker 重排序

## 优化特性

1. **智能分块**：按段落切分，chunk_size=800, chunk_overlap=200，避免关键信息被切断
2. **中英文查询扩展**：AI 自动将中文查询翻译成英文搜索词
3. **Reranker 重排序**：使用交叉编码器对搜索结果重排序，提高相关性
4. **多级检索**：搜索 → Reranker → 上下文构建，确保关键信息不丢失
5. **严谨问答**：System Prompt 优化，处理对照实验、突变体逻辑推导等复杂场景
