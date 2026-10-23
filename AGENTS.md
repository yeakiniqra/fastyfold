# AGENTS.md: how fastyfold works

This guide is for coding agents and contributors. It explains how a request travels through fastyfold, so you can change one part without breaking another. `CLAUDE.md` has the commands and conventions.

## 1. What a user writes

```python
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from fastyfold import Admin, ModelAdmin, action

engine = create_async_engine("sqlite+aiosqlite:///shop.db")
app = FastAPI()

async def login(username: str, password: str) -> str | None:
    return username if (username, password) == ("admin", "admin") else None

admin = Admin(engine, secret_key="change-me", authenticate=login, title="Acme Store", accent="#ff6a2b")

class OrderAdmin(ModelAdmin):
    model = Order
    group = "Shop"
    icon = "receipt_long"
    list_display = ["id", "customer", "status", "total", "created_at"]
    search_fields = ["customer.name", "note"]
    list_filter = ["status", "created_at"]
    board_field = "status"            # enables the Board view
    schema = OrderIn                  # optional Pydantic model for validation

    @action("Mark shipped", icon="local_shipping", key="s")
    async def mark_shipped(self, session, objs):
        for o in objs:
            o.status = "shipped"

admin.register(OrderAdmin)
admin.mount(app, "/admin")
```

That is the whole public API: `Admin`, `ModelAdmin` and `action`.

## 2. Request flow

1. `Admin.mount(app, path)` adds an `APIRouter` and mounts `StaticFiles` at `{path}/static`.
2. Every route except `/login` and static files depends on `Admin._user`. It reads the signed `ff_session` cookie (itsdangerous, `max_age` 14 days). If there is no valid session:
   - Normal requests get a 303 redirect to `/login?next=…`.
   - htmx requests get `HX-Redirect`.
3. Every POST also runs `Admin._csrf`:
   - The form field `_csrf`, or the `X-CSRF-Token` header for htmx and fetch, must equal `csrf_token(session)`.
   - That token is an HMAC of the session cookie with `secret_key`.
   - `base.html` puts it in a `<meta>` tag, and `fastyfold.js` adds it to every htmx request.
4. Permission check: `Admin.can(user, perm, model_admin)` calls `ModelAdmin.has_permission(user, perm)`. `perm` is one of `view`, `add`, `change`, `delete`, `export`, or an action name. Custom pages (`Page`) go through the same check with `view`. The default allows any signed-in user. Override it per model, or pass `Admin(permission=...)` to set it globally.
5. The view opens a session with `async with admin.session() as s` (an `async_sessionmaker` built from the user's engine), runs the query and renders a template.
6. Writes go through `audit.record()` in the same transaction, then commit.

## 3. Routes

All routes sit under the mount path.

| Method | Path | View | Template |
|---|---|---|---|
| GET/POST | `/login`, POST `/logout` | sign in (rate limited per IP) and out | `login.html` |
| GET | `/` | dashboard: record count per model, widgets and the latest audit entries | `dashboard.html` |
| GET | `/{model}` | list, with `?view=table\|board\|split&q=&page=&sort=&f_<field>=` | `list.html` |
| GET/POST | `/{page}` | a custom page from `@admin.page` (POST needs CSRF) | `page.html` or the page's Response |
| GET | `/{model}/export.csv` | CSV of the current search, filters and sort; perm `export` | none |
| GET | `/{model}/_lookup/{field}?q=` | JSON for an autocomplete: `field` is an fk, an m2m, or `<inline>.<fk>` | none |
| GET | `/{model}/{pk}/preview` | split-view preview panel (htmx partial) | `partials/preview.html` |
| GET/POST | `/{model}/new` | create form | `form.html` |
| GET/POST | `/{model}/{pk}` | edit form | `form.html` |
| POST | `/{model}/{pk}/delete` | delete one record | redirect |
| POST | `/{model}/{pk}/move` | board drag and drop, sets `board_field` | 204 |
| POST | `/{model}/action` | bulk action on `ids`: `delete`, `export` (returns CSV) or an `@action` | redirect |
| GET | `/_search?q=` | JSON for the ⌘K palette | none |
| GET | `/_log` | audit log list, with `?who=&model=&action=&days=` | `log.html` |
| GET | `/_media/{key}` | uploaded files for `LocalStorage`; images inline, everything else as a sandboxed download | none |

`{model}` is `ModelAdmin.slug`, which defaults to the lower-cased model class name. Models and pages share one slug namespace; `register()` and `page()` refuse a taken or reserved slug.

## 4. ModelAdmin and fields

`ModelAdmin.__init__` inspects `sqlalchemy.inspect(model)` once and builds `self.fields: dict[str, Field]` (`fields.py`).

`Field` holds:
- `name`, `label`, `kind`
- `required`, `readonly`, `choices`
- `relation`: the related `ModelAdmin` for a foreign key

`kind` decides three things: the widget (the `widget()` macro in `templates/fastyfold/form.html`), how form input is parsed (`Field.parse(str) -> value`), and how the value is displayed (`Field.display(value)`).

| SQLAlchemy type | kind | Widget |
|---|---|---|
| Integer, BigInteger | `int` | number |
| Float, Numeric | `decimal` | number, step any |
| String (≤255) | `str` | text |
| Text, String (>255) | `text` | textarea |
| Boolean | `bool` | toggle |
| Date | `date` | date |
| DateTime | `datetime` | datetime-local |
| JSON | `json` | monospace textarea |
| Enum, or `ModelAdmin.choices[name]` | `choice` | radio pills (≤5 options) or select |
| ForeignKey column | `fk` | autocomplete (`ui.combobox`) fed by `/_lookup`, labelled with `admin.label(obj)` |
| many-to-many relationship (`secondary=`) | `m2m` | multi-select autocomplete with chips |
| String listed in `ModelAdmin.file_fields` | `image` / `file` | file input with preview and a Remove box |

How a column's flags are set:

- **`readonly`:** the primary key, or listed in `readonly_fields`.
- **`auto`:** the column has a `server_default`, so the database fills it (for example `created_at`). It is left off the default `form_fields` and shown in the form's dark "Record" card instead. Listing it in `form_fields` makes it editable.
- **`required`:** the column is NOT NULL. Bool fields are never required.
- **`default`:** the column's scalar Python default. New forms are pre-filled with it, so required fields that have a default don't block saving.

Each option has a default when it's left out:

| Option | Default |
|---|---|
| `list_display` | first 6 columns |
| `form_fields` | every column that is neither `readonly` nor `auto` |
| `fieldsets` | one "Details" section; `[("Shipping", ["address", "city"]), …]` splits the form into cards |
| `search_fields` | every `str` column |
| `list_filter` | every `choice`, `bool` or `date` column |
| `export_fields` | `list_display` |
| `inlines` | none. `["items"]` edits every editable child column; `[("items", ["product_id", "qty"])]` picks them |
| `file_fields` | none. `{"photo": "image", "manual": "file"}` (a list means all `file`) |

Dotted names such as `customer.name` follow a many-to-one relationship. Search joins it, and display reads it. `list_display` may also name the relationship itself (`customer`), which displays its label, or a method defined on the ModelAdmin subclass (`def margin(self, obj)`), which is shown but not sortable (`ModelAdmin.is_method`). Related rows are labelled with `Admin.label(obj)`: their own ModelAdmin's `label()` when registered, else `str(obj)`.

Row identity, used by the table, board, split view, dashboard and palette:

- **`title_field`:** the first `str` column in `list_display`. It is shown with an avatar.
- **`subtitle_field`:** the second `str` column in `list_display`.
- **`lead_fk`:** the first many-to-one relationship in `list_display`. Records without a text title, such as orders, use it for their avatar and subtitle.
- **`avatar_text(obj)`, `subtitle(obj)`, `card_fields()`:** the helpers templates call. Use them instead of re-deriving.

Validation:
1. `Field.parse` turns form input into Python values. A failure becomes a per-field error.
2. If `ModelAdmin.schema` (Pydantic) is set, `schema.model_validate(data)` runs next. Its errors are mapped to field names.
3. If there are errors, the form is re-rendered with status 422 and the values the user typed.

Relations and files in `_save`:
- **m2m:** the posted ids (`form.getlist(name)`) must all exist, else "Select a valid option."; the relationship is replaced as a whole.
- **Inlines:** rows post as `<inline>-<i>-<field>`, plus `<inline>-<i>-id` for existing children and `<inline>-<i>-_delete`. Indices may have gaps (rows added in the browser). Blank new rows are dropped, ids that aren't this record's children are ignored, and the audit entry gets `{"items": [None, "1 added, 2 changed"]}`. Deleting the parent relies on the relationship's own cascade.
- **Files:** `storage.check_upload` enforces `max_upload` and, for images, the real file signature. New files are saved as `<slug>/<random>.<ext>`; the replaced or removed file is deleted after commit, and so are a deleted record's files.

## 5. List views

`ModelAdmin.list_query(params)` builds one `select()`:

- **Search:** `q` becomes an OR of case-insensitive `icontains` (with `%`/`_` escaped) over `search_fields`, with outer joins for dotted fields.
- **Filters:** `f_<field>=value`. Choice, bool and fk filters match exactly. Date filters take `today`, `7d` or `30d`. Number filters take `lo..hi`, `lo..`, `..hi` or one value; the filter panel's two inputs are joined into that by Alpine. Unparsable values are ignored, and only working filters get a chip (`ModelAdmin.filter_text`).
- **Export:** `export.csv` reuses the same query without paging. `ModelAdmin.export_value` writes ISO dates and raw numbers, and prefixes text starting with `= + - @` with `'` so spreadsheets never run it.
- **Sort:** `sort=field` or `sort=-field`. The default is `-pk`.
- **Paging:** `page` with `per_page`, defaulting to 25. `# ponytail:` this uses offset paging; switch to keyset paging for very large tables.

The views:

- **Table** shows the `list_display` columns, row checkboxes and a floating bulk-action bar (Alpine `selected` set). The bar posts to `/{model}/action`.
- **Board** appears only when `board_field` is set. There is one column per choice. Cards are draggable (HTML5 drag and drop in `fastyfold.js`), and dropping one calls `/{pk}/move`.
- **Split** shows a compact list. Clicking a row, or pressing `J`/`K`, loads `/{pk}/preview` into the right panel with htmx. The preview shows `preview_fields` (default `list_display`), quick actions and the latest audit entries for that record.

The context panel lists saved views: one per `board_field` choice with its count, plus "All". It also lists the other models in the same `group`. Rail icons are the groups. Each group's icon comes from its first model's `icon`, unless `Admin(groups={"Shop": "shopping_bag"})` sets it.

## 6. Audit log

`audit.py` defines `LogEntry` on its own `MetaData`. Its columns are `id`, `at`, `user`, `action`, `model`, `object_id`, `object_repr` and `changes`, where `changes` is JSON of `{field: [old, new]}`.

The table is created lazily on first use with `run_sync(metadata.create_all)`, guarded by a flag on the `Admin`. `record()` is called for every add, change, delete, move and action.

Everything that reads the log (dashboard, `/_log`) passes `models=` the slugs the user may view, so changes to hidden models never show. `/_log` filters by user, model, action and date, and shows each change as old → new.

## 7. ⌘K palette and shortcuts

`fastyfold.js` defines an Alpine store, `ff`:

- **Palette:** `⌘K`, `Ctrl+K` and `/` open it. Typing (debounced 150 ms) fetches `/_search?q=`, which returns this JSON:
  ```json
  {"groups": [{"title": "Records", "items": [{"label", "sub", "url", "icon", "badge"}]}, {"title": "Models"}, {"title": "Actions"}]}
  ```
  - Records come from up to 3 matches per model, using `search_fields`.
  - Models are matched by name.
  - Models are matched by name; custom pages are listed with them.
  - Actions are "New {model}" for every model the user may add to, "Export {models}" once you type, plus "Toggle dark mode" and "Keyboard shortcuts".

  `↑`/`↓` and `↵` navigate the results with `htmx.ajax` / `location`.
- **Global keys:** `?` shortcuts sheet, `G` then a model's `key` to jump to it (`D` for the dashboard), `T` dark mode, `N` new record, `Esc` close.
- **List keys:** `J`/`K` move, `X` select, `↵`/`E` open, `F` filters, `1`/`2`/`3` for Table, Board and Split.
- **Action keys:** `@action(key="s")` runs that action on the selected rows (bulk bar), the previewed record (split view) or the open record (its form's Actions card). Built-in keys win on a clash.
- **Autocomplete (`ffCombo`):** `↑`/`↓` and `↵` pick, `Backspace` removes the last chip, `Esc` closes the menu without leaving the page. Keys that land on its button while the menu is open still drive the menu.
- **Form keys:** `⌘S` save, `⌘↵` save and keep editing.

Keys are ignored while focus is in an input, except `⌘K`, `⌘S` and `Esc`.

## 8. Frontend build

- `styles/fastyfold.css` is the Tailwind v4 source. It defines:
  - the theme tokens `--color-accent`, `--color-panel` and so on, all mapped to `--ff-*` CSS variables
  - the dark variant
  - the `@utility` components `float`, `pill`, `pill-accent`, `pill-plain`, `field`, `field-label` and `kbd`
- `@source` scans `src/fastyfold/templates` and `src/fastyfold/*.py`. Class strings built in Python must appear literally in one of those.
- `TAILWINDCSS_VERSION=v4.1.11 tailwindcss -i styles/fastyfold.css -o src/fastyfold/static/fastyfold.css --minify` writes the built CSS. `tailwindcss` is the standalone binary from the `pytailwindcss` dev dependency, so no Node is needed. Commit the output.
- htmx and Alpine are vendored in `static/vendor/` at pinned versions. Fonts (Plus Jakarta Sans, Material Symbols Rounded) load from Google Fonts. `# ponytail:` vendor the woff2 files for offline admins.
- `Admin(accent=...)` sets `--ff-accent` and the hover, soft and ink variants that `base.html` derives with `color-mix()`. That one colour rebrands every screen.

## 9. Common changes

| Change | Where |
|---|---|
| New column type | `fields.py` `KIND_BY_TYPE` + parse/display + widget macro + test |
| New built-in page | route in `admin.py`, template extending `layout.html`, rail/palette entry, test |
| A screen for one app | `@admin.page(...)` in that app, no fastyfold change needed |
| New ModelAdmin option | class attribute with default in `model.py`, document in README + section 4 here |
| Visual tweak | `styles/fastyfold.css` or template, then rebuild the CSS (section 8) |
| Permission rule | `ModelAdmin.has_permission` or `Admin(permission=...)` |
