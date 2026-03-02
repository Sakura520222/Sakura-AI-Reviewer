# 🌸 Sakura AI Reviewer

> 基于 AI 的智能 GitHub Pull Request 代码审查机器人，具备主动探索代码库的能力

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Latest-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ 核心特性

### 🤖 AI 驱动的深度审查
- **AI 推理模式**：利用 AI 的推理能力进行深度代码分析
- **主动探索代码库**：AI 可以自主调用工具查看项目结构和任意文件
- **跨文件依赖理解**：通过多轮对话理解模块间的复杂依赖关系
- **上下文感知**：不局限于 PR diff，AI 具备"全域视野"

### 🛠️ AI 函数工具系统
- **read_file**：查看任意文件的完整内容
- **list_directory**：列出目录结构，了解项目组织
- **智能决策**：AI 根据需要主动调用工具

### 📊 自适应审查策略
- **⚡️ 快速审查**：小改动（≤5 文件，≤200 行）
- **🔍 标准审查**：中等改动（≤20 文件，≤1000 行）
- **🔬 深度审查**：大改动（≤100 文件），分批处理
- **⏭️ 智能跳过**：文档、配置文件自动跳过

### 🎯 结构化审查报告
- **整体评分**：代码质量评分（1-10 分）
- **分类问题**：严重问题 🔴、重要建议 🟡、优化建议 💡
- **详细摘要**：变更概述和主要发现
- **可操作建议**：提供具体的改进方案

---

## 🎬 实际效果展示

### AI 审查流程实录

```
✅ 第 1 轮：调用 list_directory 查看项目结构
✅ 第 2 轮：调用 read_file 阅读 bot.py（主程序）
✅ 第 3 轮：调用 read_file 阅读 config.py（配置）
✅ 第 4 轮：调用 read_file 阅读 database.py（数据层）
✅ 第 5 轮：综合分析，生成 8 条有价值的审查评论
```

**AI 能力**：
- 🔍 主动探索项目架构
- 📖 阅读关键文件源码
- 🧠 理解跨文件依赖
- 💡 提供专业建议

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                        GitHub PR                             │
└──────────────────────┬──────────────────────────────────────┘
                       │ Webhook
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Web Server                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Webhook    │  │   PR 分析器   │  │  评论服务    │      │
│  │   Handler    │  │  (策略选择)   │  │  (发布结果)  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     AI 审查引擎                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         AI (推理模式)                       │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐    │   │
│  │  │ read_file  │  │list_dir    │  │ 多轮对话   │    │   │
│  │  │   工具     │  │   工具      │  │   推理     │    │   │
│  │  └────────────┘  └────────────┘  └────────────┘    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据存储层                                │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │    MySQL     │  │    Redis     │                        │
│  │  (审查记录)   │  │   (队列)     │                        │
│  └──────────────┘  └──────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

**技术栈**：
- **后端框架**：FastAPI (Python 3.11+)
- **AI 模型**：DeepSeek-R1 (推理模式)
- **数据库**：MySQL 8.0 + Redis
- **GitHub 集成**：GitHub App (PyGithub)
- **部署**：Docker + Docker Compose

---

## 🚀 快速开始

### 1. 环境要求

- Linux 服务器（推荐 Ubuntu 20.04+）
- Docker 和 Docker Compose
- 公网 IP 和域名
- GitHub 账号
- DeepSeek API Key（或其他 OpenAI 兼容 API）

### 2. 克隆项目

```bash
git clone https://github.com/Sakura520222/Sakura-AI-Reviewer.git
cd Sakura-AI-Reviewer
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# GitHub App 配置
GITHUB_APP_ID=your_github_app_id
GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=your_webhook_secret

# DeepSeek API 配置
OPENAI_API_BASE=https://api.deepseek.com
OPENAI_API_KEY=your_deepseek_api_key
OPENAI_MODEL=deepseek-reasoner

# 数据库配置
DATABASE_URL=mysql+aiomysql://pr_reviewer:your_password@mysql:3306/pr_reviewer

# Redis 配置
REDIS_URL=redis://redis:6379/0

# 应用配置
APP_DOMAIN=your-domain.com
APP_PORT=8000
```

### 4. 创建 GitHub App

详见 [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)

### 5. 启动服务

```bash
cd docker
docker-compose up -d
```

### 6. 验证部署

```bash
curl http://your-domain.com:8000/health
```

应返回：

```json
{
  "status": "healthy",
  "service": "Sakura AI Reviewer"
}
```

---

## 📖 使用说明

### 安装 GitHub App

1. 在 GitHub 上创建并安装你的 App
2. 选择要启用审查的仓库
3. App 会自动接收 Webhook 事件

### 创建 PR

1. 在已安装 App 的仓库中创建 Pull Request
2. Sakura AI 会立即开始审查（会显示"正在审查中..."占位评论）
3. AI 可能会主动查看项目文件（通过工具调用）
4. 审查完成后，占位评论会被替换为完整的审查报告

### 查看审查报告

审查报告包含：

- **📊 整体评分**：代码质量评分（1-10 分）
- **📝 审查摘要**：变更概述和主要发现
- **🔴 严重问题**：必须修复的问题
- **🟡 重要建议**：推荐改进
- **💡 优化建议**：代码优化建议

---

## 🔧 配置说明

### 审查策略配置

编辑 `config/strategies.yaml`：

```yaml
strategies:
  quick:
    name: "⚡️ 快速审查"
    conditions:
      max_files: 5
      max_lines: 200
    prompt: |
      你是一个经验丰富的代码审查专家...
```

### 文件过滤规则

```yaml
file_filters:
  skip_extensions:
    - .md
    - .txt
    - .json
  skip_paths:
    - node_modules/
    - vendor/
    - .venv/
```

### AI 工具配置

在 `.env` 中配置：

```env
# 启用 AI 工具增强功能
ENABLE_AI_TOOLS=true

# 最大工具调用次数（防止无限循环）
MAX_TOOL_ITERATIONS=10
```

---

## 📊 监控和日志

### 查看应用日志

```bash
# 查看所有日志
docker-compose logs -f

# 只查看 Web 服务日志
docker-compose logs -f web
```

### 查看 AI 工具调用

日志会显示 AI 的工具调用过程：

```
INFO | 执行工具 list_directory: {"directory": "."}
INFO | 执行工具 read_file: {"file_path": "bot.py"}
INFO | AI审查完成（使用了5轮对话）
```

### 数据库查询

```bash
docker exec -it pr-reviewer-mysql mysql -u pr_reviewer -p
```

---

## 🛠️ 故障排查

### 常见问题

#### 1. Webhook 签名验证失败
- 检查 `.env` 中的 `GITHUB_WEBHOOK_SECRET`
- 确保在 GitHub App 中设置了相同的 Secret

#### 2. AI 审查失败
- 检查 DeepSeek API 密钥是否有效
- 确认 API 端点地址可访问
- 查看应用日志获取详细错误信息

#### 3. 评论发布失败
- 确保 GitHub App 有写入权限
- 检查 PyGithub 版本兼容性
- 查看日志中的具体错误信息

### 日志级别调整

在 `.env` 中设置：

```env
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
```

---

## 🔐 安全建议

1. **保护敏感信息**
   - 不要将 `.env` 文件提交到版本控制
   - 使用强密码作为数据库和 Webhook 密钥
   - 定期轮换 API 密钥

2. **限制访问**
   - 使用防火墙限制数据库端口访问
   - 配置 Nginx 反向代理启用 HTTPS
   - 限制 GitHub App 的仓库访问权限

3. **定期更新**
   - 及时更新 Docker 镜像
   - 定期更新依赖包
   - 关注安全公告

---

## 📝 开发指南

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
cp .env.example .env

# 运行应用
python -m backend.main
```

### 代码结构

```
Sakura-AI-Reviewer/
├── backend/
│   ├── api/           # API 路由
│   ├── core/          # 核心配置
│   ├── models/        # 数据模型
│   ├── services/      # 业务逻辑
│   │   ├── ai_reviewer.py      # AI 审查引擎
│   │   ├── pr_analyzer.py      # PR 分析器
│   │   └── comment_service.py  # 评论服务
│   └── workers/       # 后台任务
├── config/            # 配置文件
├── docker/            # Docker 配置
└── logs/              # 日志文件
```

### 运行测试

```bash
pytest tests/
```

---

## 🎯 路线图

### 已完成 ✅
- [x] AI 推理模式支持
- [x] AI 函数工具系统（read_file, list_directory）
- [x] 多轮对话和上下文理解
- [x] 自适应审查策略
- [x] 结构化审查报告
- [x] 优雅的错误处理和降级机制

### 计划中 🚧
- [ ] 行内评论（针对特定代码行）
- [ ] 审查评分系统（0-10 分）
- [ ] 审查历史记录和趋势分析
- [ ] 支持更多 AI 模型（Gemini、Claude）
- [ ] 审查结果导出（PDF/Markdown）
- [ ] Web UI 管理界面

### 未来构想 💡
- [ ] 代码相似度检测
- [ ] 安全漏洞扫描
- [ ] 性能分析建议
- [ ] 多语言支持
- [ ] 自定义审查规则

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 贡献指南

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 📄 许可证

MIT License

---

## 🙏 致谢

- [DeepSeek](https://www.deepseek.com/) - 提供极具性价比的强大的 AI 推理能力
- [FastAPI](https://fastapi.tiangolo.com/) - 现代化的 Python Web 框架
- [PyGithub](https://github.com/PyGithub/PyGithub) - GitHub API 封装

---

## 📮 联系方式

- 项目主页：[GitHub](https://github.com/Sakura520222/Sakura-AI-Reviewer)
- 问题反馈：[Issues](https://github.com/Sakura520222/Sakura-AI-Reviewer/issues)
- 邮箱：Sakura520222@outlook.com

---

## 🌟 Star History

如果这个项目对你有帮助，请给我们一个 Star ⭐

---

<div align="center">

**Sakura AI Reviewer** - 让代码审查更智能、更高效

Made with ❤️ by [Your Name]

</div>