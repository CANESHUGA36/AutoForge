# Vite React - Tier 2: 进阶功能测试

## 提示词

Build a markdown note-taking app with Vite + React + TypeScript. Features: create/edit/delete notes with markdown syntax, live preview split-pane, tag system with color-coded labels, full-text search, export to PDF, dark/light theme toggle, keyboard shortcuts (Ctrl+S save, Ctrl+N new note), drag-and-drop reorder.

## 预期技术栈
- **模板**: vite-react-ts
- **复杂度**: 中高（10-15个功能模块）
- **关键测试点**:
  - 复杂状态管理（多实体关联：notes + tags）
  - 第三方库集成（markdown parser, PDF export）
  - 主题系统（CSS变量 + Context）
  - 键盘事件处理
  - 拖拽排序（HTML5 Drag API）
  - 分屏布局（resizable panes）

## 验收标准关注点
- F1-F4: 基础CRUD
- F5-F8: 搜索、标签、主题、导出
- D: 分屏交互设计
- T: 键盘快捷键、性能
