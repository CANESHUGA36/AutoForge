# 边界情况测试提示词

## 1. 纯HTML项目（验证技术栈选择）

**提示词**: Build an audio spectrum visualizer. Real-time frequency analysis with Web Audio API, multiple visualization modes (bars, waveform, circular), color themes, playback controls. Single HTML file, no build step.

**预期**: 选择 pure-html 模板，验证 Builder 是否正确识别并禁用 npm/构建流程

---

## 2. 中文内容项目（验证编码处理）

**提示词**: Build a Chinese poetry reading app with Vite + React + TypeScript. Features: display Tang dynasty poems with traditional/simplified toggle, pinyin annotation toggle, author biography, favorite collection, search by title/author/content, responsive card layout.

**预期**: 验证 UTF-8 编码处理、中文字体加载、中文搜索功能

---

## 3. 图片生成依赖项目

**提示词**: Build a fantasy character card generator with Next.js. Features: character creation form (name, class, stats, backstory), AI-generated portrait using image generation API, printable card layout with QR code linking to character page, gallery of generated characters, share via URL.

**预期**: 验证 generate_image 工具调用、图片存储、next/image 优化

---

## 4. 外部API集成项目

**提示词**: Build a weather dashboard with Vite + React + TypeScript. Features: current weather display, 7-day forecast with charts, multiple city search and comparison, weather alerts, unit conversion (C/F, km/h-mph), background changes based on weather condition, geolocation auto-detect.

**预期**: 验证 search_web / API 调用、错误处理（API限流/失败）、缓存策略

---

## 5. 极简项目（验证不过度设计）

**提示词**: Build a simple countdown timer. Set minutes/seconds, start/pause/reset, alarm sound when done. Single page.

**预期**: 验证 Architect 选择 pure-html，不引入不必要的构建工具
