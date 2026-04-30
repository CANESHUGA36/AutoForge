# Vite React - Tier 3: 复杂应用测试

## 提示词

Build a real-time collaborative whiteboard with Vite + React + TypeScript. Features: infinite canvas with pan/zoom, draw shapes (rect, circle, line, freehand), text boxes with rich formatting, sticky notes with colors, image upload and positioning, multi-select with bounding box, undo/redo history (50 steps), layer management (send to front/back), export canvas to PNG/SVG, WebSocket simulation with presence cursors (mock 3 users), responsive toolbar.

## 预期技术栈
- **模板**: vite-react-ts
- **复杂度**: 高（15+功能模块）
- **关键测试点**:
  - Canvas API / SVG 高级绘图
  - 复杂交互状态机（工具切换、选择模式）
  - 历史管理（Command Pattern）
  - 模拟实时协作（WebSocket mock）
  - 性能优化（大量图形元素）
  - 导出功能（Canvas → PNG/SVG）

## 验收标准关注点
- F1-F9: 绘图、形状、文本、图片、选择、历史、图层
- F10-F14: 导出、协作、工具栏
- D: 专业级设计工具UI
- T: 性能、内存管理
