# fastyfold

A modern admin panel for FastAPI. Point it at your SQLAlchemy models and get a fast, keyboard-friendly back office with a board view, a search box over everything (⌘K), an audit log and dark mode. It's one `pip install` away, with no Node build on your side.

> **Status:** early (0.1). The API may still change.

![Dashboard](docs/screenshots/dashboard-light.png)

| Board view | Split view (list + preview) |
|---|---|
| ![Board](docs/screenshots/orders-board-dark.png) | ![Split](docs/screenshots/orders-split-light.png) |
| **⌘K search** | **Forms** |
| ![Search](docs/screenshots/palette-light.png) | ![Form](docs/screenshots/order-form-light.png) |

## Features

- **Three list views:** Table with bulk actions, Board (drag cards between statuses), and Split (list plus a live preview).
- **⌘K search box:** jump to any record, model or action from anywhere.
- **Keyboard first:** `J`/`K` to move, `↵` to open, `X` to select, `G` then a key to jump to a model, `⌘S` to save, `?` for the full list.
- **Forms from your models:** field types, required flags and defaults come from SQLAlchemy. Add a Pydantic model for extra validation.
- **Relations done properly:** search-as-you-type for foreign keys, chips for many-to-many, and one-to-many children edited inline (order → line items).
- **File and image uploads** to a local folder or your own storage (S3, GCS…).
- **CSV export** of the current search and filters, or of the selected rows.
- **Your own screens:** `@admin.page(...)` adds a page to the rail and ⌘K; `@admin.widget(...)` adds a card or chart to the dashboard.
- **A ready-made roles editor:** `Admin(owners=["you"])` adds a Roles screen with a permission matrix (models and pages × view, add, change, delete, export and each action). People get exactly what their roles grant.
- **Per-user and per-tenant data:** one `get_query(request)` override scopes every list, count, export, lookup and record; `has_object_permission` guards single records.
- **A typed JSON API, if you want one:** `Admin(api=True)` adds `/admin/api/{model}` CRUD with generated Pydantic schemas, shown in your app's OpenAPI docs and protected by your own FastAPI auth dependency.
- **Secure by default:** server-side sessions that end on sign-out, CSRF on every write, sign-in rate limiting, clickjacking and no-cache headers, per-model and per-record permissions.
- **Audit log:** every add, change, delete and action is recorded with who did it and what changed (old → new), filterable by user, model, action and date.
- **One colour rebrands everything:** `Admin(accent="#2f6fed")`. Light, dark and system themes.
- **Async SQLAlchemy 2.0**, server-rendered with Jinja2, htmx and Alpine.js. No SPA and no frontend build.

## Install

```bash
pip install fastyfold          # not on PyPI yet: pip install git+https://github.com/yeakiniqra/fastyfold
```

## Quick start

```python
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from fastyfold import Admin, ModelAdmin, action

from .models import Order, Status   # your SQLAlchemy models

engine = create_async_engine("sqlite+aiosqlite:///shop.db")
app = FastAPI()


async def authenticate(username: str, password: str) -> str | None:
    """Return a display name if the credentials are valid, else None."""
    return username if (username, password) == ("admin", "secret") else None


admin = Admin(engine, secret_key="change-me", authenticate=authenticate, title="Acme Store")


@admin.register
class OrderAdmin(ModelAdmin):
    model = Order
    group = "Shop"
    icon = "receipt_long"            # any Material Symbols name
    key = "o"                         # press G then O to jump here
    list_display = ["id", "customer", "status", "total", "created_at"]
    search_fields = ["customer.name", "note"]
    list_filter = ["status", "created_at"]
    board_field = "status"           # enables the Board view

    @action("Mark shipped", icon="local_shipping")
    async def mark_shipped(self, session, orders):
        for order in orders:
            order.status = Status.shipped


admin.mount(app, "/admin")
```

Run it with `uvicorn main:app` and open `/admin`.

## Try the demo

```bash
git clone https://github.com/yeakiniqra/fastyfold && cd fastyfold
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn examples.shop.app:app --reload     # http://127.0.0.1:8000/admin  (admin / admin)
```

## ModelAdmin options

| Option | Default | What it does |
|---|---|---|
| `model` | required | The SQLAlchemy model class |
| `name`, `name_plural`, `slug` | from the class name | Labels and the URL segment |
| `group`, `icon`, `key` | `"Models"`, `"table_rows"`, none | Navigation group, Material Symbols icon, `G` + key shortcut |
| `list_display` | first 6 columns | Columns in lists: columns, `relation.column`, relationship names (`customer`), or the name of a method on your ModelAdmin (`def margin(self, obj)`) |
| `search_fields` | all text columns | Searched by the list and ⌘K; `relation.column` works |
| `list_filter` | choice, bool and date columns | Filters in the filter panel. Also takes foreign keys (`customer_id`, search-as-you-type) and number columns (`total`, a min–max range) |
| `export_fields` | `list_display` | Columns in the CSV export |
| `inlines` | `[]` | One-to-many children edited in the form: `["items"]` or `[("items", ["product_id", "qty"])]` |
| `file_fields` | `{}` | String columns that hold uploads: `{"photo": "image", "manual": "file"}`. Needs `Admin(media_dir=...)` or `storage=` |
| `board_field` | none | A choice/Enum column; turns on the Board view and status counts |
| `form_fields`, `fieldsets` | editable columns, one section | Which fields the form shows and how they're grouped |
| `readonly_fields` | `[]` | Shown, never edited |
| `choices`, `colors` | from Enum | Turn any column into choices; colour per value |
| `schema` | none | Pydantic model to validate form data |
| `ordering`, `per_page`, `per_page_options` | `-pk`, 25, `[25, 50, 100]` | Default sort, page size, and the sizes users may pick |
| `column_labels` | `{}` | Rename columns and fields: `{"total": "Amount"}`; works for computed and dotted columns too |
| `exclude` | `[]` | Fields kept off the form (and so never written) |
| `save_as` | `False` | A "Save as new" button on the edit form |
| `select_all_limit` | 1000 | Most rows a bulk action may touch via "Select all N matching" |
| `get_query(request)` | all rows | The rows this request may see; see [Per-user data](#per-user-data) |
| `has_permission(user, perm)` | allow | `perm` is `view`, `add`, `change`, `delete`, `export` or an action name |
| `has_object_permission(user, perm, obj)` | allow | The same check for one record, before it is shown, changed, deleted, moved or acted on |
| `save(session, obj, is_new)`, `delete(session, obj)` | add / delete | Hooks inside the transaction |
| `after_save(obj, is_new)`, `after_delete(obj)` | nothing | Hooks after commit: notify, clear caches. Sync or async |

## Admin options

```python
Admin(
    engine,                       # AsyncEngine
    secret_key="…",               # signs the session cookie and CSRF tokens
    authenticate=fn,              # async (username, password) -> display name | None
    title="Acme Store",
    accent="#ff6a2b",             # one colour themes everything
    logo_icon="layers",
    permission=fn,                # optional global (user, perm, model_admin) -> bool
    groups={"Shop": "shopping_bag"},  # rail icon per group
    site_url="/",                 # "View site" link
    login_attempts=10,            # failed sign-ins per IP per 15 minutes; 0 turns the limit off
    media_dir="uploads",          # where file_fields are stored (served to signed-in users at /admin/_media/…)
    storage=None,                 # or your own storage instead of media_dir, see below
    max_upload=10 * 1024 * 1024,  # bytes per file
    secure_cookies=None,          # None follows the request scheme; True behind a TLS-terminating proxy
    cookie_path=None,             # defaults to the mount path; "/" lets admin.current_user work on your other routes
    templates_dir="templates",    # your templates win; extend the originals with "original/fastyfold/<name>.html"
    api=False,                    # JSON API at /admin/api/{model}
    api_auth=None,                # a FastAPI dependency returning the user, for API clients (bearer token, API key)
    owners=["nusrat"],            # turns on roles: owners can do everything and manage roles; everyone else gets what their roles grant
)
```

`fastyfold.security` has `hash_password` and `verify_password` (PBKDF2) if you need to store admin passwords.

Behind a proxy, run uvicorn with `--proxy-headers` so the sign-in limit counts visitors, not the proxy.

### Roles

```python
admin = Admin(engine, ..., owners=["nusrat"])
```

Owners see a **Roles** entry in the rail and ⌘K. A role has a name, its people (one username per line) and a permission matrix: every model and page × view / add / change / delete / export, plus each `@action`. Click a model to tick its row, or a column heading to tick it everywhere. Someone in several roles gets all their permissions combined, and someone in no role sees nothing.

Roles stack with your code: `has_permission`, `has_object_permission`, `get_query` and `Admin(permission=...)` still apply, and every one of them has to allow. Roles are stored in `fastyfold_role`, `fastyfold_role_grant` and `fastyfold_role_member`, created on first use, and role changes appear in the audit log for owners. A user is whatever your `authenticate` returns, so use the same names in a role's people list.

### Per-user data

Override `get_query` and every way into that model (list, counts, dashboard, ⌘K, export, autocomplete, record pages, bulk actions, the API) is limited to what it returns. Rows outside it are a 404, and can't be picked in another model's foreign-key field either.

```python
class OrderAdmin(ModelAdmin):
    model = Order

    def get_query(self, request):
        user = request.state.admin_user
        query = super().get_query(request)
        return query if user == "owner" else query.where(Order.shop_id.in_(select(Shop.id).where(Shop.manager == user)))

    def has_object_permission(self, user, perm, obj):
        return perm == "view" or obj.status != "closed"     # closed orders are read-only
```

### Sessions and your own routes

Sessions live in a `fastyfold_session` table (created on first use, next to the audit log). Signing out deletes the row, so a copied cookie stops working at once; `await admin.end_sessions("nusrat")` signs someone out everywhere, e.g. after a password change.

Use the admin's sign-in on your own routes with `Depends(admin.current_user)` (set `cookie_path="/"` if they live outside the admin path).

### JSON API

```python
async def api_key(x_api_key: str = Header()) -> str:
    if x_api_key != settings.admin_api_key:
        raise HTTPException(401)
    return "automation"

admin = Admin(engine, ..., api_auth=api_key)     # or api=True to use the browser session (+ X-CSRF-Token on writes)
```

Each model gets `GET /admin/api/{model}` (with `q`, `sort`, `page`, `per_page` and the list's `f_<field>` filters), `GET/PATCH/DELETE /admin/api/{model}/{pk}` and `POST /admin/api/{model}`. Bodies are validated by Pydantic models generated from the ModelAdmin, then by its `schema`; writes are audited and run the same hooks as the forms. Constraint errors come back as 409.

### Custom pages and dashboard widgets

```python
from markupsafe import escape
from sqlalchemy import func, select

@admin.page("Reports", icon="bar_chart", group="Insights", key="r")
async def reports(request, user):
    # Return HTML (shown inside the admin layout) or any Response. Escape untrusted values yourself.
    return f"<p>Hello {escape(user)}</p>"

@admin.widget("Revenue", icon="payments", span=4)     # span: 3, 4, 6, 8 or 12 of 12 columns
async def revenue(session, user):
    total = await session.scalar(select(func.sum(Order.total)))
    return {"value": total, "caption": "all time"}     # or a number, text, [(label, n), ...] for bars, or None to hide
```

Pages take part in navigation, ⌘K, `G`+key and permissions like models do. A page receives POSTs too; forms on it need `<input type="hidden" name="_csrf" value="{admin.csrf(request)}">`.

### Your own file storage

`media_dir` keeps uploads on disk. For anything else pass `storage=` an object with:

```python
async def save(self, name: str, data: bytes, content_type: str | None) -> str: ...  # returns the key stored in the column
async def delete(self, key: str) -> None: ...
def url(self, key: str) -> str: ...      # optional; without it the admin serves the file itself
```

Images must be real PNG, JPEG, GIF or WebP files (SVG is refused because it can carry script). Other files are always served as downloads.

## Development

```bash
pip install -e ".[dev]" && playwright install chromium && pytest
FASTYFOLD_TEST_DB=postgresql+asyncpg://user:pass@localhost/db pytest --ignore=tests/test_browser.py   # against Postgres
# only when changing styles; the built CSS is committed
TAILWINDCSS_VERSION=v4.1.11 tailwindcss -i styles/fastyfold.css -o src/fastyfold/static/fastyfold.css --minify
```

See [CLAUDE.md](CLAUDE.md) for conventions and [AGENTS.md](AGENTS.md) for how everything fits together.

## License

[Apache License 2.0](LICENSE)
