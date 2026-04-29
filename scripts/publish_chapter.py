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
  python publish_chapter.py 11901525 "第3章 新章节" /path/to/content.txt

  # 立即发布
  python publish_chapter.py 11901525 "第3章 新章节" /path/to/content.txt --mode publish

  # 定时发布
  python publish_chapter.py 11901525 "第3章 新章节" /path/to/content.txt \
      --mode timed --timed-at "2026-05-01 20:00"

  # 带作者说
  python publish_chapter.py 11901525 "第3章 新章节" /path/to/content.txt \
      --mode publish --author-say "求推荐票！"

依赖：
  pip install playwright && playwright install chromium

作者： Hermes Agent
日期： 2026-04-29
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
        chapter_name:   章节标题，如 "第3章 跃迁"
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
    # ---- 日志头 ----
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
        browser = playwright.chromium.launch(headless=True)

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
            page.screenshot(path=f"/tmp/qimao_draft_{book_id}.png")

        elif mode == "publish":
            # ---- 模式：立即发布 ----
            # 流程：
            #   1. 先"存为草稿"（auto-save-chapter）
            #   2. 再点击"立即发布"（触发 regular-time-data GET 请求）
            #   3. 如果弹出"重要提醒"对话框，点击确认
            #   4. 在"确认发布"对话框中点击确认（触发 upload-chapter POST）
            #   5. 成功后页面自动跳转到书籍管理页（book-manage/manage）

            click_by_text(page, "存为草稿")
            print("[七猫发布] 📦 已保存草稿")
            page.wait_for_timeout(3000)

            click_by_text(page, "立即发布")
            print("[七猫发布] 🚀 已点击'立即发布'")
            page.wait_for_timeout(3000)

            # 处理"重要提醒"对话框（仅首次发布时出现）
            # 这是七猫的内容合规声明，需要用户勾选"我已阅读并知晓"
            handled = page.evaluate(
                """() => {
                    // 先尝试精确匹配"我已阅读并知晓"文本
                    const items = document.querySelectorAll(
                        '.el-dialog__wrapper, .el-dialog, button, a, span'
                    );
                    for (const el of items) {
                        const t = el.innerText.trim();
                        if (
                            (t.includes('已阅读') ||
                             t.includes('知晓') ||
                             t.includes('阅读并')) &&
                            el.closest('.el-dialog')
                        ) {
                            el.click();
                            return true;
                        }
                    }
                    // fallback：点击对话框中的任意 .el-button--primary
                    const dialogs = document.querySelectorAll(
                        '.el-dialog, .el-dialog__wrapper'
                    );
                    for (const d of dialogs) {
                        if (d.style.display !== 'none') {
                            const btns = d.querySelectorAll(
                                '.el-button--primary, .qm-btn.primary, .el-button'
                            );
                            for (const btn of btns) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }"""
            )
            if handled:
                print("[七猫发布] 📋 已处理'重要提醒'对话框")
            else:
                print("[七猫发布] ℹ️  无'重要提醒'对话框（非首次发布）")
            page.wait_for_timeout(2000)

            # 点击"确认发布"按钮（Element UI 对话框）
            # 确认发布对话框中会展示：书名、上一章、当前章、字数、内容声明
            if click_by_text(page, "确认发布"):
                print("[七猫发布] ✅ 已点击'确认发布'，等待发布结果...")
            else:
                # fallback：直接在可见对话框中找 .el-button--primary 按钮
                page.evaluate(
                    """() => {
                        const dialogs = document.querySelectorAll(
                            '.el-dialog, .el-dialog__wrapper'
                        );
                        for (const d of dialogs) {
                            if (d.style.display !== 'none') {
                                const btns = d.querySelectorAll(
                                    '.el-button--primary'
                                );
                                for (const btn of btns) {
                                    btn.click();
                                    return;
                                }
                            }
                        }
                    }"""
                )
                print(
                    "[七猫发布] ℹ️  已尝试点击对话框中的确认按钮"
                )

            page.wait_for_timeout(5000)

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

        # 保存截图用于事后排查
        screenshot_path = f"/tmp/qimao_result_{book_id}.png"
        page.screenshot(path=screenshot_path)
        print(f"[七猫发布] 📸 截图保存到: {screenshot_path}")

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
  python publish_chapter.py 11901525 "第3章 新章节" /tmp/chapter.txt

  # 立即发布
  python publish_chapter.py 11901525 "第3章 新章节" /tmp/chapter.txt --mode publish

  # 定时发布
  python publish_chapter.py 11901525 "第3章 新章节" /tmp/chapter.txt \\
      --mode timed --timed-at "2026-05-01 20:00"

  # 带作者说
  python publish_chapter.py 11901525 "第3章 新章节" /tmp/chapter.txt \\
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
        help="章节名称，如 '第3章 新章节'（最多20字）",
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

    args = parser.parse_args()

    # ---- 获取 Cookie ----
    # Cookie 通过环境变量传入，而不是命令行参数
    # 原因：避免在命令行历史或进程列表中泄露敏感信息
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
    success: bool = publish_chapter(
        book_id=args.book_id,
        book_title=args.book_title or f"书籍{args.book_id}",
        chapter_name=args.chapter_name,
        content_html=content_html,
        cookie_str=cookie,
        mode=args.mode,
        author_say=args.author_say,
        timed_at=args.timed_at,
    )

    sys.exit(0 if success else 1)


# ======================================================================
# 程序入口
# ======================================================================

if __name__ == "__main__":
    main()
