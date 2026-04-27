---
description: Pre-submission checklist for Builder to self-verify before finishing a round. Catches 80% of common issues before Evaluator sees them.
---

# Component Testing Checklist

Run through this checklist BEFORE committing and finishing your round.

## ⚠️ Functional Group Scope Limit

**You are implementing ONE functional group per round.** Do NOT attempt to verify the entire project.

- Only check items **directly related to the current functional group**
- Skip items belonging to other groups (e.g., don't check "all images have alt" if you're implementing F3 waveform visualization)
- If the current group is a **Design (D)** or **Technical (T)** group, you MAY run the full checklist
- Otherwise, focus on: build pass + current group's features work + no console errors

The Harness Evaluator will only score criteria in the current group.

## Build Verification

```bash
# 1. Type check
npx tsc --noEmit

# 2. Production build
npm run build

# 3. Dev server runtime verification (CRITICAL)
# Start dev server in background
npm run dev &
sleep 15  # Wait for first compilation

# HTTP health check — MUST return 200
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5173)
echo "HTTP Status: $HTTP_CODE"

# Content check — MUST contain expected title
TITLE=$(curl -s http://localhost:5173 | grep -o "<title>.*</title>")
echo "Page title: $TITLE"

# Stop dev server
pkill -f "vite" || pkill -f "npm run dev"
```

**If HTTP code is not 200, or title is missing/contains "404"/"error":**
1. Check terminal output for build errors
2. Read `build-troubleshooting` skill
3. Fix the issue — DO NOT commit

**Common causes of 404/500 after successful build:**
- Vite dev server not ready → Increase sleep to 20s
- Port conflict → Check `lsof -i :5173`
- Corrupted cache → `rm -rf node_modules/.vite` and restart

**If build fails**: Read `build-troubleshooting` skill FIRST before asking for help.

## Visual Checklist

### Color System
- [ ] All colors use CSS variables or Tailwind config tokens
- [ ] No hardcoded hex values scattered in components
- [ ] Dark/light mode works (if applicable)

### Typography
- [ ] Font explicitly loaded (Google Fonts link or next/font)
- [ ] Heading font different from body font
- [ ] Size scale: heading > subheading > body > caption

### Spacing
- [ ] Uses consistent spacing scale (4/8/12/16/24/32/48/64)
- [ ] No arbitrary values like `margin: 7px`
- [ ] Touch targets >= 44x44px

### Components
- [ ] Hover states on ALL interactive elements
- [ ] Focus states visible (keyboard navigation)
- [ ] Active/pressed states on buttons
- [ ] Disabled states styled (if applicable)
- [ ] Loading states handled
- [ ] Empty states handled

## Functional Checklist

### Interactive Elements
- [ ] Buttons are clickable and do something
- [ ] Forms validate input
- [ ] Navigation links work
- [ ] Modal/dialog opens and closes

### State Management
- [ ] State updates trigger re-render
- [ ] No stale closures (functions reference current state)
- [ ] localStorage/IndexedDB persistence works (if applicable)

### Edge Cases
- [ ] Empty input handled
- [ ] Long text doesn't break layout
- [ ] Rapid clicking doesn't crash
- [ ] Mobile viewport works (375px)
- [ ] Desktop viewport works (1280px)

## Animation Checklist

- [ ] Animations respect `prefers-reduced-motion`
- [ ] No janky transitions (smooth 200-300ms)
- [ ] Hover effects feel responsive
- [ ] Scroll animations trigger correctly

## Accessibility Checklist

> ⚠️ Only check items relevant to the **current functional group**. Skip if you're not in a Design (D) or UI-focused group.

- [ ] All images **in this group** have `alt` text
- [ ] Interactive elements **in this group** have `aria-label` if no visible text
- [ ] Color contrast >= 4.5:1 (for elements you created/modified)
- [ ] Keyboard navigation works for **this group's** features
- [ ] Focus indicators visible on **this group's** elements

## Code Quality Checklist

- [ ] No `console.log` left in production code
- [ ] No `TODO` or `FIXME` comments
- [ ] No unused imports
- [ ] No unused variables
- [ ] Types defined (TypeScript)
- [ ] Props typed correctly

## Asset Checklist

- [ ] All images referenced in code exist on disk
- [ ] No broken image links
- [ ] Icons load correctly
- [ ] Fonts load correctly

## Final Steps

1. Run `npm run build` one more time
2. Start dev server and verify with curl (see Build Verification section above)
3. Verify HTTP 200 and correct page title/content
4. Test responsive breakpoints **for the current group's UI**
5. Check browser console for errors
6. Commit with `git add -A && git commit -m "round N: [group-id] description"`

---

## 条件渲染功能测试指南（Reviewer 专用）

> 本指南供 **Reviewer** 在测试条件渲染功能时使用。很多功能（如 F4 Playback Controls）只在特定 state 下才显示，初始 DOM 中看不到是正常的。

### 何时使用本指南

当你发现功能组要求的 DOM 元素在页面加载后不存在时：
1. **先检查源码** — 确认元素是否被条件渲染（如 `{state && <Element />}` 或 `className={state ? 'visible' : 'hidden'}`）
2. **如果是条件渲染** — 使用以下方法验证，不要直接判 FAIL

### 测试方法

#### 方法 1：CSS 强制显示（验证 DOM 结构存在）

```javascript
// 强制显示可能被 CSS 隐藏的元素
const el = document.querySelector('.control-bar');
if (el) {
  el.style.display = 'flex';
  el.style.visibility = 'visible';
  el.style.opacity = '1';
}
return {
  exists: !!el,
  childCount: el ? el.children.length : 0,
  hasPlayBtn: !!el?.querySelector('.play-btn'),
  hasVolumeSlider: !!el?.querySelector('.volume-slider'),
};
```

#### 方法 2：触发事件模拟状态变化

```javascript
// 模拟文件上传 drop 事件（无需真实文件）
const dropzone = document.querySelector('.dropzone');
if (dropzone) {
  const file = new File([''], 'test.mp3', { type: 'audio/mpeg' });
  const dt = new DataTransfer();
  dt.items.add(file);
  const event = new DragEvent('drop', { dataTransfer: dt, bubbles: true });
  dropzone.dispatchEvent(event);
}
// 等待 React re-render 后检查
setTimeout(() => {
  return {
    controlBarVisible: !!document.querySelector('.control-bar'),
    playBtnVisible: !!document.querySelector('.play-btn'),
  };
}, 500);
```

#### 方法 3：检查 React 内部状态

```javascript
// 通过 React DevTools 或 Fiber 节点检查组件状态
const app = document.querySelector('.app');
const reactKey = Object.keys(app).find(k => k.startsWith('__react'));
let hasUploadComplete = false;
if (reactKey) {
  let fiber = app[reactKey];
  while (fiber) {
    if (fiber.memoizedState && typeof fiber.memoizedState === 'object') {
      // 遍历 state 链表
      let state = fiber.memoizedState;
      while (state) {
        if (state.memoizedState === true || state.memoizedState === false) {
          // 可能是 boolean state
        }
        state = state.next;
      }
    }
    fiber = fiber.return;
  }
}
return { reactKeyFound: !!reactKey };
```

#### 方法 4：源码验证（最直接）

如果浏览器模拟困难，直接读取源码验证：

```bash
# 检查 JSX 中是否有目标元素
grep -n "control-bar" src/App.tsx

# 检查事件处理函数是否非存根
grep -A 5 "togglePlayPause" src/App.tsx

# 检查 state 定义
grep -n "uploadComplete\|isPlaying\|volume" src/App.tsx
```

### 判定标准

| 检查项 | 通过标准 |
|--------|----------|
| JSX 存在性 | 目标元素在 return 语句中定义（非注释、非字符串） |
| 条件逻辑 | state/props 绑定正确，条件渲染逻辑合理 |
| 事件处理 | onClick/onChange 等 handler 正确定义且非空函数 |
| CSS 支持 | 元素的 className 在 CSS 文件中有对应样式 |
| DOM 结构（模拟后）| 强制显示后，子元素（按钮、输入框等）齐全 |

### 常见误区

❌ **错误**："页面加载后看不到 control-bar → FAIL"
✅ **正确**："control-bar 被 `uploadComplete` 条件控制，无音频时隐藏是设计意图。代码审查确认 JSX + handler 完整存在 → PASS"

❌ **错误**："没有实际上传文件测试播放功能 → 无法验证 → FAIL"
✅ **正确**："play 按钮的 onClick 绑定了 `togglePlayPause` 函数，函数内部调用 `audio.play()`。无需真实音频即可验证事件绑定 → PASS"
