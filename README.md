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
- **Built-in sign-in, CSRF protection and per-model permissions.**
- **Audit log:** every add, change, delete and action is recorded with who did it and what changed.
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
| `list_display` | first 6 columns | Columns in lists; relationship names (`customer`) are allowed |
| `search_fields` | all text columns | Searched by the list and ⌘K; `relation.column` works |
| `list_filter` | choice, bool and date columns | Filters in the filter panel |
| `board_field` | none | A choice/Enum column; turns on the Board view and status counts |
| `form_fields`, `fieldsets` | editable columns, one section | Which fields the form shows and how they're grouped |
| `readonly_fields` | `[]` | Shown, never edited |
| `choices`, `colors` | from Enum | Turn any column into choices; colour per value |
| `schema` | none | Pydantic model to validate form data |
| `ordering`, `per_page` | `-pk`, 25 | Default sort and page size |
| `has_permission(user, perm)` | allow | `perm` is `view`, `add`, `change`, `delete` or an action name |
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
)
```

`fastyfold.security` has `hash_password` and `verify_password` (PBKDF2) if you need to store admin passwords.

## Development

```bash
pip install -e ".[dev]" && playwright install chromium && pytest
# only when changing styles; the built CSS is committed
TAILWINDCSS_VERSION=v4.1.11 tailwindcss -i styles/fastyfold.css -o src/fastyfold/static/fastyfold.css --minify
```

See [CLAUDE.md](CLAUDE.md) for conventions and [AGENTS.md](AGENTS.md) for how everything fits together.

## License

[Apache License 2.0](LICENSE)
