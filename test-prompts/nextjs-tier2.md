# Next.js - Tier 2: 进阶功能测试

## 提示词

Build an e-commerce product catalog with Next.js 14 App Router. Features: product grid with filtering (category, price range, rating), product detail page with image gallery and zoom, shopping cart with add/remove/quantity update, checkout flow (shipping + payment forms), order confirmation page, user authentication (mock JWT), wishlist functionality, search with autocomplete, pagination, admin dashboard for product management (CRUD), dark mode support, ISR for product pages.

## 预期技术栈
- **模板**: nextjs-app
- **复杂度**: 中高（10-15个功能模块）
- **关键测试点**:
  - 路由组与布局嵌套（(shop), (admin)）
  - 状态管理（Zustand/Context + localStorage）
  - 认证流程（middleware, protected routes）
  - 表单复杂验证（多步骤checkout）
  - ISR + 客户端数据获取混合
  - 搜索功能（debounce + API integration）
  - 图片优化（next/image）

## 验收标准关注点
- F1-F4: 产品浏览、详情、搜索
- F5-F9: 购物车、结账、认证、愿望单
- F10-F12: 管理后台、分页、主题
- D: 电商专业UI
- T: 性能优化、安全
