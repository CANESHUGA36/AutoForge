# AutoForge 全栈化改进路线图

> 版本: v1.0  
> 日期: 2026-04-30  
> 状态: 规划阶段（未开始实施）

---

## 目录

1. [愿景与目标](#1-愿景与目标)
2. [当前状态评估](#2-当前状态评估)
3. [改进维度总览](#3-改进维度总览)
4. [Phase 1: 最小可行全栈 (MVP)](#phase-1-最小可行全栈-mvp)
5. [Phase 2: 数据库服务化](#phase-2-数据库服务化)
6. [Phase 3: 前后端分离架构](#phase-3-前后端分离架构)
7. [Phase 4: 安全与生产就绪](#phase-4-安全与生产就绪)
8. [Phase 5: 部署与 DevOps](#phase-5-部署与-devops)
9. [风险与对策](#9-风险与对策)
10. [附录: 技术选型决策记录](#10-附录-技术选型决策记录)

---

## 1. 愿景与目标

### 1.1 愿景

将 AutoForge 从**纯前端应用生成器**演进为**全栈应用开发平台**，能够从零开始构建、验证和部署包含以下完整技术栈的 Web 应用：

```
┌─────────────────────────────────────────────────────────────┐
│                      用户输入需求                             │
│         "Build a SaaS dashboard with user auth"              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  AutoForge 输出                                              │
│  ├── 前端: React/Vue + TypeScript + Tailwind                 │
│  ├── 后端: Express/NestJS/Next.js API Routes                 │
│  ├── 数据库: PostgreSQL/SQLite + Prisma/Drizzle              │
│  ├── 认证: JWT/NextAuth/OAuth                                │
│  └── 部署: Docker + Vercel/Railway (可选)                    │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 目标

| 维度 | 当前状态 | 目标状态 | 衡量标准 |
|------|----------|----------|----------|
| **技术栈覆盖** | 纯前端 (React/Vite/Next.js) | 全栈 (前端+后端+数据库) | 能构建 CRUD + Auth + DB 的完整应用 |
| **Agent 认知** | 仅理解前端代码 | 理解前后端分离架构 | Architect 能设计 API + DB Schema |
| **验证能力** | DOM/浏览器测试 | 浏览器 + API + DB 三重验证 | Reviewer 能验证 HTTP 响应和数据库状态 |
| **数据持久化** | 静态 JSON mock | 真实数据库读写 | mock_api → db_query 演进 |
| **部署能力** | 无 | 容器化 + 云部署 | 生成 Dockerfile + docker-compose |

---

## 2. 当前状态评估

### 2.1 架构优势（可复用基础）

| 组件 | 当前实现 | 可扩展性 |
|------|----------|----------|
| Pipeline 阶段系统 | `harness/pipeline.py` + `harness/stages.py` | ⭐⭐⭐ 极易扩展新阶段 |
| 工具注册与分发 | `tools_impl.py` TOOL_SCHEMAS + TOOL_DISPATCH | ⭐⭐⭐ 新增工具只需注册 schema + 函数 |
| Framework Adapter | `harness/framework_adapter.py` | ⭐⭐⭐ 抽象基类，易增新适配器 |
| Feature Group 状态机 | `harness/feature_groups.py` | ⭐⭐⭐ 前后端功能组可统一调度 |
| 共享状态系统 | `harness/shared_state.py` | ⭐⭐⭐ 可扩展后端架构决策 |
| Git 检查点 | `harness/git.py` | ⭐⭐⭐ 全栈代码同样适用 |
| 上下文生命周期 | `context.py` | ⭐⭐☆ 需优化以应对更大代码量 |

### 2.2 关键缺失

| 缺失领域 | 具体表现 | 阻塞程度 |
|----------|----------|----------|
| 后端框架适配 | 无 Express/NestJS/Fastify/Django 适配器 | 🔴 严重 |
| 数据库集成 | docker-compose 无 DB 服务，无 ORM 工具 | 🔴 严重 |
| API 测试工具 | 无 HTTP 请求工具，无法验证 API 响应 | 🔴 严重 |
| Agent 后端认知 | Prompt 全为前端视角，无后端代码规范 | 🔴 严重 |
| 多服务编排 | 只能启动一个 dev server | 🟡 中等 |
| 环境变量管理 | 无 .env 生成/管理工具 | 🟡 中等 |
| 认证系统 | 无 JWT/Session/OAuth 支持 | 🟡 中等 |
| 部署流水线 | 无 Docker 构建/云部署能力 | 🟢 较低 |

---

## 3. 改进维度总览

全栈化改进涉及 **5 个维度、10 个文件、约 40 项具体任务**：

```
维度 1: Agent Prompt 认知升级
├── prompts/architect.md    → 后端技术栈选型、API 设计、DB Schema 设计
├── prompts/builder.md      → 后端代码规范、文件组织、数据库操作
├── prompts/reviewer.md     → API 测试流程、数据库验证、安全检查
└── prompts/sprint_master.md → 全栈功能拆分策略

维度 2: 工具链扩展
├── tools_impl.py           → +api_test, +db_migrate, +db_query, +db_seed, +set_env_var, +start_backend_server
└── 新增: tests/test_fullstack_tools.py

维度 3: Pipeline 阶段扩展
├── harness/stages.py       → +EnvSetupGate, +DbInitGate, +BackendBuildGate, +BackendDevServerGate, +ApiTestGate
└── harness/pipeline.py     → 多服务依赖管理

维度 4: Framework Adapter 扩展
├── harness/framework_adapter.py → +ExpressAdapter, +NestJSAdapter, +NextJSFullstackAdapter, +FastifyAdapter
└── harness/build.py        → 多端口检测与管理

维度 5: 基础设施与部署
├── docker-compose.yml      → +postgres, +redis, +backend 服务
├── Dockerfile              → 多阶段构建（前端 + 后端）
└── skills/                 → +database-design, +api-design, +backend-patterns, +auth-security, +docker-deployment
```

---

## Phase 1: 最小可行全栈 (MVP)

**目标**: 让 AutoForge 能构建 Next.js + Prisma + SQLite 的完整全栈应用  
**时间估算**: 2-3 周  
**成功标准**: 能构建带用户注册/登录的 Todo 应用

### 1.1 任务清单

#### 1.1.1 更新 Architect Prompt (`prompts/architect.md`)

**改动范围**: 新增"全栈技术栈选型"章节

**新增内容**:
```markdown
## 全栈技术栈选择

当用户需求涉及以下场景时，选择全栈方案：
- 用户认证（登录/注册/权限）
- 数据持久化存储
- 服务端数据处理
- 多用户协作

### 方案 A: Next.js Fullstack（推荐，最简单）
- 前端: Next.js App Router + React + TypeScript + Tailwind
- 后端: Next.js API Routes / Server Actions
- 数据库: SQLite (开发) / PostgreSQL (生产)
- ORM: Prisma
- 认证: NextAuth.js
- 适用: 中小型 SaaS、内容管理、电商平台

### 方案 B: Vite + Express 分离
- 前端: Vite + React + TypeScript + Tailwind
- 后端: Express.js + TypeScript
- 数据库: PostgreSQL + Prisma
- 认证: JWT + bcrypt
- 适用: 大型应用、需要独立 API 服务、微服务架构

### 输出物扩展
除 spec.md 和 contract.md 外，全栈项目还需生成：
- `api_spec.md` — API 端点定义（路径、方法、请求体、响应体）
- `db_schema.md` — 数据库表结构、关系、索引
```

**验收标准**:
- [ ] Architect 能为 "Build a blog with user auth" 生成包含 API 和 DB 设计的完整 spec
- [ ] contract.md 中包含 API 相关的验收标准（如 "POST /api/auth/login 返回 200 和 JWT"）

---

#### 1.1.2 更新 Builder Prompt (`prompts/builder.md`)

**改动范围**: 新增"后端代码规范"和"全栈文件组织"章节

**新增内容**:
```markdown
## 全栈项目文件组织

Next.js Fullstack 项目结构:
```
app/
├── page.tsx              # 前端页面
├── layout.tsx            # 根布局
├── api/
│   ├── auth/
│   │   └── [...nextauth]/route.ts   # 认证 API
│   ├── posts/
│   │   └── route.ts      # REST API: GET/POST /api/posts
│   └── users/
│       └── route.ts      # REST API: GET/POST /api/users
├── posts/
│   └── page.tsx          # 前端: 文章列表页
└── dashboard/
    └── page.tsx          # 前端: 管理后台
prisma/
├── schema.prisma         # 数据库模型定义
└── seed.ts               # 种子数据
lib/
├── prisma.ts             # Prisma Client 单例
└── auth.ts               # 认证配置
```

## 后端编码规范

1. **API 路由**: 使用 Next.js App Router 的 Route Handlers
   - GET /api/posts → 获取列表
   - POST /api/posts → 创建（需认证）
   - GET /api/posts/[id] → 获取单个
   - PUT /api/posts/[id] → 更新
   - DELETE /api/posts/[id] → 删除

2. **数据库操作**: 通过 Prisma Client
   ```typescript
   import { prisma } from "@/lib/prisma";
   const posts = await prisma.post.findMany({ include: { author: true } });
   ```

3. **错误处理**: 统一返回 JSON 格式
   ```typescript
   return Response.json({ error: "Not found" }, { status: 404 });
   ```

4. **认证检查**: 在需要保护的 API 中检查 session
   ```typescript
   const session = await getServerSession(authOptions);
   if (!session) return Response.json({ error: "Unauthorized" }, { status: 401 });
   ```
```

**验收标准**:
- [ ] Builder 能正确创建 `app/api/*/route.ts` 文件
- [ ] Builder 能正确创建 `prisma/schema.prisma`
- [ ] Builder 能在 API 中正确使用 Prisma Client

---

#### 1.1.3 新增 `api_test` 工具 (`tools_impl.py`)

**功能定义**:
```python
def api_test(
    endpoint: str,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    expected_status: int = 200,
    base_url: str = "http://localhost:3000",
) -> str:
    """测试 HTTP API 端点，验证响应状态码和数据结构。
    
    Args:
        endpoint: API 路径，如 "/api/posts"
        method: HTTP 方法，默认 GET
        body: 请求体（JSON 对象）
        headers: 自定义请求头
        expected_status: 期望的响应状态码
        base_url: 基础 URL，默认 http://localhost:3000
    
    Returns:
        JSON 格式的测试结果，包含 status, response_body, headers, passed
    """
```

**Schema 定义**:
```json
{
  "type": "function",
  "function": {
    "name": "api_test",
    "description": "Test an HTTP API endpoint. Validates response status code and returns response body for inspection. Use to verify backend APIs are working correctly.",
    "parameters": {
      "type": "object",
      "properties": {
        "endpoint": { "type": "string", "description": "API path, e.g., /api/posts" },
        "method": { "type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "default": "GET" },
        "body": { "type": "object", "description": "Request body as JSON object" },
        "headers": { "type": "object", "description": "Custom headers" },
        "expected_status": { "type": "integer", "default": 200 },
        "base_url": { "type": "string", "default": "http://localhost:3000" }
      },
      "required": ["endpoint"]
    }
  }
}
```

**验收标准**:
- [ ] `api_test("/api/health")` 返回 JSON 格式的响应数据
- [ ] `api_test("/api/posts", method="POST", body={"title":"Test"})` 正确发送 POST 请求
- [ ] 非 2xx 响应返回包含错误信息的结构化结果

---

#### 1.1.4 新增 `db_query` 工具 (`tools_impl.py`)

**功能定义**:
```python
def db_query(
    sql: str,
    readonly: bool = True,
    db_url: str | None = None,
) -> str:
    """执行 SQL 查询以验证数据库状态。
    
    默认只读模式（SELECT 语句）。非只读模式需要显式设置 readonly=False。
    
    Args:
        sql: SQL 查询语句
        readonly: 是否只读，默认 True
        db_url: 数据库连接字符串，默认从 DATABASE_URL 环境变量读取
    
    Returns:
        JSON 格式的查询结果
    """
```

**安全约束**:
- 只读模式下只允许 `SELECT` 语句
- 拒绝包含 `DROP`, `DELETE`, `UPDATE`, `INSERT` 的写操作（除非 readonly=False）
- 拒绝多语句执行（防止 SQL 注入）

**验收标准**:
- [ ] `db_query("SELECT * FROM Post")` 返回表数据
- [ ] `db_query("DROP TABLE Post")` 在只读模式下返回错误
- [ ] 无法连接数据库时返回友好的错误信息

---

#### 1.1.5 扩展 `project_init` 支持全栈模板 (`tools_impl.py`)

**新增模板**: `nextjs-prisma`

**模板内容**:
```
nextjs-prisma/
├── app/
│   ├── page.tsx
│   ├── layout.tsx
│   └── api/
│       └── health/
│           └── route.ts
├── prisma/
│   └── schema.prisma
├── lib/
│   └── prisma.ts
├── package.json      (含 prisma, @prisma/client, next-auth)
├── .env.example
└── next.config.js
```

**prisma/schema.prisma 模板**:
```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "sqlite"
  url      = env("DATABASE_URL")
}

model User {
  id        String   @id @default(cuid())
  email     String   @unique
  name      String?
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```

**验收标准**:
- [ ] `project_init("nextjs-prisma")` 创建完整项目骨架
- [ ] `npm install` 成功安装所有依赖
- [ ] `npx prisma generate` 成功生成 Prisma Client

---

#### 1.1.6 更新 Reviewer Prompt (`prompts/reviewer.md`)

**新增内容**:
```markdown
## 全栈验证流程

### Step 4: API 验证（全栈项目必做）

对每个 API 端点执行测试：

1. **健康检查**: `api_test("/api/health")` → 应返回 200
2. **列表接口**: `api_test("/api/posts")` → 应返回数组
3. **创建接口**: `api_test("/api/posts", method="POST", body={...})` → 应返回 201
4. **数据库验证**: `db_query("SELECT * FROM Post")` → 确认数据已写入

### Step 5: 安全检查

- API 是否返回适当的错误状态码（404, 401, 403）
- 敏感操作是否要求认证
- 是否暴露敏感信息（密码、密钥）
```

**验收标准**:
- [ ] Reviewer 能使用 `api_test` 验证 API 响应
- [ ] Reviewer 能使用 `db_query` 验证数据库状态
- [ ] contract.md 中的 API 标准被正确映射到测试结果

---

#### 1.1.7 更新 `reviewer_tools` 权限 (`harness/core.py`)

```python
reviewer_tools = CORE_TOOLS | FILE_TOOLS | BROWSER_TOOLS | {
    "contract_test_run", "react_devtools_inspect", "check_console_logs",
    "detect_framework", "run_diagnostics",
    "check_responsive", "check_a11y", "check_performance", "check_routes", "mock_api",
    "api_test", "db_query",  # ← 新增
}
```

---

### 1.2 Phase 1 成功标准

运行以下命令应成功构建完整应用：

```bash
python run.py "Build a fullstack todo app with user authentication. Users can register, login, create todos, mark them complete, and delete them."
```

**期望输出**:
- [ ] 生成包含前端页面、API 路由、数据库模型的完整代码
- [ ] `validate_build()` 通过（npm run build 成功）
- [ ] `api_test("/api/todos")` 返回正确的 JSON 数据
- [ ] `db_query("SELECT * FROM Todo")` 确认数据持久化
- [ ] browser_check 验证前端能正确调用 API
- [ ] 所有功能组通过 Judge 评分

---

## Phase 2: 数据库服务化

**目标**: 将 SQLite 升级为 PostgreSQL，添加迁移和种子数据管理  
**时间估算**: 2 周  
**前置条件**: Phase 1 完成

### 2.1 任务清单

#### 2.1.1 更新 docker-compose.yml

```yaml
services:
  autoforge:
    image: autoforge:v8
    volumes:
      - .:/workspace
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      - DATABASE_URL=postgresql://app:app@postgres:5432/app
      - NODE_ENV=development

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: app
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

#### 2.1.2 新增 `db_migrate` 工具

```python
def db_migrate(command: str = "deploy") -> str:
    """运行数据库迁移。
    
    自动检测项目使用的迁移工具：
    - Prisma: npx prisma migrate dev/deploy
    - Drizzle: npx drizzle-kit migrate
    - Alembic: alembic upgrade head
    
    Args:
        command: 迁移命令，prisma 支持 "dev"(开发) / "deploy"(生产)
    
    Returns:
        迁移执行结果
    """
```

#### 2.1.3 新增 `db_seed` 工具

```python
def db_seed(seed_file: str | None = None) -> str:
    """导入种子数据到数据库。
    
    自动检测并执行：
    - Prisma: npx prisma db seed
    - 自定义 SQL: 执行 seed.sql
    
    Args:
        seed_file: 可选的自定义种子文件路径
    
    Returns:
        种子数据导入结果
    """
```

#### 2.1.4 新增 `DbInitGate` Pipeline 阶段

```python
class DbInitGateStage(PipelineStage):
    """数据库初始化阶段。
    
    执行流程:
    1. 检查 prisma/schema.prisma 是否存在
    2. 运行 npx prisma generate（生成 Client）
    3. 运行 npx prisma migrate dev（创建表结构）
    4. 运行 npx prisma db seed（导入种子数据）
    """
    name = "db_init_gate"
    
    def run(self, ctx: StageContext) -> StageResult:
        # 检测迁移工具
        if (ctx.workspace / "prisma" / "schema.prisma").exists():
            return self._run_prisma_migrate(ctx)
        elif (ctx.workspace / "drizzle.config.ts").exists():
            return self._run_drizzle_migrate(ctx)
        return StageResult.skipped("No database schema found")
```

#### 2.1.5 创建 `database-design` Skill

```markdown
---
description: Database schema design guide. Principles for normalization, indexing, relationships, and Prisma/Drizzle schema definition.
---

# Database Design Guide

## Schema Design原则

1. **命名规范**: 表名 PascalCase (Prisma) 或 snake_case (SQL)
2. **主键**: 使用 `cuid()` 或 `uuid()`，避免自增 ID
3. **时间戳**: 每个表必须包含 `createdAt` 和 `updatedAt`
4. **软删除**: 使用 `deletedAt` 字段而非物理删除

## Prisma Schema 示例

```prisma
model Post {
  id        String   @id @default(cuid())
  title     String
  content   String?
  published Boolean  @default(false)
  authorId  String
  author    User     @relation(fields: [authorId], references: [id])
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```

## 索引策略

- 外键字段自动创建索引
- 查询频繁的字段手动添加 `@index`
- 全文搜索使用 `@index([title, content])`
```

---

### 2.2 Phase 2 成功标准

- [ ] docker-compose up 自动启动 PostgreSQL
- [ ] Builder 创建的 Prisma schema 能正确迁移到 PostgreSQL
- [ ] 应用重启后数据不丢失
- [ ] `db_migrate()` 和 `db_seed()` 工具正常工作

---

## Phase 3: 前后端分离架构

**目标**: 支持 Vite React + Express/NestJS 的独立前后端架构  
**时间估算**: 2-3 周  
**前置条件**: Phase 2 完成

### 3.1 任务清单

#### 3.1.1 新增后端 Framework Adapter

```python
class ExpressAdapter(FrameworkAdapter):
    """Express.js 后端适配器"""
    name = "express"
    
    def get_dev_server_command(self) -> str:
        return "npm run dev:server"  # nodemon src/index.ts
    
    def get_build_command(self) -> str:
        return "npm run build:server"  # tsc
    
    def get_dev_server_port(self) -> int:
        return 3001  # 与前端 5173 区分
    
    def get_health_check_url(self) -> str:
        return "http://localhost:3001/api/health"
    
    def get_package_install_command(self) -> str:
        return "npm install express cors helmet morgan dotenv bcryptjs jsonwebtoken"


class NestJSAdapter(FrameworkAdapter):
    """NestJS 后端适配器"""
    name = "nestjs"
    
    def get_dev_server_command(self) -> str:
        return "npm run start:dev"
    
    def get_build_command(self) -> str:
        return "npm run build"
    
    def get_dev_server_port(self) -> int:
        return 3001


class FastifyAdapter(FrameworkAdapter):
    """Fastify 后端适配器"""
    name = "fastify"
    
    def get_dev_server_command(self) -> str:
        return "npm run dev"
    
    def get_dev_server_port(self) -> int:
        return 3001
```

#### 3.1.2 新增 `start_backend_server` 工具

```python
def start_backend_server(
    command: str = "npm run dev:server",
    port: int = 3001,
    wait: int = 5,
    cwd: str | None = None,
) -> str:
    """启动后端开发服务器。
    
    与前端 dev server 独立运行，支持同时启动多个服务。
    
    Args:
        command: 启动命令
        port: 服务端口
        wait: 启动后等待时间（秒）
        cwd: 工作目录（前后端分离时指向 backend/）
    """
```

#### 3.1.3 支持多服务并发

**改动文件**: `harness/core.py`, `harness/build.py`

```python
# harness/core.py
class Harness:
    def __init__(self, workspace: str):
        # ... 现有初始化 ...
        self.frontend_port: int = 5173
        self.backend_port: int = 3001
        self.active_servers: dict[str, subprocess.Popen] = {}
    
    def _start_all_services(self):
        """启动前端 + 后端 + 数据库所有服务"""
        # 1. 数据库已在 docker-compose 中启动
        # 2. 启动后端服务
        if self._has_backend():
            self._start_backend_server()
        # 3. 启动前端服务
        self._start_frontend_server()
```

#### 3.1.4 新增 `BackendDevServerGate` 阶段

```python
class BackendDevServerGateStage(PipelineStage):
    """后端服务健康检查阶段。"""
    name = "backend_dev_server_gate"
    
    def run(self, ctx: StageContext) -> StageResult:
        backend_port = ctx.config.get("backend_port", 3001)
        health_url = f"http://localhost:{backend_port}/api/health"
        
        if not self._is_server_healthy(health_url):
            # 尝试启动后端服务
            start_backend_server(port=backend_port)
            time.sleep(5)
            
            if not self._is_server_healthy(health_url):
                return StageResult.failed("Backend server failed to start")
        
        return StageResult.passed()
```

#### 3.1.5 创建 `backend-patterns` Skill

```markdown
---
description: Backend development patterns for Express.js and NestJS. Covers MVC, repository pattern, error handling, and middleware.
---

# Backend Patterns Guide

## Express.js 项目结构

```
backend/
├── src/
│   ├── index.ts          # 入口文件
│   ├── routes/           # 路由定义
│   │   ├── posts.ts
│   │   └── users.ts
│   ├── controllers/      # 请求处理
│   │   ├── postController.ts
│   │   └── userController.ts
│   ├── services/         # 业务逻辑
│   │   ├── postService.ts
│   │   └── userService.ts
│   ├── models/           # 数据模型
│   │   └── prisma.ts
│   ├── middleware/       # 中间件
│   │   ├── auth.ts
│   │   └── errorHandler.ts
│   └── types/            # TypeScript 类型
│       └── index.ts
├── package.json
└── tsconfig.json
```

## 错误处理中间件

```typescript
app.use((err: Error, req: Request, res: Response, next: NextFunction) => {
  console.error(err.stack);
  res.status(500).json({ error: "Internal server error" });
});
```

## 认证中间件

```typescript
export const authMiddleware = (req: Request, res: Response, next: NextFunction) => {
  const token = req.headers.authorization?.split(" ")[1];
  if (!token) return res.status(401).json({ error: "Unauthorized" });
  // 验证 JWT...
  next();
};
```
```

#### 3.1.6 创建 `api-design` Skill

```markdown
---
description: RESTful API design guide. Covers HTTP methods, status codes, error handling, pagination, and OpenAPI specification.
---

# API Design Guide

## RESTful 规范

| 操作 | HTTP 方法 | 路径 | 成功状态码 |
|------|-----------|------|-----------|
| 获取列表 | GET | /api/posts | 200 |
| 获取单个 | GET | /api/posts/:id | 200 |
| 创建 | POST | /api/posts | 201 |
| 更新 | PUT/PATCH | /api/posts/:id | 200 |
| 删除 | DELETE | /api/posts/:id | 204 |

## 错误响应格式

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input",
    "details": [
      { "field": "email", "message": "Must be a valid email" }
    ]
  }
}
```

## 分页格式

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "pageSize": 20,
    "total": 100,
    "totalPages": 5
  }
}
```
```

---

### 3.2 Phase 3 成功标准

```bash
python run.py "Build a fullstack blog with Vite React frontend and Express backend. Users can register, write posts, and comment."
```

- [ ] 前端在 localhost:5173 运行
- [ ] 后端在 localhost:3001 运行
- [ ] 前端能正确调用后端 API
- [ ] 数据库使用 PostgreSQL，数据持久化
- [ ] 所有服务同时运行不冲突

---

## Phase 4: 安全与生产就绪

**目标**: 添加认证系统和安全审查能力  
**时间估算**: 2 周  
**前置条件**: Phase 3 完成

### 4.1 任务清单

#### 4.1.1 新增认证相关工具

```python
def generate_jwt_secret() -> str:
    """生成随机的 JWT_SECRET 并写入 .env"""
    import secrets
    secret = secrets.token_urlsafe(32)
    set_env_var("JWT_SECRET", secret)
    set_env_var("NEXTAUTH_SECRET", secret)
    return f"Generated JWT_SECRET: {secret[:8]}..."


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码（供 Builder 参考）"""
    # 返回示例哈希，不实际执行（Builder 应在代码中使用 bcrypt）
    return "[info] Use bcrypt.hashSync(password, 10) in your code"
```

#### 4.1.2 更新 Reviewer 安全检查清单

```markdown
## 安全审查清单

### 认证与授权
- [ ] 敏感 API 是否要求认证（检查 Authorization header）
- [ ] 密码是否使用 bcrypt 哈希存储（不是明文）
- [ ] JWT 是否设置合理的过期时间
- [ ] 是否实现 CSRF 保护

### 输入验证
- [ ] API 是否验证请求体（zod / joi / class-validator）
- [ ] SQL 查询是否使用参数化（防止 SQL 注入）
- [ ] 用户输入是否进行 XSS 过滤

### 敏感信息
- [ ] .env 文件是否包含在 .gitignore 中
- [ ] API 响应是否不包含密码哈希或密钥
- [ ] 错误信息是否不暴露内部实现细节
```

#### 4.1.3 创建 `auth-security` Skill

```markdown
---
description: Authentication and security guide. Covers JWT, session management, password hashing, CORS, CSRF, and common vulnerability prevention.
---

# Auth & Security Guide

## NextAuth.js 配置（Next.js）

```typescript
// lib/auth.ts
import NextAuth from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

export const authOptions = {
  providers: [
    CredentialsProvider({
      name: "credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" }
      },
      async authorize(credentials) {
        // 验证用户...
        return user;
      }
    })
  ],
  session: { strategy: "jwt", maxAge: 30 * 24 * 60 * 60 }, // 30天
};
```

## JWT 中间件（Express）

```typescript
import jwt from "jsonwebtoken";

export const authMiddleware = (req, res, next) => {
  const token = req.headers.authorization?.split(" ")[1];
  if (!token) return res.status(401).json({ error: "Unauthorized" });
  
  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET!);
    req.user = decoded;
    next();
  } catch {
    res.status(403).json({ error: "Invalid token" });
  }
};
```

## 密码哈希

```typescript
import bcrypt from "bcryptjs";

const hashedPassword = await bcrypt.hash(password, 10);
const isValid = await bcrypt.compare(password, hashedPassword);
```

## CORS 配置

```typescript
import cors from "cors";
app.use(cors({
  origin: process.env.FRONTEND_URL || "http://localhost:5173",
  credentials: true
}));
```
```

---

### 4.2 Phase 4 成功标准

- [ ] 能构建带完整认证流程的应用（注册/登录/登出/受保护路由）
- [ ] Reviewer 能检测常见的安全问题
- [ ] 密码不以明文存储
- [ ] JWT 正确配置和使用

---

## Phase 5: 部署与 DevOps

**目标**: 生成生产部署配置  
**时间估算**: 2 周（可选）  
**前置条件**: Phase 4 完成

### 5.1 任务清单

#### 5.1.1 新增部署工具

```python
def generate_dockerfile(
    frontend_framework: str = "vite",
    backend_framework: str | None = None,
) -> str:
    """生成多阶段 Dockerfile。
    
    Args:
        frontend_framework: 前端框架 (vite/nextjs)
        backend_framework: 后端框架 (express/nestjs/nextjs)，None 表示纯前端
    
    Returns:
        生成的 Dockerfile 路径
    """


def generate_docker_compose_prod() -> str:
    """生成生产环境 docker-compose.yml（含反向代理、SSL）"""


def deploy_to_vercel() -> str:
    """生成 Vercel 部署配置（vercel.json）"""
```

#### 5.1.2 新增 Pipeline 部署阶段

```python
class DockerBuildStage(PipelineStage):
    """构建 Docker 镜像阶段"""
    name = "docker_build"
    
    def run(self, ctx: StageContext) -> StageResult:
        result = run_bash("docker build -t myapp .")
        if "error" in result.lower():
            return StageResult.failed(result)
        return StageResult.passed()


class ProductionBuildStage(PipelineStage):
    """生产构建验证阶段"""
    name = "production_build"
    
    def run(self, ctx: StageContext) -> StageResult:
        # 验证前端生产构建
        frontend_build = run_bash("cd frontend && npm run build")
        # 验证后端生产构建
        backend_build = run_bash("cd backend && npm run build")
        return StageResult.passed()
```

#### 5.1.3 创建 `docker-deployment` Skill

```markdown
---
description: Docker deployment guide. Multi-stage builds, health checks, nginx reverse proxy, and production best practices.
---

# Docker Deployment Guide

## 多阶段 Dockerfile（Vite + Express）

```dockerfile
# 前端构建阶段
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 后端构建阶段
FROM node:20-alpine AS backend-build
WORKDIR /app/backend
COPY backend/package*.json ./
RUN npm ci
COPY backend/ ./
RUN npm run build

# 生产运行阶段
FROM node:20-alpine
WORKDIR /app
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
COPY --from=backend-build /app/backend/dist ./backend/dist
COPY --from=backend-build /app/backend/package*.json ./backend/
RUN cd backend && npm ci --production
EXPOSE 3001
CMD ["node", "backend/dist/index.js"]
```

## Nginx 反向代理

```nginx
server {
    listen 80;
    location / {
        root /var/www/frontend;
        try_files $uri $uri/ /index.html;
    }
    location /api {
        proxy_pass http://backend:3001;
        proxy_set_header Host $host;
    }
}
```
```

---

### 5.2 Phase 5 成功标准

- [ ] 生成可用的 Dockerfile
- [ ] `docker build` 成功构建镜像
- [ ] 生成生产环境 docker-compose.yml
- [ ] 可选: 生成 Vercel/Railway 部署配置

---

## 9. 风险与对策

| 风险 | 可能性 | 影响 | 对策 |
|------|--------|------|------|
| **Context 窗口爆炸** | 高 | 🔴 严重 | ① 前后端代码分阶段加载 ② 更激进的 context compaction ③ 考虑分离 Builder 为 FrontendBuilder + BackendBuilder |
| **数据库状态污染** | 高 | 🔴 严重 | ① 每轮测试后 `db_reset` ② 使用独立测试数据库 ③ 事务回滚模式 |
| **多服务端口冲突** | 中 | 🟡 中等 | ① 统一端口分配器（5173/3001/5432）② 动态端口检测 ③ 服务注册表 |
| **后端构建时间增加** | 中 | 🟡 中等 | ① 增量编译 ② 并行构建前后端 ③ 缓存 node_modules |
| **Agent 混淆前后端** | 中 | 🟡 中等 | ① 强制目录约定 ② 文件模板 ③ 在 prompt 中明确标注当前工作上下文 |
| **安全漏洞** | 中 | 🟡 中等 | ① Reviewer 安全检查清单 ② 静态安全扫描工具 ③ 禁止危险操作（eval, exec）|
| **LLM 后端代码质量** | 高 | 🟡 中等 | ① 详细的 backend-patterns skill ② 代码模板 ③ 更严格的 contract 标准 |

---

## 10. 附录: 技术选型决策记录

### ADR-001: 后端框架选择

**决策**: 优先支持 Express.js，其次 NestJS，再考虑 Fastify

**理由**:
- Express.js: 生态最成熟，LLM 训练数据最多，生成质量最可靠
- NestJS: 企业级架构，但概念较多（Module/Controller/Service），LLM 可能混淆
- Fastify: 性能最好，但生态相对较小

### ADR-002: ORM 选择

**决策**: 优先支持 Prisma，其次 Drizzle

**理由**:
- Prisma: 类型安全最好，schema 定义直观，迁移工具成熟
- Drizzle: 更轻量，但生态和文档不如 Prisma

### ADR-003: 数据库选择

**决策**: 开发用 SQLite，生产用 PostgreSQL

**理由**:
- SQLite: 零配置，适合快速原型和测试
- PostgreSQL: 功能最全，生产标准

### ADR-004: 认证方案选择

**决策**: Next.js 项目用 NextAuth.js，分离架构用 JWT + bcrypt

**理由**:
- NextAuth.js: 与 Next.js 深度集成，支持 OAuth 提供商
- JWT: 通用方案，前后端分离的标准选择

### ADR-005: 为什么不先支持 Python/Django 后端

**理由**:
- 当前技术栈已经是 Node.js 生态（npm, Vite, Next.js）
- 保持技术栈一致性降低复杂度
- LLM 对 TypeScript/JavaScript 的生成质量优于 Python
- 未来可考虑添加 Python 适配器

---

## 变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，包含 5 个 Phase 的完整规划 |

---

> **注意**: 本文档为规划文档，尚未开始实施。实施时应根据实际进展和反馈调整优先级。
