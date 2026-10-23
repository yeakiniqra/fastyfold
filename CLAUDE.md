# fastyfold

A modern admin panel for FastAPI with its own visual identity (design "B · Rail"): dark icon rail, floating panels, warm canvas, one accent colour. Open source, Apache-2.0.

How it works end to end (request flow, model introspection, views, auth, audit, search, frontend): @AGENTS.md

## Commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # package + uvicorn, aiosqlite, pytest, pytailwindcss, pytest-playwright
playwright install chromium       # once, for tests/test_browser.py
pytest                            # all tests (add --headed to watch the browser ones)
uvicorn examples.shop.app:app --reload   # demo admin at http://127.0.0.1:8000/admin  (admin / admin)

# rebuild src/fastyfold/static/fastyfold.css (add --watch to rebuild on template changes)
TAILWINDCSS_VERSION=v4.1.11 tailwindcss -i styles/fastyfold.css -o src/fastyfold/static/fastyfold.css --minify
```

The built `fastyfold.css` is committed. `tailwindcss` is the standalone binary fetched by `pytailwindcss`; no Node anywhere.

## Layout

| Path | Role |
|---|---|
| `src/fastyfold/admin.py` | `Admin`: registry, router, auth/session, every HTTP view |
| `src/fastyfold/model.py` | `ModelAdmin`: per-model options users subclass |
| `src/fastyfold/fields.py` | SQLAlchemy column → `Field` (widget, parse, display) |
| `src/fastyfold/audit.py` | `LogEntry` table and `record()` |
| `src/fastyfold/security.py` | Signed session cookie, CSRF token, password hashing helpers |
| `src/fastyfold/storage.py` | Upload checks and `LocalStorage`; the storage protocol for `Admin(storage=...)` |
| `src/fastyfold/templates/fastyfold/` | Jinja2 templates (layout, pages, partials, macros) |
| `src/fastyfold/static/` | Built CSS, `fastyfold.js`, vendored htmx + Alpine |
| `styles/fastyfold.css` | Tailwind v4 source (theme tokens, `@utility` components) |
| `examples/shop/` | Runnable demo app with seed data |
| `design/` | Static HTML mockups. The visual source of truth for new screens |
| `tests/` | pytest suite, one file per feature; `server/` is the test app, `fixtures.py` + `factories.py` seed it |

## Conventions

- Visual rules come from `design/mockup.html`. New UI copies its patterns (`float` panels, `pill` buttons, `field` inputs, `kbd` hints) instead of inventing new ones.
- Colours only through theme tokens (`bg-accent`, `text-muted`, `bg-panel`…). Never hard-code the accent; users rebrand with `Admin(accent=...)`.
- Every user-visible action gets a keyboard shortcut and appears in the ⌘K palette when it makes sense.
- Server renders HTML. JavaScript stays small: Alpine for local UI state, htmx for partial swaps and boosted navigation. No SPA framework, no build step for users.
- Async SQLAlchemy only (`AsyncEngine`). Single-column primary keys only.
- Public API is what `fastyfold/__init__.py` exports. Keep it small: `Admin`, `ModelAdmin`, `action`.
- Every POST checks the CSRF token and the permission hook. Don't add a write endpoint without both.
- Deliberate shortcuts are marked `# ponytail:` with the limit and upgrade path. `grep -rn "ponytail:" src` lists the known ceilings.
- Tests: add one to the matching `tests/test_<feature>.py` for any new view or rule. Each test gets a fresh in-memory SQLite site.
  - Fixtures: `admin_client` / `editor_client` (signed in), `submit(url, data)` (POST with CSRF), `db.add(factories.post(...))`, `db.get`, `db.log()`.
  - Change the site under test with `@pytest.mark.parametrize("admin_options", [{...}])`.
  - New column kind or model shape: extend `tests/server/models.py` rather than adding a second test app.
  - Anything that only happens in JavaScript (keys, palette, drag and drop, Alpine state) gets a Playwright test in `tests/test_browser.py`. Its `live` fixture serves the same test app with uvicorn; wait for responses (`page.expect_response`) instead of polling `live.db`, which shares the server's one SQLite connection.

## Git

- Push to `git@github-personal:yeakiniqra/fastyfold.git` as yeakiniqra.
- Commit with `-c user.name=yeakiniqra -c user.email=108855634+yeakiniqra@users.noreply.github.com`. No AI attribution lines.
