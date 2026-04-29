# 七猫章节自动发布工具

> 🧠 **AI 用户请复制下面这段话给你的 AI 助手：**

<details>
<summary>点击展开复制内容</summary>

```
我需要你帮我部署七猫作者后台章节自动发布工具（qimao-publish）。请按以下步骤操作：

1. 克隆仓库：https://github.com/HomeSafe2017/qimao-publish
2. 安装依赖：pip install playwright && playwright install chromium
3. 帮我配置 config.json：
   - cookie：你引导我获取七猫作者后台的 Cookie（详见 README.md）
   - books：问我有哪些书要管理，帮我添加别名和书籍 ID
4. 帮我安装这个 skill 到你的系统中
5. 之后我说「发布第X章到《书名》」时，你自动帮我填写标题、正文并发布
```

</details>

---

> 通过 Playwright 浏览器自动化操作七猫作者后台（zuozhe.qimao.com），实现章节自动发布。
> 适用于：每日定时更新连载、批量导入存稿、CI/CD 自动化发布管道。

---

## 目录

- [前置条件](#前置条件)
- [快速开始](#快速开始)
- [配置指南](#配置指南)
- [Cookie 获取方法](#cookie-获取方法)
- [使用示例](#使用示例)
- [文件结构](#文件结构)
- [常见问题](#常见问题)
- [注意事项](#注意事项)

---

## 前置条件

| 依赖 | 版本要求 | 安装命令 |
|------|----------|----------|
| Python | 3.8+ | — |
| Playwright | latest | `pip install playwright` |
| Chromium | (由 Playwright 管理) | `playwright install chromium` |

一键安装：
```bash
pip install playwright && playwright install chromium
```

如果在 WSL/Linux 环境中运行，可能需要额外安装系统依赖库：
```bash
sudo apt install -y libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64
```

---

## 快速开始

### 第 1 步：获取 Cookie

打开 Chrome/Edge → 登录 https://zuozhe.qimao.com/ → 按 F12 打开 DevTools → Application 标签 → Cookies → 选中 `zuozhe.qimao.com` → 全部复制。

详细步骤见下方 [Cookie 获取方法](#cookie-获取方法)。

### 第 2 步：配置 config.json

```json
{
  "cookie": "Hm_lvt_xxx=...; qimao-token=...; author-token=...;",
  "books": {
    "时间流域": {
      "id": "11901525",
      "title": "时间流域",
      "default_mode": "publish",
      "default_author_say": "每日更新，感谢追读！"
    }
  }
}
```

### 第 3 步：准备正文文件

纯文本即可，空行分段：
```
这是第一段正文内容。

这是第二段正文内容。

这是第三段正文内容。
```

### 第 4 步：运行

```bash
cd 本项目目录
export QIMAO_COOKIE='<从浏览器复制的完整cookie>'

# 存草稿
python scripts/publish_chapter.py 11901525 "第3章 新章节" /path/to/chapter.txt --book-title "时间流域" --mode draft

# 立即发布
python scripts/publish_chapter.py 11901525 "第3章 新章节" /path/to/chapter.txt --book-title "时间流域" --mode publish

# 定时发布
python scripts/publish_chapter.py 11901525 "第3章 新章节" /path/to/chapter.txt --book-title "时间流域" --mode timed --timed-at "2026-05-01 20:00"
```

---

## 配置指南

所有可变配置集中在项目根目录的 `config.json` 中：

| 字段 | 说明 | 示例 |
|------|------|------|
| `cookie` | 完整 Cookie 字符串（必填） | `Hm_lvt_xxx=...; qimao-token=eyJ...` |
| `books` | 书籍别名映射 | 见下方 |
| `global_defaults.mode` | 默认发布模式 | `publish` |
| `global_defaults.author_say` | 默认作者说 | `""` |

### 书籍别名配置

`books` 对象中每个 key 是一个别名（你习惯称呼的书名），value 包含：

```json
"时间流域": {
  "id": "11901525",           // 七猫书籍 ID
  "title": "时间流域",         // 书籍全名
  "default_mode": "publish",  // 发布模式：draft/publish/timed
  "default_author_say": "每日更新，感谢追读！"
}
```

你可以添加多本书，用不同的别名（如简称、昵称），Hermes Agent 会根据你说的话自动匹配。

---

## Cookie 获取方法

### 方法一：浏览器 DevTools（推荐）

1. 打开 Chrome 或 Edge 浏览器
2. 访问 https://zuozhe.qimao.com/ 并**登录你的作者账号**
3. 按 **F12** 打开开发者工具
4. 切换到 **Application**（应用程序）标签
5. 左侧展开 **Storage → Cookies** → 点击 `zuozhe.qimao.com`
6. **右键任意 cookie** → Select All（全选）→ Copy（复制）
7. 将复制的完整内容粘贴到 `config.json` 的 `cookie` 字段中

### 方法二：浏览器控制台

```javascript
// 在七猫作者后台页面按 F12 → Console 执行
copy(document.cookie);
```

### Cookie 各字段说明

| Cookie | 作用 | 有效期 |
|--------|------|--------|
| `qimao-token` | JWT 鉴权令牌（最关键） | 数小时~1天 |
| `author-token` | 作者身份令牌 | 与登录会话绑定 |
| `puid` | 用户 ID | 持久化 |
| `acw_tc` | 阿里云 WAF 防护 | 会话级别 |

**注意：** `qimao-token` 过期后需要重新获取。建议每次使用前验证，如果报错 cookie 过期就重新复制。

---

## 使用示例

### 从纯文本文件发布

```bash
# 准备正文
cat << 'TEXT' > /tmp/chapter4.txt
林深站在控制室的舷窗前，看着远处星舰编队缓缓进入跃迁状态。

他的通讯器响了三次，他才从沉思中回过神来。

"舰长，跃迁坐标已确认，所有系统准备就绪。"副官的声音从扬声器中传出。

林深没有立刻回答。他还在想刚才那段加密通讯——来自地球最后一艘深空探测船的信号。

"再等五分钟。"他说。
TEXT

# 发布（此时正文已超过1000字）
export QIMAO_COOKIE='...'
python scripts/publish_chapter.py 11901525 "第4章 跃迁" /tmp/chapter4.txt \
  --book-title "时间流域" \
  --mode publish \
  --author-say "求推荐票！"
```

### 带作者说

```bash
python scripts/publish_chapter.py 11901525 "第5章 信号" /tmp/chapter5.txt \
  --book-title "时间流域" \
  --mode draft \
  --author-say "这段存稿先不发布，等我再改改。"
```

---

## 文件结构

```
qimao-publish/
├── SKILL.md                 # Hermes Agent 大模型指令（开发者不用看）
├── README.md                # 本文件 — 用户使用说明
├── config.json              # 配置文件（cookie、书籍别名，已加入 .gitignore）
├── config.example.json      # 配置示例（可安全提交）
├── scripts/
│   └── publish_chapter.py   # 核心发布脚本（686 行）
```

---

## 常见问题

### Q: 报错 "Cookie 可能已过期"
**解决：** 重新打开浏览器 → 登录七猫 → 重新复制 Cookie → 更新 `config.json` 中的 `cookie` 字段。

### Q: 点击「立即发布」后没有反应
**可能原因：** 正文不足 1000 字。七猫要求每章最少 1000 字，字数不足时发布按钮不触发。
**解决：** 检查正文长度，补足 1000 字以上。

### Q: 找不到按钮元素
**可能原因：**
1. Cookie 过期导致页面跳转到登录页
2. 页面更新改变了按钮文本
**解决：** 检查 Cookie 是否有效；查看截图 `/tmp/qimao_result_{id}.png` 确认页面实际状态。

### Q: 章节编号不对
七猫系统根据上一章的 `name_index` 自动递增。建议章节名称中的编号与之一致。

### Q: 需要签名（sign）参数吗？
不需要。脚本使用浏览器模拟，所有 sign 由七猫前端 JS 自动生成。只有直接调 API 才需要逆向 sign 算法。

### Q: Playwright 在 WSL 上报 shared library 错误？
运行：
```bash
sudo apt install -y libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64
```

---

## 注意事项

1. **Cookie 有效期短**：`qimao-token` JWT 通常数小时过期，建议每次使用前验证
2. **正文最少 1000 字**：平台硬性要求
3. **章节名最多 20 字**：超出会被截断
4. **作者说最多 2000 字**：超出会被截断
5. **同一本书不允许重复章节名**
6. **首次发布需确认声明**：每次登录后第一次发布会弹出「重要提醒」
7. **正文使用 HTML 格式**：纯文本自动转换为 `<p>` 包裹段落
8. **headless 模式限制**：可能被风控检测，如果失败可尝试 `headless=False`
9. **并发限制**：同一时间不要对同一本书发布多个章节，避免版本冲突
10. **发布截图**：每次执行后会保存截图到 `/tmp/qimao_result_{id}.png`，用于排查
