# fastyfold

A modern admin panel for FastAPI. Point it at your SQLAlchemy models and you get a fast, keyboard-friendly back office:

- table, board and split views
- a search box over everything (⌘K)
- roles and per-record permissions
- an audit log and dark mode
- an optional typed JSON API

It installs with `pip`, and there is no Node build on your side.

> **Status:** 0.3, beta. Tested on Python 3.10–3.13 with SQLite and PostgreSQL 16. The API may still change before 1.0; see the [changelog](CHANGELOG.md).

![Dashboard](docs/screenshots/dashboard-light.png)

| Board view | Split view (list + preview) |
|---|---|
| ![Board](docs/screenshots/orders-board-dark.png) | ![Split](docs/screenshots/orders-split-light.png) |
| **⌘K search** | **Forms** |
| ![Search](docs/screenshots/palette-light.png) | ![Form](docs/screenshots/order-form-light.png) |

## Features

**Working with records**
- **Three list views:** a table with bulk actions, a board where you drag cards between statuses, and a split view with a live preview.
- **Search, filters, sorting and paging.** You can filter on choices, yes/no, dates (today, last 7 or 30 days), foreign keys (search as you type) and number ranges. Every active filter shows as a removable chip, and users pick their own page size.
- **Bulk and single-record actions.** Write one async method with `@action` and it runs on the selected rows, on every row matching the current filters ("Select all N matching"), or on one record from its page. Each action has its own key shortcut and can ask for confirmation.
- **CSV export** of what's on screen (current search, filters and sort) or of the selected rows. Cells that look like spreadsheet formulas are neutralised.
- **Computed columns.** Name a method on your ModelAdmin in `list_display`.

**Forms**
- **Built from your models:** field types, required flags and defaults come from SQLAlchemy. Add a Pydantic model for extra validation, and group fields into sections.
- **Relations:**
  - foreign keys get search-as-you-type
  - many-to-many fields get chips
  - one-to-many children are edited inline (order → line items)
- **File and image uploads** to a local folder or your own storage (S3, GCS…). Images are checked by their real file signature.
- **Database constraint errors** (duplicate email, a row still in use) show as a message on the form, not as a 500 error.
- **"Save as new", "Save & add another" and "Save & keep editing".**

**People and permissions**
- **Users built in:** no login code to write. The first visit creates the owner account (protected by a one-time code in your terminal), and owners add teammates, reset passwords and assign roles on a Users screen. Everyone can change their own password.
- **Ready-made roles editor:** a permission matrix of models and pages × view, add, change, delete, export and each action.
- **Per-user and per-tenant data:** override one method, `get_query(request)`, to scope every list, count, export, lookup, record and API call.
- **Per-record rules:** `has_object_permission(user, perm, obj)`, e.g. closed orders are read-only.
- **Your own rules on top:** `has_permission` per model, or one global `permission` hook.

**Built for FastAPI**
- **Optional JSON API:** `/admin/api/{model}` CRUD with Pydantic schemas generated from your models. It appears in your app's OpenAPI docs and is protected by any FastAPI auth dependency you pass in.
- **`Depends(admin.current_user)`** reuses the admin's sign-in on your own routes.
- **Your own screens and dashboard cards:** `@admin.page` and `@admin.widget`.
- **Override any template** from your own folder, or extend the original.
- **Hooks** around every write: `save` and `delete` run inside the transaction, `after_save` and `after_delete` after commit. Each may be sync or async.

**Secure by default**
- **Sessions:**
  - stored server-side, so signing out kills the cookie at once
  - "sign this person out everywhere" in one call
- **Every write** needs a CSRF token and a permission check.
- **Sign-in** is rate-limited per IP.
- **Every admin response** sends anti-clickjacking, no-cache, no-sniff and referrer headers.
- **Uploads** are type-checked, and anything that isn't an image is served as a sandboxed download.
- **Audit log:** every add, change, delete and action is recorded with who did it and what changed (old → new). You can filter it by user, model, action and date, and it only shows models the viewer may see.

**Fast to use**
- **⌘K palette:** jump to any record, model, page or action.
- **Keyboard first:** `J`/`K` to move, `↵` to open, `X` to select, `G` then a key to jump, `⌘S` to save, `?` for the full list. See [Keyboard shortcuts](#keyboard-shortcuts).
- **One colour rebrands everything:** `Admin(accent="#2f6fed")`. Light, dark and system themes.
- **No frontend build:** server-rendered with Jinja2, htmx and Alpine.js on async SQLAlchemy 2.0.

## Install

```bash
pip install "fastyfold @ git+https://github.com/yeakiniqra/fastyfold@v0.3.0"
```

fastyfold reads **SQLAlchemy 2.0 models on an async engine**. [SQLModel](https://sqlmodel.tiangolo.com) models work too, since they are SQLAlchemy models. Using another ORM, raw SQL or no database? See [Not using SQLAlchemy?](#not-using-sqlalchemy)

## Quick start

Add three lines where you create your FastAPI app:

```python
from fastyfold import Admin

from .db import engine                      # your SQLAlchemy AsyncEngine
from .models import Customer, Order         # your models

admin = Admin(engine)
admin.register(Order, Customer)
admin.mount(app)                            # app = your FastAPI(); the admin lives at /admin
```

Start your app and open http://127.0.0.1:8000/admin. The first visit asks for a **setup code** that fastyfold prints in your terminal, then you create your account. That's it: you are the owner, and you can add teammates on the **Users** screen.

There's no secret key or login code to write. fastyfold stores its own signing key and users in your database (tables starting with `fastyfold_`).

## Customize a model

Register a `ModelAdmin` subclass instead of the bare model when you want options:

```python
from fastyfold import ModelAdmin, action


@admin.register
class OrderAdmin(ModelAdmin):
    model = Order
    list_display = ["id", "customer", "status", "total", "created_at"]
    search_fields = ["customer.name", "note"]
    list_filter = ["status", "created_at", "customer_id", "total"]
    board_field = "status"                  # adds the Board view
    key = "o"                               # press G then O to jump here

    @action("Mark shipped", icon="local_shipping", key="s")
    async def mark_shipped(self, session, orders):
        for order in orders:
            order.status = "shipped"
```

Every option is listed under [ModelAdmin options](#modeladmin-options).

## Use your own login

Built-in users are the default. To sign in against your own user table instead, pass `authenticate` (and, if you like, your own `secret_key`):

```python
async def authenticate(username: str, password: str) -> str | None:
    user = await get_user(username)                       # your code
    return user.name if user and user.check_password(password) else None

admin = Admin(engine, authenticate=authenticate, owners=["nusrat"])   # owners: full access; others get roles
```

## Not using SQLAlchemy?

fastyfold builds its forms, filters, search and API from SQLAlchemy models, and keeps its own tables (users, sessions, audit log) in a SQL database. What that means for your project:

| Your project uses | Works? |
|---|---|
| SQLAlchemy 2.0 (async) | Yes |
| SQLModel | Yes. Pass an async engine: `create_async_engine("postgresql+asyncpg://...")` |
| Tortoise ORM, Piccolo, Peewee, raw SQL (asyncpg, databases) on PostgreSQL, MySQL or SQLite | Yes, with the workaround below |
| MongoDB (Beanie, Motor), Redis or another non-SQL store | No |
| No database at all | Custom pages and widgets only, see below |

**Workaround: describe your existing tables for the admin.** Your tables are already there, so write a small SQLAlchemy class for each table you want in the admin. Your app keeps using its own ORM; these classes are only for fastyfold.

```python
# admin_models.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "order"                 # the existing table's name
    id: Mapped[int] = mapped_column(primary_key=True)
    customer: Mapped[str]
    total: Mapped[float]
    status: Mapped[str]
```

```python
# where you create your FastAPI app
from fastyfold import Admin
from .admin_models import Order

admin = Admin("postgresql+asyncpg://user:pass@localhost/shop")    # the same database your app uses
admin.register(Order)
admin.mount(app)
```

- Use the async driver for your database: `postgresql+asyncpg://`, `mysql+aiomysql://` or `sqlite+aiosqlite:///`, installed with `pip install asyncpg` (or `aiomysql`, `aiosqlite`).
- List only the columns you want to see or edit. Edits leave the other columns alone. To create records, include every `NOT NULL` column that has no database default.
- Every table needs a single-column primary key.
- Don't call `Base.metadata.create_all()`. The tables already exist. fastyfold creates only its own `fastyfold_*` tables.
- Edits made in the admin go straight to the database, so your ORM's own signals and save hooks don't run. Put that logic in the ModelAdmin's `save` or `after_save` instead (see [ModelAdmin options](#modeladmin-options)).

**No database at all.** fastyfold still needs somewhere to keep its users and sessions. Give it a SQLite file and use [custom pages and dashboard widgets](#custom-pages-and-dashboard-widgets) for your screens:

```python
# pip install aiosqlite
admin = Admin("sqlite+aiosqlite:///admin.db")

@admin.page("Reports", icon="bar_chart")
async def reports(request, user):
    return "<p>...</p>"

admin.mount(app)
```

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
| `list_display` | first 6 columns | Columns in lists: column names, `relation.column`, relationship names (`customer`), or a method on your ModelAdmin (`def margin(self, obj)`) |
| `column_labels` | `{}` | Rename columns and fields: `{"total": "Amount"}`; works for computed and dotted columns too |
| `search_fields` | all text columns | Searched by the list, ⌘K and autocomplete; `relation.column` works |
| `list_filter` | choice, bool and date columns | Filter panel. Also takes foreign keys (`customer_id`) and number columns (`total`, a min–max range) |
| `ordering` | `-pk` | Default sort, e.g. `"-created_at"` |
| `per_page`, `per_page_options` | 25, `[25, 50, 100]` | Page size and the sizes users may pick |
| `board_field` | none | A choice/Enum column; turns on the Board view and the status counts |
| `colors`, `choices` | from the Enum | Colour per value; turn any column into choices |
| `preview_fields` | `list_display` | What the split view's preview shows |
| `export_fields` | `list_display` | Columns in the CSV export |
| `form_fields`, `exclude`, `fieldsets` | editable columns, none, one section | Which fields the form shows, which it never writes, and how they're grouped: `[("Shipping", ["address", "city"])]` |
| `readonly_fields` | `[]` | Shown, never edited |
| `inlines` | `[]` | One-to-many children edited in the form: `["items"]` or `[("items", ["product_id", "qty"])]` |
| `file_fields` | `{}` | String columns that hold uploads: `{"photo": "image", "manual": "file"}` |
| `schema` | none | Pydantic model run after field parsing (forms and API) |
| `save_as` | `False` | A "Save as new" button on the edit form |
| `select_all_limit` | 1000 | Most rows a bulk action may touch through "Select all N matching" |

Methods you can override:

| Method | Default | What it's for |
|---|---|---|
| `get_query(request)` | all rows | The rows this request may see; see [Per-user data](#per-user-data) |
| `has_permission(user, perm)` | allow | `perm` is `view`, `add`, `change`, `delete`, `export` or an action name |
| `has_object_permission(user, perm, obj)` | allow | The same check for one record, before it's shown, changed, deleted, moved or acted on |
| `save(session, obj, is_new)`, `delete(session, obj)` | add / delete | Inside the transaction, so extra writes commit together |
| `after_save(obj, is_new)`, `after_delete(obj)` | nothing | After commit: notify someone, clear a cache |
| `label(obj)` | `str(obj)` | How a record is named across the admin |

## Admin options

```python
Admin(
    engine,                       # AsyncEngine, or a database URL like "sqlite+aiosqlite:///app.db"
    title="Acme Store",
    authenticate=None,            # None: built-in users with a setup page; or async (username, password) -> name | None
    secret_key=None,              # None: generated once and stored in the database
    accent="#ff6a2b",             # one colour themes everything
    logo_icon="layers",
    groups={"Shop": "shopping_bag"},  # rail icon per group
    site_url="/",                 # "View site" link
    permission=fn,                # optional global (user, perm, model_admin) -> bool
    owners=["nusrat"],            # with your own authenticate: these users can do everything; everyone else gets roles
    login_attempts=10,            # failed sign-ins per IP per 15 minutes; 0 turns the limit off
    secure_cookies=None,          # None follows the request scheme; True behind a TLS-terminating proxy
    cookie_path=None,             # defaults to the mount path; "/" lets admin.current_user work on your other routes
    media_dir="uploads",          # where file_fields are stored (served to signed-in users at /admin/_media/…)
    storage=None,                 # or your own storage instead of media_dir
    max_upload=10 * 1024 * 1024,  # bytes per file
    templates_dir="templates",    # your templates win; extend the originals with "original/fastyfold/<name>.html"
    api=False,                    # JSON API at /admin/api/{model}
    api_auth=None,                # a FastAPI dependency returning the user, for API clients
)
```

`fastyfold.security` has `hash_password` and `verify_password` (PBKDF2) if you need to store admin passwords.

## Field types

| SQLAlchemy column | Form widget | Notes |
|---|---|---|
| `Integer`, `Numeric`, `Float` | number | |
| `String` (≤255), `String` (>255), `Text` | text, textarea | |
| `Boolean` | toggle | |
| `Date`, `Time`, `DateTime` | date, time, date-time pickers | |
| `Uuid` | text | UUID and string primary keys work everywhere |
| `JSON` | code textarea | |
| `Enum`, or `ModelAdmin.choices` | pill buttons (≤5 options) or a select | |
| `ForeignKey` | search-as-you-type | Only rows inside the target's `get_query` can be picked |
| many-to-many relationship | chips with search | |
| one-to-many relationship | an inline table | Via `inlines` |
| `String` in `file_fields` | file or image upload | With preview and a Remove box |
| `Interval`, `ARRAY`, `LargeBinary`, others | shown read-only | Never written back |

A `TypeDecorator` behaves like the type it wraps.

## Roles

With built-in users, roles are always on, and owners are the users marked **Owner** on the Users screen. With your own `authenticate`, list the owners instead:

```python
admin = Admin(engine, authenticate=authenticate, owners=["nusrat"])
```

Owners see a **Roles** entry in the rail and ⌘K. A role has a name, its people (one username per line) and a permission matrix:

- **Rows:** every model and page.
- **Columns:** view, add, change, delete and export, plus each `@action`.
- **Shortcuts:** click a model to tick its row, or a column heading to tick it everywhere.
- **Combining:** someone in several roles gets all their permissions together; someone in no role sees nothing.

Roles stack with your code. `has_permission`, `has_object_permission`, `get_query` and `Admin(permission=...)` all still apply, and every one of them has to allow.

Roles are stored in `fastyfold_role`, `fastyfold_role_grant` and `fastyfold_role_member`, which are created on first use. Role changes appear in the audit log, for owners only. A user is whatever your `authenticate` returns, so use the same names in a role's people list.

## Per-user data

Override `get_query` and every way into that model is limited to the rows it returns: the list, counts, dashboard, ⌘K, export, autocomplete, record pages, bulk actions and the API. Rows outside it are a 404, and they can't be picked in another model's foreign-key field either.

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

## Sessions and your own routes

Sessions live in a `fastyfold_session` table, created on first use next to the audit log.

- **Signing out** deletes the row, so a copied cookie stops working at once.
- **Signing someone out everywhere:** `await admin.end_sessions("nusrat")`, for example after a password change.
- **Your own routes:** use the admin's sign-in with `Depends(admin.current_user)`. Set `cookie_path="/"` if those routes live outside the admin path.
- **Behind a proxy:** run uvicorn with `--proxy-headers` so the sign-in limit counts visitors, not the proxy, and set `secure_cookies=True` if TLS ends at the proxy.

## JSON API

```python
async def api_key(x_api_key: str = Header()) -> str:
    if x_api_key != settings.admin_api_key:
        raise HTTPException(401)
    return "automation"

admin = Admin(engine, ..., api_auth=api_key)     # or api=True to use the browser session (+ X-CSRF-Token on writes)
```

Each model gets these routes:
- `GET /admin/api/{model}`, which takes `q`, `sort`, `page`, `per_page` and the list's `f_<field>` filters
- `POST /admin/api/{model}`
- `GET`, `PATCH` and `DELETE /admin/api/{model}/{pk}`

Request bodies are validated by Pydantic models generated from the ModelAdmin, then by its `schema`. PATCH is partial. Writes are audited and run the same hooks as the forms, and constraint errors come back as 409. Everything is listed in your app's `/docs`.

## Custom pages and dashboard widgets

```python
from markupsafe import escape
from sqlalchemy import func, select

@admin.page("Reports", icon="bar_chart", group="Insights", key="r")
async def reports(request, user):
    # Return HTML (shown inside the admin layout), a Response, or admin.render(request, "my/report.html", ...)
    return f"<p>Hello {escape(user)}</p>"

@admin.widget("Revenue", icon="payments", span=4)     # span: 3, 4, 6, 8 or 12 of 12 columns
async def revenue(session, user):
    total = await session.scalar(select(func.sum(Order.total)))
    return {"value": total, "caption": "all time"}     # or a number, text, [(label, n), ...] for bars, or None to hide
```

Pages take part in navigation, ⌘K, `G`+key, permissions and roles the same way models do. A page also receives POSTs; forms on it need `<input type="hidden" name="_csrf" value="{admin.csrf(request)}">`. Escape any untrusted values in the HTML you return.

## Templates

```python
admin = Admin(engine, ..., templates_dir="templates")
```

- **Replacing a built-in template:** `templates/fastyfold/dashboard.html` replaces the built-in dashboard.
- **Extending it instead:** start the file with `{% extends "original/fastyfold/dashboard.html" %}`, then override only the blocks you need.
- **Your own templates:** render them with `admin.render(request, "my/page.html", **context)`. They can extend `fastyfold/layout.html` to get the rail, search and theme.

## File storage

`media_dir` keeps uploads on disk. For anything else, pass `storage=` an object with:

```python
async def save(self, name: str, data: bytes, content_type: str | None) -> str: ...  # returns the key stored in the column
async def delete(self, key: str) -> None: ...
def url(self, key: str) -> str: ...      # optional; without it the admin serves the file itself
```

Images must be real PNG, JPEG, GIF or WebP files; SVG is refused because it can carry script. Other files are always served as downloads. Replaced, removed and deleted records' files are cleaned up after commit.

## Keyboard shortcuts

| Where | Keys |
|---|---|
| Everywhere | `⌘K` / `Ctrl+K` / `/` search, `?` all shortcuts, `G` then a model's key to jump (`G D` dashboard), `T` dark mode, `Esc` close or back |
| Lists | `J`/`K` move, `↵`/`E` open, `X` select, `N` new, `F` filters, `1`/`2`/`3` table/board/split, an action's own key to run it |
| Forms | `⌘S` save, `⌘↵` save and keep editing, an action's own key on the record |
| Autocomplete | `↑`/`↓` and `↵` pick, `Backspace` removes the last chip, `Esc` closes the menu |

## Going to production

- [ ] Keep the database private: it holds fastyfold's signing key and the users' password hashes (or pass your own `secret_key`).
- [ ] Serve over HTTPS, and set `secure_cookies=True` if TLS ends at a proxy.
- [ ] Run uvicorn with `--proxy-headers` behind a proxy, so the sign-in limit sees real addresses.
- [ ] Cap request bodies at the proxy, too; the upload size check runs after Starlette has received the file.
- [ ] Set `owners=[...]` and give everyone else roles, or write `has_permission` rules.
- [ ] With several workers, the sign-in limit is counted per process (`# ponytail:` in the code says how to share it).

## Development

```bash
pip install -e ".[dev]" && playwright install chromium && pytest
FASTYFOLD_TEST_DB=postgresql+asyncpg://user:pass@localhost/db pytest --ignore=tests/test_browser.py   # against Postgres
ruff check src tests examples
# only when changing styles; the built CSS is committed
TAILWINDCSS_VERSION=v4.1.11 tailwindcss -i styles/fastyfold.css -o src/fastyfold/static/fastyfold.css --minify
```

See [CLAUDE.md](CLAUDE.md) for conventions and [AGENTS.md](AGENTS.md) for how everything fits together.

### Releasing

1. Bump `version` in `pyproject.toml`.
2. Add a `## <version>` section to [CHANGELOG.md](CHANGELOG.md).
3. Push a matching tag, e.g. `git tag -a v0.3.1 -m "fastyfold 0.3.1" && git push origin v0.3.1`.
4. The **Publish** workflow then:
   - runs the tests
   - builds the package
   - creates the GitHub Release, using that changelog section as the notes
   - publishes to PyPI, once the repository variable `PUBLISH_TO_PYPI` is `true` and PyPI trusted publishing is set up

## License

[Apache License 2.0](LICENSE)
