# Next.js - Tier 1: 基础功能测试

## 提示词

Build a blog platform with Next.js 14 App Router. Features: homepage with post list, individual post pages with markdown rendering, about page, contact form with validation, static generation for posts, responsive layout with Tailwind CSS, SEO metadata per page.

## 预期技术栈
- **模板**: nextjs-app
- **复杂度**: 中等（5-8个功能模块）
- **关键测试点**:
  - App Router 文件约定（layout.tsx, page.tsx, loading.tsx）
  - 静态生成（generateStaticParams）
  - 服务端/客户端组件分离
  - 表单处理（Server Actions 或 API Route）
  - SEO（metadata API）
  - 构建验证（next build）

## 验收标准关注点
- F1: 文章列表页
- F2: 文章详情页
- F3: 关于/联系页
- F4: 表单功能
- D: 排版设计
- T: SEO、构建优化
