# Screenshots of the guide

The images in `aifred/web/static/guide/img/` are taken from a demo instance with invented data, so they can be redone whenever the dashboard changes.

```bash
python scripts/guide-screenshots/serve.py > /tmp/guide-demo.log 2>&1 &          # demo instance on http://127.0.0.1:18090 (fake model, fake MCP server)
PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
  node scripts/guide-screenshots/capture.mjs /tmp/guide-demo.log aifred/web/static/guide/img
kill %1
```

Playwright is a development tool only (it is not a dependency of AIFRED). The demo uses a temporary data directory and never contacts a real service.
