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
- **Built-in sign-in with rate limiting, CSRF protection and per-model permissions.**
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
| `ordering`, `per_page` | `-pk`, 25 | Default sort and page size |
| `has_permission(user, perm)` | allow | `perm` is `view`, `add`, `change`, `delete`, `export` or an action name |
| `save(session, obj, is_new)`, `delete(session, obj)` | add / delete | Hooks around writes |

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
)
```

`fastyfold.security` has `hash_password` and `verify_password` (PBKDF2) if you need to store admin passwords.

Behind a proxy, run uvicorn with `--proxy-headers` so the sign-in limit counts visitors, not the proxy.

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
# only when changing styles; the built CSS is committed
TAILWINDCSS_VERSION=v4.1.11 tailwindcss -i styles/fastyfold.css -o src/fastyfold/static/fastyfold.css --minify
```

See [CLAUDE.md](CLAUDE.md) for conventions and [AGENTS.md](AGENTS.md) for how everything fits together.

## License

[Apache License 2.0](LICENSE)
