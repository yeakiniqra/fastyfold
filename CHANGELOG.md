# Changelog

All notable changes to fastyfold. The newest release is at the top.

## 0.2.0

The release that makes fastyfold ready for real teams. We compared it against sqladmin, starlette-admin, fastadmin and fastapi-amis-admin, then closed the gaps that mattered while keeping what it already did better: CSRF everywhere, an audit log, the board, split and ⌘K views, and no frontend build.

### Added

**Access control**
- **Roles editor.** `Admin(owners=[...])` adds a Roles screen at `/admin/_roles`. Each role has a name, a description, its people, and a permission matrix: models and pages × view, add, change, delete, export, plus every `@action`. Clicking a model ticks its row; clicking a column heading ticks it everywhere. Owners can do everything; everyone else gets what their roles grant.
- **Row-level scoping.** Every list, count, dashboard figure, ⌘K result, export, autocomplete, record page, bulk action and API call starts from `ModelAdmin.get_query(request)`. Foreign-key and many-to-many values are checked against the target's scope on save.
- **Per-record permissions.** `ModelAdmin.has_object_permission(user, perm, obj)`.
- **New `export` permission.** Also a sign-in rate limit per IP: `Admin(login_attempts=10)`, after which sign-in returns 429.

**FastAPI integration**
- **JSON API.** `Admin(api=True)` adds typed CRUD routes at `/admin/api/{model}`, with Pydantic schemas generated from each ModelAdmin and listed in your OpenAPI docs. `Admin(api_auth=dependency)` plugs in your own FastAPI auth (bearer token, API key).
- **`admin.current_user`.** A FastAPI dependency for your own routes. `Admin(cookie_path="/")` shares the admin session with them.
- **Server-side sessions** in a `fastyfold_session` table. Signing out revokes the cookie at once, and `await admin.end_sessions(user)` signs someone out everywhere. `Admin(secure_cookies=True)` is for apps behind a TLS-terminating proxy.
- **HTML error pages in browsers.** 403, 404, 405 and 422 render as admin pages while JSON clients keep getting JSON. This comes from a custom `APIRoute`, so your app's own exception handlers are untouched.
- **Template overrides.** `Admin(templates_dir=...)` puts your templates first, an override can extend `original/fastyfold/<name>.html`, and `admin.render()` renders your own templates.
- **Lifecycle hooks.** `after_save(obj, is_new)` and `after_delete(obj)` run after commit. Every hook may be sync or async.

**Lists and forms**
- **Search-as-you-type for foreign keys** via `/{model}/_lookup/{field}`, replacing the select that stopped at 200 rows.
- **Many-to-many fields** as autocomplete chips.
- **Inline editing of one-to-many children** (`ModelAdmin.inlines`): add, change and remove rows inside the parent's form.
- **File and image uploads** (`ModelAdmin.file_fields`) to `Admin(media_dir=...)` or any storage you plug in. Images are checked by their real file signature, and other files are always served as downloads.
- **CSV export** of the current search, filters and sort, or of the selected rows.
- **Filters on foreign keys and number ranges** (`f_views=10..100`).
- **Computed columns.** `list_display` may name a method on your ModelAdmin.
- **Record actions on the edit page**, with their key shortcuts.
- **Bulk actions on every matching row.** "Select all N matching" is capped by `select_all_limit`.
- **More options:** `column_labels`, `exclude`, `save_as`, `per_page_options`.
- **Column types:** `Uuid` and `Time`, and UUID or string primary and foreign keys. A `TypeDecorator` is treated as the type it wraps.

**Dashboard, pages and log**
- `@admin.page(...)` adds a custom screen to the rail, ⌘K and `G`+key, under the same permissions as models.
- `@admin.widget(...)` adds a dashboard card: a number, a stat with a caption, or a bar list.
- The audit log can be filtered by user, model, action and date, and shows every change as old → new.

**Project**
- Ships `py.typed`.
- CI on Python 3.10–3.13 and Postgres 16, Playwright browser tests, ruff, a 95% coverage gate and a check that the built CSS is current.
- Releases publish to PyPI and GitHub from a version tag.

### Fixed
- **Security:** signing out now invalidates the session. Before, a copied cookie kept working until it expired.
- **Security:** the dashboard and audit log no longer show changes to models the viewer may not see.
- **Security:** admin pages can no longer be framed (clickjacking), and responses are marked `no-store` so record data isn't cached by the browser.
- **Database constraint errors** (duplicate unique values, rows still referenced) now show as a form error or list message instead of a 500. Uploads from the failed save are removed.
- **UUID and string foreign keys** no longer crash: they were parsed as integers.
- **`Interval`, `ARRAY`, `LargeBinary` and other types** fastyfold can't edit safely are now read-only. Before, they were saved back as strings.
- **Missing foreign-key values:** a foreign-key value pointing at a missing row is now a form error instead of reaching the database.
- **Bad query parameters** (`?page=abc`) show an error page instead of raw JSON.
- **Autocomplete keyboard:** Esc closes the menu without leaving the page, and keys pressed before the search box takes focus still work.
- **Inline editing performance:** inline editing no longer reloads each child's link back to its parent.

### Changed
- **Existing sessions end once on upgrade.** `Signer.dump(user, sid)` and `Signer.load()` now carry a session id, so everyone signs in again after upgrading.
- **`Admin.render()` takes a full template name** (`"fastyfold/list.html"` or your own). Built-in views use `_render()`.
- **SQLite tests enforce foreign keys**, as Postgres does.

## 0.1.0

The first release.

- **Lists:** table, board (drag cards between statuses) and split (list plus a live preview) views, with search, filters, sorting, paging and bulk actions.
- **Forms** built from SQLAlchemy columns, with field types, required flags, defaults, fieldsets and optional Pydantic validation.
- **⌘K palette and keyboard shortcuts:** `J`/`K`, `X`, `G` plus a key, `⌘S` and `?`.
- **Security:** sign-in with a signed session cookie, HMAC CSRF on every write, and per-model and global permission hooks.
- **Audit log** of every add, change, delete and action.
- **Theming:** one accent colour rebrands everything; light, dark and system themes.
- **Tooling:** CSS built with the standalone Tailwind CLI, so there is no Node anywhere.
