"""Real-browser page load probe.

Opens one page in a real Chromium browser (via Patchright) with a fresh cache and reads
the browser's own timing APIs. Runs in its own process because Playwright's sync API and the load
engine's gevent patching don't mix.
"""

BROWSER_MISSING = "Install Google Chrome or Microsoft Edge to measure page load."
CHANNELS = ("chrome", "msedge", None)

METRICS_JS = """async () => {
  const watch = (type, pick) => new Promise((resolve) => {
    let value = null;
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) value = pick(entry) ?? value;
      }).observe({ type, buffered: true });
    } catch {}
    setTimeout(() => resolve(value), 1000);
  });
  const [fcp, lcp] = await Promise.all([
    watch("paint", (e) => (e.name === "first-contentful-paint" ? e.startTime : null)),
    watch("largest-contentful-paint", (e) => e.startTime),
  ]);
  const nav = performance.getEntriesByType("navigation")[0];
  const resources = performance.getEntriesByType("resource");
  return {
    ttfb: nav.responseStart,
    fcp,
    lcp,
    dcl: nav.domContentLoadedEventEnd,
    load: nav.loadEventEnd,
    bytes: nav.transferSize + resources.reduce((sum, r) => sum + (r.transferSize || 0), 0),
    requests: 1 + resources.length,
  };
}"""


def probe_command(url: str, frozen: bool, executable: str, script: str) -> list[str]:
    """Command that relaunches this app to probe `url` and print one JSON line."""
    head = [executable] if frozen else [executable, script]
    return [*head, "--probe", url]


def measure(url: str, timeout_ms: int = 60000) -> dict:
    """Load `url` once in a real browser. Timings are milliseconds from navigation start.

    Drives Chrome or Edge through Patchright, falling back to Patchright's own Chromium
    when neither is installed.
    """
    from patchright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        for channel in CHANNELS:
            try:
                browser = playwright.chromium.launch(channel=channel, headless=True)
            except Error:
                continue
            return _load(
                browser, f"patchright/{channel or 'chromium'}", url, timeout_ms
            )
    return {"ok": False, "error": BROWSER_MISSING}


def _load(browser, name: str, url: str, timeout_ms: int) -> dict:
    from patchright.sync_api import Error

    try:
        page = browser.new_context().new_page()
        page.goto(url, wait_until="load", timeout=timeout_ms)
        metrics = page.evaluate(METRICS_JS, isolated_context=False)
        return {"ok": True, "browser": f"{name} {browser.version}", **metrics}
    except Error as error:
        return {"ok": False, "error": str(error).splitlines()[0]}
    finally:
        browser.close()
