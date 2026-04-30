# Next.js - Tier 3: 复杂应用测试

## 提示词

Build a SaaS analytics dashboard with Next.js 14 App Router. Features: authentication (OAuth + email/password with NextAuth.js), role-based access control (admin/analyst/viewer), multi-tenant workspace switching, real-time data visualization with Recharts (line, bar, pie, funnel charts), custom date range picker with presets, data table with sorting/filtering/column visibility/export CSV, report builder with drag-and-drop widget arrangement, scheduled email reports (mock), notification center with read/unread, team member invitation and permission management, API key generation and management, billing page with plan comparison (mock Stripe), settings page with profile/team/billing/integrations tabs, full dark mode, PWA support with service worker, server-side PDF generation.

## 预期技术栈
- **模板**: nextjs-app
- **复杂度**: 很高（20+功能模块）
- **关键测试点**:
  - 复杂认证系统（NextAuth + RBAC）
  - 多租户数据隔离
  - 数据可视化（Recharts/D3）
  - 复杂表格（TanStack Table）
  - 拖拽布局（react-grid-layout）
  - 服务端PDF生成（Puppeteer/Playwright）
  - PWA（manifest, service worker）
  - 模拟支付流程

## 验收标准关注点
- F1-F9: 认证、仪表板、图表、表格
- F10-F17: 报告、通知、团队、API、计费、设置
- D: 企业级SaaS设计
- T: 安全、性能、可扩展性
