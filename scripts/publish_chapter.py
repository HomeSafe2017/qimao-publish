#!/usr/bin/env python3
"""
==========================================================================
七猫作者后台 (zuozhe.qimao.com) — 章节自动发布脚本

功能：
  Playwright 浏览器模拟 → 自动登录 → 填写标题/正文/作者说 →
  支持三种发布模式：存草稿 / 立即发布 / 定时发布

使用方式：
  # 先设置 Cookie（从浏览器复制）
  export QIMAO_COOKIE='Hm_lvt_...=...; qimao-token=...; author-token=...;'

  # 存草稿（默认）
  python publish_chapter.py 11901525 "新章节" /path/to/content.txt

  # 立即发布
  python publish_chapter.py 11901525 "新章节" /path/to/content.txt --mode publish

  # 定时发布
  python publish_chapter.py 11901525 "新章节" /path/to/content.txt \
      --mode timed --timed-at "2026-05-01 20:00"

  # 带作者说
  python publish_chapter.py 11901525 "新章节" /path/to/content.txt \
      --mode publish --author-say "求推荐票！"

依赖：
  pip install playwright && playwright install chromium
==========================================================================
"""

# ======================================================================
# 标准库导入
# ======================================================================
import os          # 环境变量获取（QIMAO_COOKIE）
import sys         # 退出码控制
import argparse    # 命令行参数解析

# ======================================================================
# 第三方库导入
# ======================================================================
from urllib.parse import quote  # URL 编码（中文书名转 URL 安全格式）

# Playwright 是微软开发的浏览器自动化库，支持 Chromium/Firefox/WebKit。
# 它提供比 Selenium 更快的启动速度和更稳定的自动化能力。
try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext
except ImportError:
    print("错误：未安装 Playwright。请执行：")
    print("  pip install playwright && playwright install chromium")
    sys.exit(1)


# ======================================================================
# Cookie 解析器
# ======================================================================

def parse_cookies(cookie_str: str) -> list[dict]:
    """将 HTTP Cookie 字符串解析为 Playwright 接受的 cookie 列表。

    输入格式：从浏览器 DevTools 或 document.cookie 复制的原始字符串。
    例如： "Hm_lvt_xxx=123; qimao-token=eyJ...; author-token=abc%3Adef"

    输出格式：Playwright context.add_cookies() 要求的格式：
      [{"name": "Hm_lvt_xxx", "value": "123", "domain": ".qimao.com", "path": "/"}, ...]

    解析策略：
      1. 按分号分割各个 cookie 条目
      2. 跳过空条目（开头或结尾可能有多余分号）
      3. 按第一个等号分割 name 和 value（value 内部可能含等号）
      4. 为所有 cookie 设置 domain=".qimao.com"（带前缀点号表示所有子域名）

    Args:
        cookie_str: 原始 cookie 字符串（可能含空格和多余分号）

    Returns:
        符合 Playwright cookie 格式的字典列表
    """
    cookies = []
    # 按分号分割每条 cookie
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item:
            continue  # 跳过空条目

        if "=" in item:
            # 只按第一个等号分割（value 中可能包含 =）
            name, value = item.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".qimao.com",  # 前缀点号覆盖所有子域名
                "path": "/",
            })

    return cookies


# ======================================================================
# Cookie 工具函数（JWT 过期检测）
# ======================================================================

import base64
import time
import json as json_module


def decode_jwt_payload(token: str) -> dict | None:
    """解码 JWT payload（不验签），用于检查令牌过期时间。

    Args:
        token: JWT 字符串（如 eyJhbGci...）

    Returns:
        解码后的 payload 字典，解码失败返回 None
    """
    try:
        payload_b64 = token.split(".")[1]
        # base64 URL-safe 解码，补齐 padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        decoded = base64.urlsafe_b64decode(payload_b64)
        return json_module.loads(decoded)
    except (IndexError, ValueError, json_module.JSONDecodeError):
        return None


def extract_qimao_token(cookie_str: str) -> str | None:
    """从 Cookie 字符串中提取 qimao-token 的值。"""
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("qimao-token="):
            return part.split("=", 1)[1]
    return None


def is_token_expired(cookie_str: str, buffer_minutes: int = 15) -> bool:
    """检查 qimao-token JWT 是否已过期或即将过期。

    Args:
        cookie_str: 完整 Cookie 字符串
        buffer_minutes: 提前预警时间（分钟），默认 15 分钟前就算过期

    Returns:
        True=已过期或即将过期, False=仍然有效
    """
    token = extract_qimao_token(cookie_str)
    if not token:
        return True  # 没有 token，视为过期

    payload = decode_jwt_payload(token)
    if not payload or "exp" not in payload:
        return True  # 无法解析，视为过期

    exp_time = payload["exp"]
    now = time.time()
    return now >= (exp_time - buffer_minutes * 60)


# ======================================================================
# DOM 交互工具函数
# ======================================================================

def click_by_text(page: Page, text: str) -> bool:
    """在页面中按元素的可见文本（innerText.trim()）精确匹配并点击。

    为什么不使用 CSS 选择器？
      七猫前端（Vue SPA）的 class 名包含 Webpack 构建哈希，
      每次发布都可能变化（如 qm-btn-abc123 → qm-btn-def456）。
      而按钮的文本内容（"存为草稿"、"立即发布"、"确认发布"）是
      稳定的用户可见标识，不受构建版本影响。

    搜索策略：
      1. 遍历页面中所有 <a>、<button>、<span> 元素
      2. 比较每个元素的 innerText.trim() 是否完全等于目标文本
      3. 找到后直接执行原生 click()

    Args:
        page: Playwright Page 对象
        text: 要匹配的精确文本（不含前后空格）

    Returns:
        是否成功找到并点击了目标元素
    """
    return page.evaluate(
        """(text) => {
            // 遍历所有可交互标签，按文本精确匹配
            const all = document.querySelectorAll('a, button, span');
            for (const el of all) {
                if (el.innerText.trim() === text) {
                    el.click();  // 触发原生 click 事件
                    return true;
                }
            }
            return false;
        }""",
        text,
    )


# ======================================================================
# 主发布逻辑
# ======================================================================

def publish_chapter(
    book_id: str,
    book_title: str,
    chapter_name: str,
    content_html: str,
    cookie_str: str,
    mode: str = "draft",
    author_say: str = "",
    timed_at: str = "",
    config_path: str = "",  # 可选，非空时发布成功后自动更新 cookie
) -> bool:
    """七猫章节自动发布主函数。

    这是整个脚本的核心函数，实现了完整的浏览器自动化流程：
      启动浏览器 → 注入 Cookie → 导航到上传页 → 填写内容 →
      按模式执行保存/发布 → 处理对话框 → 验证结果

    三种发布模式：
      - draft（存草稿）：
          只触发 auto-save-chapter API，不提交审核。
          适合：写了一半临时保存、批量存稿、测试验证。

      - publish（立即发布）：
          先自动保存草稿，然后调用 upload-chapter API 正式发布。
          如果当前登录会话是首次发布，还会处理"重要提醒"对话框。
          适合：写完即发的日常更新。

      - timed（定时发布）：
          先保存草稿，再弹出定时选择面板设置发布时间，
          最后调用 upload-chapter(publish_type=2) 并传入定时时间。
          适合：设定未来某个时间点自动发布。

    章节正文要求（七猫平台限制）：
      - 最少 1000 字，最多 50000 字
      - 章节名称最多 20 个字
      - 正文为 HTML 格式（<p> 标签或 <br> 换行）
      - 作者说最多 2000 字

    Args:
        book_id:        书籍 ID（从 URL 参数 id= 获取）
        book_title:     书籍名称（用于 URL 编码和日志输出）
        chapter_name:   章节标题，如 "跃迁"（只传标题，七猫自动编号）
        content_html:   正文 HTML 内容（支持 <p> 标签包裹的段落）
        cookie_str:     完整登录 Cookie 字符串
        mode:           发布模式 — "draft" | "publish" | "timed"
        author_say:     作者说的话（可选，留空不填写）
        timed_at:       定时发布时间，mode="timed" 时必填
                        格式： "YYYY-MM-DD HH:mm"，如 "2026-05-01 20:00"

    Returns:
        True  — 操作成功（保存为草稿 或 成功发布）
        False — 操作失败（Cookie 过期、按钮未找到等）
    """
    # 注入自定义函数：强制点击按钮（支持 Vue 响应式）
    def force_click(page: Page, selector: str, text: str | None = None) -> bool:
        """增强版点击：支持多种策略按文本/选择器点击按钮。
        
        策略：
        1. 按文本精确匹配
        2. 按文本模糊匹配（包含）
        3. 按 CSS 选择器
        """
        return page.evaluate(
            """(args) => {
                const {text, selector} = args;
                
                // 策略1：精确文本匹配
                if (text) {
                    const all = document.querySelectorAll('button, a, span, div[role="button"]');
                    for (const el of all) {
                        if (el.innerText.trim() === text) {
                            // Vue 兼容：触发 mousedown + mouseup + click
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return true;
                        }
                    }
                }
                
                // 策略2：CSS 选择器
                if (selector) {
                    const el = document.querySelector(selector);
                    if (el) {
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        return true;
                    }
                }
                
                return false;
            }""",
            {"text": text, "selector": selector},
        )

    def handle_any_dialog(page: Page) -> bool:
        """通用对话框处理：勾选所有可见对话框中的复选框，然后点击确认按钮。
        
        处理流程：
        1. 等待对话框渲染
        2. 勾选所有未勾选的复选框
        3. 点击对话框中的主要按钮（el-button--primary 或包含确定/确认/知晓等文本的按钮）
        4. 等待对话框关闭
        """
        return page.evaluate(
            """() => {
                // 查找所有可见对话框
                function getVisibleDialogs() {
                    const dialogs = document.querySelectorAll(
                        '.el-dialog, .el-dialog__wrapper, .v-modal, .el-overlay'
                    );
                    const visible = [];
                    for (const d of dialogs) {
                        const style = window.getComputedStyle(d);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            visible.push(d);
                        }
                    }
                    return visible.length > 0 ? visible : [];
                }
                
                let dialogs = getVisibleDialogs();
                if (dialogs.length === 0) return false;
                
                // 1. 勾选所有可见的复选框
                const checkboxes = document.querySelectorAll(
                    '.el-checkbox, .el-checkbox__input, input[type="checkbox"]'
                );
                for (const cb of checkboxes) {
                    const input = cb.querySelector('input[type="checkbox"]') || cb;
                    if (input && !input.checked) {
                        // 点击复选框或其label
                        const label = cb.closest('.el-checkbox') || cb;
                        label.click();
                        input.checked = true;
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                }
                
                // 2. 点击对话框中的确认按钮（多种策略）
                // 策略A: 文本匹配
                const confirmTexts = ['确认发布', '确认', '确定', '我知道了', '已阅读并知晓'];
                const allBtns = document.querySelectorAll('button, a, span, div[role="button"]');
                for (const btn of allBtns) {
                    const t = btn.innerText.trim();
                    for (const ct of confirmTexts) {
                        if (t === ct || t.includes(ct)) {
                            btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return true;
                        }
                    }
                }
                
                // 策略B: .el-button--primary
                const primaryBtns = document.querySelectorAll('.el-button--primary');
                for (const btn of primaryBtns) {
                    const style = window.getComputedStyle(btn);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        return true;
                    }
                }
                
                // 策略C: 对话框底部的任意按钮
                for (const d of dialogs) {
                    const btns = d.querySelectorAll('.el-dialog__footer button, .el-dialog__footer a');
                    for (const btn of btns) {
                        btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        return true;
                    }
                }
                
                return true;
            }"""
        )

    def wait_dialog_closed(page: Page, timeout: int = 5) -> bool:
        """等待所有对话框关闭，超时返回 False。"""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            closed = page.evaluate(
                """() => {
                    const dialogs = document.querySelectorAll(
                        '.el-dialog, .el-dialog__wrapper, .v-modal, .el-overlay'
                    );
                    for (const d of dialogs) {
                        const style = window.getComputedStyle(d);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            return false;
                        }
                    }
                    return true;
                }"""
            )
            if closed:
                return True
            page.wait_for_timeout(500)
        return False
    print(f"[七猫发布] ⏳ 开始处理: {book_title} - {chapter_name}")
    print(f"[七猫发布] 📋 模式: {mode}")
    print(f"[七猫发布] 📏 正文长度: {len(content_html)} 字符")
    if author_say:
        print(f"[七猫发布] 💬 作者说: {author_say[:60]}...")
    if timed_at:
        print(f"[七猫发布] ⏰ 定时: {timed_at}")

    # ---- 启动浏览器 ----
    # sync_playwright() 返回一个 SyncPlaywright 上下文管理器
    # with 块结束时自动关闭所有浏览器资源
    with sync_playwright() as playwright:

        # 启动 Chromium 浏览器（headless=True → 无头模式，不显示窗口）
        # headless=False 可在调试时看到浏览器操作过程
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--font-render-hinting=none",
            ],
        )

        # new_context 创建浏览器上下文（隔离的会话环境）
        # 设置 UA 和视口大小以模拟真实浏览器行为，降低被风控识别的风险
        context: BrowserContext = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )

        # ---- 注入身份 Cookie ----
        # 这是最关键的一步：通过预置 Cookie 实现"免登录"。
        # 七猫使用 qimao-token（JWT）做鉴权，只需要 Cookie 就可跳过登录页面。
        # 注意：添加 Cookie 必须在 page.goto() 之前，否则 Cookie 不会生效。
        cookies = parse_cookies(cookie_str)
        context.add_cookies(cookies)

        # 创建新页面并导航到上传章节页
        page: Page = context.new_page()

        # 构建 URL —— id 是书籍 ID，title 是 URL 中的书名字段
        # urllib.parse.quote 对中文书名进行 URL 编码（如 "时间流域" → "%E6%97%B6%E9%97%B4%E6%B5%81%E5%9F%9F"）
        url = (
            f"https://zuozhe.qimao.com/front/book-upload"
            f"?id={book_id}&title={quote(book_title)}"
        )
        print(f"[七猫发布] 🌐 导航到: {url}")

        # wait_until="networkidle" 表示等待网络空闲（所有请求完成）后再继续
        # 30秒超时防止页面加载卡死
        page.goto(url, wait_until="networkidle", timeout=30000)

        # 额外等待 3 秒，给 Vue SPA 足够的渲染时间
        # networkidle 不保证所有异步组件都已渲染完毕
        page.wait_for_timeout(3000)

        # ---- 验证登录状态 ----
        # 检查 #app 元素中是否有足够的内容来判断是否成功加载了作者后台
        # 如果 Cookie 过期，页面会重定向到登录页，app 将为空或只显示登录框
        has_login: bool = page.evaluate(
            """() => {
                const app = document.querySelector('#app');
                // app 必须有子元素且文本内容超过 100 字符才算登录成功
                return app && app.children.length > 0 && app.innerText.length > 100;
            }"""
        )
        if not has_login:
            print("[七猫发布] ❌ 错误：Cookie 可能已过期，请重新获取")
            print("  获取方法：登录七猫作者后台 → F12 → Application → Cookies")
            browser.close()
            return False

        # ---- 填写章节标题 ----
        # 七猫前端使用 Vue 框架，input 的 value 通过 v-model 双向绑定。
        # 普通的 element.value = 'xxx' 赋值不会被 Vue 感知，
        # 必须：
        #   1. 通过 Object.getOwnPropertyDescriptor 获取原生的 value setter
        #   2. 调用 setter 直接修改底层 DOM 属性
        #   3. 手动 dispatch input 事件通知 Vue 更新
        #   4. 手动 dispatch change 事件触发校验逻辑
        page.evaluate(
            """(name) => {
                const inp = document.querySelector(
                    'input[placeholder*="章节名称"]'
                );
                if (inp) {
                    // 获取 input 元素的原生 value setter（绕过 Vue 的代理）
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    // 在 setter 的 this 上下文中设置值
                    setter.call(inp, name);
                    // 触发 Vue 的响应式更新（input 事件）
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    // 触发表单校验逻辑（change 事件）
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""",
            chapter_name,
        )
        print(f"[七猫发布] ✏️  标题已填写: {chapter_name}")

        # ---- 填写正文 ----
        # 七猫的编辑器是一个 contenteditable 的 div（富文本编辑器）。
        # 通过设置 innerHTML 直接注入 HTML 内容，效率最高。
        # 格式要求：使用 <p> 包裹段落，换行用 <br>。
        # 注意：innerHTML 设置后必须 dispatch input 事件，
        # 否则 Vue 组件不会感知内容变化，字数统计也不会更新。
        page.evaluate(
            """(html) => {
                const div = document.querySelector(
                    'div.q-contenteditable.book'
                );
                if (div) {
                    div.innerHTML = html;  // 直接注入 HTML
                    div.dispatchEvent(
                        new Event('input', {bubbles: true})
                    );
                }
            }""",
            content_html,
        )
        print(f"[七猫发布] ✏️  正文已填写 ({len(content_html)} 字符)")

        # ---- 填写作者说（可选） ----
        # 作者说位于正文下方的 contenteditable div，
        # 使用 font-size-14 样式区分。
        if author_say:
            page.evaluate(
                """(text) => {
                    const div = document.querySelector(
                        'div.q-contenteditable.font-size-14'
                    );
                    if (div) {
                        div.innerHTML = text;
                        div.dispatchEvent(
                            new Event('input', {bubbles: true})
                        );
                    }
                }""",
                author_say,
            )
            print(f"[七猫发布] ✏️  作者说已填写")

        # 等待 Vue 完成响应式更新
        page.wait_for_timeout(2000)

        # ================================================================
        # 模式分发
        # ================================================================

        if mode == "draft":
            # ---- 模式：存为草稿 ----
            # 这是最简单的模式：只触发 auto-save-chapter API
            # 不会提交审核，适合临时保存或批量存稿
            if not click_by_text(page, "存为草稿"):
                print(
                    "[七猫发布] ❌ 错误：找不到'存为草稿'按钮，"
                    "可能是 Cookie 过期或页面未正确加载"
                )
                browser.close()
                return False

            print("[七猫发布] ✅ 已点击'存为草稿'，等待保存...")
            page.wait_for_timeout(5000)

        elif mode == "publish":
            # ---- 模式：立即发布 ----
            # 流程：
            #   1. 先"存为草稿"（auto-save-chapter）
            #   2. 再点击"立即发布"（触发 regular-time-data GET 请求）
            #   3. 使用增强对话框处理（自动勾选复选框 + 点击确认按钮）
            #   4. 在"确认发布"对话框中点击确认（触发 upload-chapter POST）
            #   5. 成功后页面自动跳转到书籍管理页（book-manage/manage）

            # 使用增强对话框处理（自动勾选复选框 + 点击确认按钮）
            click_by_text(page, "存为草稿")
            print("[七猫发布] 📦 已保存草稿")
            page.wait_for_timeout(3000)

            click_by_text(page, "立即发布")
            print("[七猫发布] 🚀 已点击'立即发布'")
            page.wait_for_timeout(3000)

            # 第1轮：处理所有弹出的对话框（重要提醒/确认发布等）
            handled = handle_any_dialog(page)
            if handled:
                print("[七猫发布] 📋 已处理对话框（第1轮）")
            page.wait_for_timeout(2000)

            # 等待对话框关闭
            if not wait_dialog_closed(page, timeout=5):
                print("[七猫发布] 🔄 对话框仍可见，再次处理...")
                handle_any_dialog(page)
                page.wait_for_timeout(3000)
                if wait_dialog_closed(page, timeout=5):
                    print("[七猫发布] ✅ 对话框已关闭")
                else:
                    print("[七猫发布] ⚠️  对话框可能未完全关闭，继续...")

            page.wait_for_timeout(2000)

            # 如果还有对话框（确认发布），再处理一轮
            if not wait_dialog_closed(page, timeout=2):
                print("[七猫发布] 📋 发现对话框仍存在，再处理...")
                handle_any_dialog(page)
                page.wait_for_timeout(3000)
                wait_dialog_closed(page, timeout=8)

            page.wait_for_timeout(3000)

        elif mode == "timed":
            # ---- 模式：定时发布 ----
            # 流程：
            #   1. 先"存为草稿"
            #   2. 点击"定时发布"
            #   3. 处理"重要提醒"对话框
            #   4. 在定时设置面板中填写发布时间
            #   5. 点击确认（触发 upload-chapter(publish_type=2)）
            if not timed_at:
                print(
                    "[七猫发布] ❌ 错误：定时模式(--mode timed)"
                    "需要提供 --timed-at 参数"
                )
                browser.close()
                return False

            click_by_text(page, "存为草稿")
            page.wait_for_timeout(3000)

            click_by_text(page, "定时发布")
            print(f"[七猫发布] ⏰ 已点击'定时发布'")
            page.wait_for_timeout(3000)

            # 处理"重要提醒"对话框
            page.evaluate(
                """() => {
                    const dialogs = document.querySelectorAll(
                        '.el-dialog, .el-dialog__wrapper'
                    );
                    for (const d of dialogs) {
                        if (d.style.display !== 'none') {
                            const btns = d.querySelectorAll(
                                '.el-button--primary, button'
                            );
                            for (const btn of btns) {
                                btn.click();
                                return;
                            }
                        }
                    }
                }"""
            )
            page.wait_for_timeout(2000)

            # 在定时时间输入框中填写时间
            # 七猫的定时输入框在 Element UI 的 Dialog 中
            page.evaluate(
                """(t) => {
                    const inputs = document.querySelectorAll(
                        '.el-dialog input, .el-dialog__wrapper input'
                    );
                    for (const inp of inputs) {
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(inp, t);
                        inp.dispatchEvent(
                            new Event('input', {bubbles: true})
                        );
                    }
                }""",
                timed_at,
            )
            page.wait_for_timeout(1000)

            # 点击确认按钮
            click_by_text(page, "确认发布") or click_by_text(page, "确定")
            page.wait_for_timeout(5000)

        # ================================================================
        # 结果验证
        # ================================================================

        final_url: str = page.url
        print(f"[七猫发布] 🔗 最终 URL: {final_url}")

        # 发布成功后，自动捕获服务器返回的新 cookie，更新 config.json
        if config_path and ("book-manage" in final_url or "manage" in final_url):
            try:
                # Playwright context.cookies() 返回所有 cookie（包括 Set-Cookie 更新的）
                raw_cookies = context.cookies()
                cookie_parts = []
                for c in raw_cookies:
                    # 排除 domain 不是 qimao.com 的 cookie
                    if "qimao.com" in c.get("domain", ""):
                        cookie_parts.append(f"{c['name']}={c['value']}")
                if cookie_parts:
                    new_cookie_str = "; ".join(cookie_parts)
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json_module.load(f)
                    config_data["cookie"] = new_cookie_str
                    with open(config_path, "w", encoding="utf-8") as f:
                        json_module.dump(config_data, f, ensure_ascii=False, indent=2)
                    print(f"[七猫发布] 💾 Cookie 已自动更新到 {config_path}")
            except Exception as e:
                print(f"[七猫发布] ⚠️  Cookie 自动更新失败: {e}")

        browser.close()

        # 判断是否成功
        # 发布成功后七猫会自动跳转到 book-manage/manage 页面
        success: bool = "book-manage" in final_url or "manage" in final_url

        if success:
            action_label = "发布" if mode != "draft" else "保存为草稿"
            print(f"[七猫发布] 🎉 成功！章节已{action_label}")
        else:
            print(
                f"[七猫发布] ⚠️  操作完成（结果不确定，"
                f"请查看截图确认）"
            )

        return success


# ======================================================================
# 命令行入口
# ======================================================================

def main() -> None:
    """CLI 入口函数。

    解析命令行参数，读取正文文件，调用 publish_chapter() 执行发布。
    """
    # ---- 参数解析 ----
    parser = argparse.ArgumentParser(
        description="七猫作者后台章节自动发布工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 基本用法（存草稿）
  export QIMAO_COOKIE='Hm_lvt_xxx=...; qimao-token=...;'
  python publish_chapter.py 11901525 "新章节" /tmp/chapter.txt

  # 立即发布
  python publish_chapter.py 11901525 "新章节" /tmp/chapter.txt --mode publish

  # 定时发布
  python publish_chapter.py 11901525 "新章节" /tmp/chapter.txt \\
      --mode timed --timed-at "2026-05-01 20:00"

  # 带作者说
  python publish_chapter.py 11901525 "新章节" /tmp/chapter.txt \
      --mode publish --author-say "求推荐，求月票！"
        """,
    )

    # 位置参数
    parser.add_argument(
        "book_id",
        help="书籍 ID（从上传页 URL 的 id 参数获取）",
    )
    parser.add_argument(
        "chapter_name",
        help="章节名称，如 '新章节'（只传标题，七猫自动编号，最多20字）",
    )
    parser.add_argument(
        "content_file",
        help="正文文件路径。支持："
        "1) HTML 格式（直接作为 innerHTML 注入）"
        "2) 纯文本格式（自动用 <p> 标签包裹段落，空行分隔段落）",
    )

    # 可选参数
    parser.add_argument(
        "--book-title",
        default="",
        help="书籍名称（用于 URL 编码和日志输出，可选但推荐提供）",
    )
    parser.add_argument(
        "--mode",
        choices=["draft", "publish", "timed"],
        default="draft",
        help="发布模式：draft(存草稿,默认) | publish(立即发布) | timed(定时发布)",
    )
    parser.add_argument(
        "--timed-at",
        default="",
        help="定时发布时间，--mode=timed 时必填。格式：'YYYY-MM-DD HH:mm'",
    )
    parser.add_argument(
        "--author-say",
        default="",
        help="作者说的话（显示在章节末尾，最多2000字）",
    )
    parser.add_argument(
        "--config",
        default="",
        help="config.json 路径（可选，用于自动刷新 cookie）",
    )

    args = parser.parse_args()

    # ---- 获取 Cookie ----
    cookie: str | None = os.environ.get("QIMAO_COOKIE")
    
    if not cookie:
        print("错误：请设置环境变量 QIMAO_COOKIE")
        print()
        print("  export QIMAO_COOKIE='Hm_lvt_xxx=...; qimao-token=...; author-token=...;'")
        print()
        print("获取方法：登录七猫作者后台 → F12 → Application → Cookies → 复制全部")
        sys.exit(1)

    # ---- 读取正文文件 ----
    try:
        with open(args.content_file, "r", encoding="utf-8") as f:
            raw_content: str = f.read()
    except FileNotFoundError:
        print(f"错误：找不到正文文件: {args.content_file}")
        sys.exit(1)
    except IOError as e:
        print(f"错误：读取正文文件失败: {e}")
        sys.exit(1)

    # ---- 自动转换纯文本为 HTML ----
    # 如果内容不包含 HTML 标签，视为纯文本，
    # 按空行分割段落，用 <p> 包裹。
    # 这样用户可以直接写纯文本文件，无需关心 HTML 格式。
    if not raw_content.strip().startswith("<"):
        paragraphs: list[str] = [
            f"<p>{p}</p>"
            for p in raw_content.strip().split("\n\n")
            if p.strip()
        ]
        content_html: str = "\n".join(paragraphs)
    else:
        content_html = raw_content

    # ---- 执行发布 ----
    config_path = os.path.abspath(args.config) if args.config else ""
    success: bool = publish_chapter(
        book_id=args.book_id,
        book_title=args.book_title or f"书籍{args.book_id}",
        chapter_name=args.chapter_name,
        content_html=content_html,
        cookie_str=cookie,
        mode=args.mode,
        author_say=args.author_say,
        timed_at=args.timed_at,
        config_path=config_path,
    )

    sys.exit(0 if success else 1)


# ======================================================================
# 程序入口
# ======================================================================

if __name__ == "__main__":
    main()
