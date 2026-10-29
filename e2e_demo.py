"""Playwright 真浏览器验证 demo 页：加载 -> 拉配置 -> 提交 MOCK 任务 -> 等待 COMPLETED。"""
from playwright.sync_api import sync_playwright

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 1000})
    pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto("http://127.0.0.1:8979/demo", wait_until="networkidle", timeout=45000)
    pg.wait_for_function("document.getElementById('cfg').innerText.length > 20", timeout=45000)
    cfg = pg.inner_text("#cfg")
    print("cfg_lines:", len(cfg.splitlines()))
    pg.select_option("#mode", "mock")
    pg.click("#go")
    pg.wait_for_function("document.getElementById('job-status').innerText.includes('COMPLETED')", timeout=40000)
    print("status:", pg.inner_text("#job-status"))
    print("预览:", pg.inner_text("#preview").replace("\n", " | ")[:120])
    print("log:", pg.inner_text("#log").splitlines()[0].encode("utf-8","replace").decode("utf-8")[:120])
    pg.screenshot(path="artifacts/demo_completed.png", full_page=True)
    b.close()
print("console errors:", errors[:5], "count=", len(errors))
