# Swarmscope

Find out how many users your site takes before it slows down, and whether it's your site or your host that gives way first. Swarmscope is a desktop app for macOS (Apple Silicon) and Windows that drives [Locust](https://locust.io) from one panel: you set the URL and the crowd, it plots req/s, p95 and errors live, opens the page in a real browser every 15 seconds to time how fast it actually shows up, keeps the host's throttling (HTTP 429) apart from your site's own failures, and ends each run with a verdict: users held, page load alone versus under load, errors after retries, and when the first 429 hit.

One thing it can't do: load traffic never runs JavaScript, so Google Analytics won't see these visits. To check analytics, open the site in a few real browsers or send GA4 Measurement Protocol events.

## Commands

The project's commands live in `pyproject.toml` under `[tool.taskipy.tasks]`, like `scripts` in a `package.json`. Run them from this folder with `uv run task <name>`, or as plain `task <name>` after `source .venv/bin/activate`.

| Task | What it does |
|---|---|
| `dev` | Opens the app from source, no build needed |
| `build` | Builds the desktop app into `dist/` |
| `open` | Opens the built app |
| `test` | Runs the tests |
| `lint` | Lints with ruff |
| `format` | Formats with ruff |

## Desktop app

Build it on the machine you'll run it on:

```bash
uv sync
```

```bash
uv run task build
```

macOS gives you `dist/Swarmscope.app`. Windows gives you `dist/Swarmscope/Swarmscope.exe`. PyInstaller can't cross-compile, so the Windows build has to happen on Windows. The `build` workflow in `.github/workflows` does both: push a `v*` tag or start it by hand from the Actions tab, then download the zips from the run.

The builds aren't signed. On a Mac, a copy downloaded from CI needs its quarantine flag cleared once:

```bash
xattr -dr com.apple.quarantine /Applications/Swarmscope.app
```

On Windows, SmartScreen shows a warning the first time; click "More info", then "Run anyway".

To run from source without building:

```bash
uv run task dev
```

## The settings

| Field | Default | What it does |
|---|---|---|
| Paths | `/` | Pages to visit, comma or newline separated. Each user picks one at random per request. |
| Users / Ramp | 300 / 10 per s | Peak concurrent users and how fast they arrive. The Smoke 50, 1,000 and 3,000 presets fill both. |
| Duration | 5m | `90s`, `5m`, `1h30m`. Empty runs until you press Stop. |
| Delay min / max | 3 / 5 s | Pause each user takes between requests. |
| Retries | 3 | Retries on 429, 5xx or network errors. 0 turns retries off. |
| Backoff | 1 s | Base for exponential backoff (1 s, 2 s, 4 s, with jitter). A numeric `Retry-After` header wins, capped at 30 s. |

Retried attempts show up in Pushback as `HTTP 429, retry 1/3`. The Errors readout only counts requests that still failed after every retry, and Throttled counts every 429, so a rate-limited host and a broken page never blur into one number. Once the first 429 lands, the chart marks it and Pushback tells you that from then on you're measuring the host's rate limit, not your site.

Keyboard: Cmd+Enter (Ctrl+Enter on Windows) starts a run, Esc stops it. Plain Enter in a field does nothing, so a stray keypress never fires traffic at production.

## Page load under load

The crowd only fetches HTML, so on its own it tells you how fast the server answers, not how fast the page shows up. With "Measure real page load every 15 s" ticked (the default), Swarmscope also opens the page in a real browser with an empty cache: once before the crowd starts, as a baseline, then every 15 seconds while it runs. Each visit records TTFB, first contentful paint, largest contentful paint (LCP), the load event, the bytes and the number of requests, and the panel plots LCP next to the crowd so you can see where the page starts to drag. The verdict compares the two directly, for example "124 ms alone and 4.42 s under load", and calls the run "Slowed down under load" when LCP more than doubles or passes 2.5 s.

The probe drives the Chrome or Edge installed on your machine through [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) (Apache-2.0), an open-source drop-in for Playwright. Windows always has Edge. On a Mac without Chrome, either install Chrome or fetch Patchright's own Chromium once:

```bash
uv run patchright install chromium
```

The probe measures from the same machine that generates the load, so if your CPU is maxed out the numbers include that. Only the first path in Paths is probed.

## Sizing a run

Requests per second land near `users / average delay`; the Load panel shows that estimate and the total for the run before you start. With 3,000 users and a 3 to 5 s delay that's about 750 req/s, or roughly 225K requests in five minutes. Start with 300 users for a minute before going big: a single machine at hundreds of req/s can trip the host's DDoS protection, and then you're measuring the firewall instead of the site.

## Without the desktop app

The panel also runs on plain Locust in a browser:

```bash
uv run locust
```

Open the `Swarmscope panel: http://127.0.0.1:8089/ui?token=...` link that Locust prints when it starts. Locust's stock UI stays at http://127.0.0.1:8089 with the same fields under "Custom parameters". Past about 2,000 users, `uv run locust --processes -1` spreads the load across every core.

## Who can reach the engine

The engine only listens on 127.0.0.1, and every request to it needs a token that changes on each launch. The desktop app passes it to its own window, and `uv run locust` prints it in the link above. The token's link sets a `SameSite=Strict` cookie, so a website open in your browser can't start, stop or redirect a load test from your machine, and a request that arrives under a different hostname is refused.

Headless, same settings:

```bash
uv run locust --headless -H https://yoursite.com -u 300 -r 10 -t 5m --paths "/, /docs"
```

## Lint, format and test

```bash
uv run task lint
```

```bash
uv run task format
```

```bash
uv run task test
```

`uv run ruff check --fix .` fixes what it safely can. The ruff rules live in `pyproject.toml`, so the result is the same on every machine and in CI, which runs the lint, a format check and the tests before building.
