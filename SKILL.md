---
name: qimao-publish
description: 七猫作者后台章节自动发布。通过 Playwright 浏览器模拟自动填写章节标题、正文、作者说，支持存草稿/立即发布/定时发布三种模式。书籍别名系统自动匹配，cookie 过期引导用户更新。
tags:
  - qimao
  - 七猫
  - 小说发布
  - 浏览器自动化
  - playwright
---

# 七猫章节发布 — LLM 使用指南

> 本文件是大模型（你）的操作指引。部署教程、Cookie 获取步骤等在 README.md 中。
> 所有可变配置（cookie、书籍别名、默认发布模式）在 `config.json` 中。

## 如何工作

1. 你读取 `config.json` 获取 cookie 和书籍别名列表
2. 用户说「发布xxx到《时间流域》」→ 你按别名字典找到 book_id
3. 调用 `scripts/publish_chapter.py` 执行发布
4. 发布失败（Cookie 过期）→ 引导用户重新提供 cookie → 写入 config.json

## config.json 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `cookie` | string | 完整 Cookie 字符串（空串=未设置，需引导用户填写） |
| `books` | object | 书籍别名映射。key=用户称呼的书名，value={id, title, default_mode, default_author_say} |
| `books.<name>.id` | string | 七猫书籍 ID（从上传页 URL 的 id= 参数获取） |
| `books.<name>.title` | string | 书籍全名，用于 URL 编码和日志 |
| `books.<name>.default_mode` | string | 该书默认发布模式：draft/publish/timed |
| `books.<name>.default_author_say` | string | 该书默认作者说 |
| `global_defaults.mode` | string | 全局默认发布模式 |
| `global_defaults.author_say` | string | 全局默认作者说 |

## 别名系统

`config.json` 中的 `books` 对象即别名映射。例如：

```json
"books": {
  "时间流域": { "id": "11901525", "title": "时间流域", "default_mode": "publish" },
  "新书":     { "id": "11903000", "title": "新书名", "default_mode": "draft" }
}
```

用户说「发布到时间流域」→ 你查 books["时间流域"] → book_id=11901525。
用户说「存草稿到新书」→ 你查 books["新书"] → book_id=11903000 + mode=draft。

**如果用户提到的书名不在 config.json 中：** 向用户询问书籍 ID 和书名，添加到 config.json 后再执行。

## Cookie 管理流程

### 首次使用（cookie 为空）
1. 告诉用户：需要七猫作者后台的 Cookie
2. 提供获取方法简述（详见 README.md）
3. 用户提供 cookie 字符串后，写入 config.json

### Cookie 过期处理
执行发布时脚本返回失败（exit_code=1），如果在输出中看到「Cookie 可能已过期」：
1. 通过微信通知用户：「七猫 Cookie 已过期，请重新获取并发给我」
2. 同时提供 README.md 中的获取步骤
3. 用户提供新 cookie → 写入 config.json → 重新执行发布

## 脚本调用方式

```bash
export QIMAO_COOKIE='<来自config.json的cookie>'
python scripts/publish_chapter.py <book_id> <章节名> <正文文件路径> \
  --book-title <书名> --mode <draft|publish|timed> \
  [--author-say "作者说"] [--timed-at "YYYY-MM-DD HH:mm"]
```

**重要：** 始终通过 `export QIMAO_COOKIE=...` 设置环境变量，不要在命令行参数中传 cookie。

**正文文件支持：**
- HTML 格式（直接含 `<p>` 标签）
- 纯文本格式（自动用 `<p>` 包裹段落，空行分段）
- 正文要求 ≥1000 字（七猫硬性限制），不足时发布按钮不会触发

## 完整发布流程时序（简述）

1. 启动 Chromium（headless）→ 注入 Cookie → 导航到上传页
2. 验证登录状态 → 填写章节标题 → 填写正文 → 填写作者说（可选）
3. draft：点击「存为草稿」→ 截图保存
4. publish：存草稿 → 点击「立即发布」→ 处理「重要提醒」对话框 → 点击「确认发布」
5. timed：存草稿 → 点击「定时发布」→ 处理对话框 → 填写时间 → 确认

## 常见故障（LLM 侧排查）

| 症状 | 排查 |
|------|------|
| 找不到按钮 | 通常是 cookie 过期导致页面跳转到登录页 |
| 发布后 URL 不含 book-manage | 结果不确定，检查截图 /tmp/qimao_result_{id}.png |
| Cookie 报错 | 引导用户重新获取 |
| 章节名重复 | 提示用户换一个章节名称 |
| 字数不够 | 检查正文是否 ≥1000 字 |
| Playwright 未安装 | 见 README.md 前置条件 |
| 定时模式报错 | 检查 timed_at 格式 "YYYY-MM-DD HH:mm" |

## 关键限制

- 正文最少 1000 字，最多 50000 字
- 章节名最多 20 个字
- 作者说最多 2000 字
- 同一本书不允许重复章节名
- 每次登录后首次发布会出现「重要提醒」对话框
- headless 模式可能被风控检测，如失败尝试 headless=False
