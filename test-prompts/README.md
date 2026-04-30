# AutoForge 框架测试提示词集

本目录包含用于测试 AutoForge 框架对不同技术栈支持程度的提示词。

## 测试矩阵

| 技术栈 | 复杂度 | 测试文件 | 目标功能组 |
|--------|--------|----------|-----------|
| Vite React | Tier 1 (基础) | `vite-react-tier1.md` | F1-F4 |
| Vite React | Tier 2 (进阶) | `vite-react-tier2.md` | F1-F9 |
| Vite React | Tier 3 (复杂) | `vite-react-tier3.md` | F1-F17 |
| Next.js | Tier 1 (基础) | `nextjs-tier1.md` | F1-F4 |
| Next.js | Tier 2 (进阶) | `nextjs-tier2.md` | F1-F12 |
| Next.js | Tier 3 (复杂) | `nextjs-tier3.md` | F1-F17 |
| 边界情况 | - | `edge-cases.md` | 特殊场景 |

## 运行测试

```bash
# Vite React Tier 1
python run.py "Build a personal task manager with Vite + React + TypeScript. Features: add/delete tasks, mark complete, filter by status (all/active/completed), localStorage persistence, responsive design with Tailwind CSS."

# Vite React Tier 2
python run.py "Build a markdown note-taking app with Vite + React + TypeScript. Features: create/edit/delete notes with markdown syntax, live preview split-pane, tag system with color-coded labels, full-text search, export to PDF, dark/light theme toggle, keyboard shortcuts (Ctrl+S save, Ctrl+N new note), drag-and-drop reorder."

# Next.js Tier 1
python run.py "Build a blog platform with Next.js 14 App Router. Features: homepage with post list, individual post pages with markdown rendering, about page, contact form with validation, static generation for posts, responsive layout with Tailwind CSS, SEO metadata per page."

# Next.js Tier 2
python run.py "Build an e-commerce product catalog with Next.js 14 App Router. Features: product grid with filtering, product detail page with image gallery, shopping cart, checkout flow, order confirmation, user authentication, wishlist, search with autocomplete, pagination, admin dashboard, dark mode support, ISR for product pages."
```

## 评估维度

每个测试运行后，检查以下维度：

### 1. 技术栈选择准确性
- [ ] Architect 是否正确识别技术栈复杂度
- [ ] 选择的模板是否匹配需求

### 2. 项目初始化
- [ ] Builder 是否正确执行 project_init
- [ ] 依赖安装是否完整
- [ ] 配置文件是否正确（tsconfig, vite.config, tailwind.config 等）

### 3. 构建成功率
- [ ] `npm run build` / `next build` 是否通过
- [ ] TypeScript 类型检查是否通过
- [ ] 无关键构建错误

### 4. 功能完整性
- [ ] 当前 sprint 的功能组是否实现
- [ ] 是否存在存根函数/TODO
- [ ] 条件渲染是否使用 CSS 控制（Reviewer 兼容性）

### 5. Reviewer 测试覆盖率
- [ ] 浏览器测试是否覆盖当前功能组
- [ ] 测试是否通过（非 false positive）

### 6. Judge 评分合理性
- [ ] PASS/FAIL 判断是否准确
- [ ] 评分是否与 Reviewer 报告一致

### 7. 迭代效率
- [ ] 达到通过所需的轮数
- [ ] 是否存在无限循环（env-fix budget 是否生效）

### 8. 上下文管理
- [ ] 长对话是否触发 compaction
- [ ] checkpoint/restore 是否正常工作

## 记录结果

建议将每次测试的结果记录到 `test-results/` 目录：

```
test-results/
├── 2026-04-29-vite-react-tier1/
│   ├── result.md          # 测试结论
│   ├── score-history.json # 每轮评分
│   └── issues.md          # 发现的问题
```
