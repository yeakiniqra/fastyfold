# Changelog

## 0.2.0

### Added
- **Roles editor:** `Admin(owners=[...])` turns on roles. Owners can do everything and manage roles at `/admin/_roles`: name, description, people, and a permission matrix (models and pages × view/add/change/delete/export plus each action, with row and column toggles). Grants are loaded once per request, combine with every other permission check, and role changes are audited for owners. The JSON API follows the same grants.
- **Row-level scoping:** `ModelAdmin.get_query(request)` is now the start of every list, count, dashboard figure, ⌘K result, export, autocomplete, record page, bulk action and API call. Foreign-key and many-to-many values are checked against the target's scope on save.
- **Per-record permissions:** `ModelAdmin.has_object_permission(user, perm, obj)`.
- **JSON API:** `Admin(api=True)` adds typed CRUD routes under `/admin/api/{model}`, with Pydantic schemas generated from the ModelAdmin, listed in OpenAPI. `Admin(api_auth=dependency)` plugs in your own FastAPI auth.
- **`admin.current_user`:** a FastAPI dependency for your own routes. `Admin(cookie_path="/")` shares the session with them.
- **Server-side sessions:** stored in `fastyfold_session`. Signing out revokes the cookie, and `await admin.end_sessions(user)` signs someone out everywhere. `Admin(secure_cookies=True)` covers deployments behind a TLS-terminating proxy.
- **Error pages and headers:** HTML error pages for 403, 404, 405 and 422 in browsers, while JSON clients still get JSON. Every admin response carries `X-Frame-Options`, `frame-ancestors`, `Cache-Control: no-store`, `nosniff` and `Referrer-Policy`.
- **Lifecycle hooks:** `after_save(obj, is_new)` and `after_delete(obj)` run after commit. All hooks may be sync or async.
- **Constraint errors:** unique and foreign-key violations re-render the form with the database's reason (422; 409 in the API) instead of a 500. New uploads from the failed save are removed.
- **Template overrides:** `Admin(templates_dir=...)`. Overrides can extend `original/fastyfold/...`, and `admin.render()` renders your own templates for custom pages.
- **New types:** `Uuid` and `Time` columns, and UUID or string primary and foreign keys. `TypeDecorator`s act as the type they wrap. Types fastyfold can't edit safely (`Interval`, `ARRAY`, `LargeBinary`…) are shown read-only instead of being saved as strings.
- **Options:** `column_labels`, `exclude`, `save_as`, `per_page_options`, and "Select all N matching" for bulk actions (capped by `select_all_limit`).
- **Packaging:** `py.typed`; CI on Python 3.10–3.13, Postgres 16, Playwright and a CSS-drift check; ruff; a 95% coverage gate.

### Changed
- `Signer.dump(user, sid)` / `Signer.load()` now carry a session id. Existing sessions are signed out once on upgrade.
- `Admin.render()` takes a full template name. Built-in views use `_render()`.

## 0.1.0

First release.
