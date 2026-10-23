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

That is the whole public API: `Admin`, `ModelAdmin` and `action`. The shortest setup is `admin = Admin(engine); admin.register(Order); admin.mount(app)`, which uses built-in users and a generated secret key (section 4d).

## 2. Request flow

1. `Admin.mount(app, path)` mounts `StaticFiles` at `{path}/static`, then the JSON API router if `api` is on (it must come before the admin router, whose `/{model}/{pk}` would match `/api/<model>`), then the admin `APIRouter`.
   - Every admin route uses `AdminRoute` (`Admin._route_class`), a custom `APIRoute`. It turns `HTTPException` (and `RequestValidationError`) into the HTML `error.html` page for browsers, keeps JSON for fetch and API clients and short bodies for htmx, and adds `SECURITY_HEADERS` (no framing, `no-store`, `nosniff`, `same-origin` referrer). Being a route class, it never touches the host app's own handlers.
2. Every route except `/login` and static files depends on `Admin._user`. It reads the signed `ff_session` cookie (itsdangerous; payload `{u: user, s: session id}`), then requires a live row in `fastyfold_session` (`audit.AdminSession`) with that id, user and a future `expires_at`. It sets `request.state.admin_user`. If there is no valid session:
   - Normal requests get a 303 redirect to `/login?next=…`.
   - htmx requests get `HX-Redirect`.
   - Sign-in inserts the row (14 days with "remember", else 1 day and a browser-session cookie) and sweeps expired rows. Sign-out deletes it. `Admin.end_sessions(user)` deletes all of a user's rows.
   - `Admin.current_user` is the same dependency, public, for the host app's routes.
3. Every POST also runs `Admin._csrf`:
   - The form field `_csrf`, or the `X-CSRF-Token` header for htmx and fetch, must equal `csrf_token(session)`.
   - That token is an HMAC of the session cookie with `secret_key`.
   - `base.html` puts it in a `<meta>` tag, and `fastyfold.js` adds it to every htmx request.
4. Permission check: `Admin.can(user, perm, model_admin, obj=None)` calls the global `Admin(permission=...)`, then `ModelAdmin.has_permission(user, perm)`, then, for one record, `has_object_permission(user, perm, obj)`. `perm` is one of `view`, `add`, `change`, `delete`, `export`, or an action name. Custom pages (`Page`) go through the same check with `view`. The default allows any signed-in user. Override it per model, or pass `Admin(permission=...)` to set it globally.
5. The view opens a session with `async with admin.session() as s` (an `async_sessionmaker` built from the user's engine), runs the query and renders a template.
   - Every query of a model's rows starts from `ModelAdmin.get_query(request)` (row-level scoping). One record is fetched with `Admin._get(s, ma, pk, request, perm)`: inside the scope or 404, then `has_object_permission` or 403.
   - Foreign-key and many-to-many values posted by a user are checked against the target's `get_query` (`Admin._objects`), so out-of-scope rows can't be linked.
6. Writes go through `audit.record()` in the same transaction, then commit.
   - An `IntegrityError` rolls back and becomes a form error (422) or a list message, worded with `db_message(e)`; the API returns 409.
   - `after_save` / `after_delete` run after a successful commit. All hooks may be sync or async (`call_hook`).

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
| GET, POST | `/_roles`, `/_roles/new`, `/_roles/{id}`, POST `/_roles/{id}/delete` | roles editor, owners only; with `Admin(owners=...)` | `roles.html`, `role_form.html` |
| GET, POST | `/api/{model}` | JSON list (`q`, `sort`, `page`, `per_page` ≤ 100, `f_<field>`) and create; only with `Admin(api=True)` | JSON |
| GET, PATCH, DELETE | `/api/{model}/{pk}` | JSON read, partial update, delete | JSON |

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
| Time | `time` | time |
| DateTime | `datetime` | datetime-local |
| Uuid (incl. postgresql.UUID) | `uuid` | text; parsed with `uuid.UUID` |
| Interval, ARRAY, LargeBinary, anything else | `other` | none: shown read-only, never written |
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

Type notes: a `TypeDecorator` is classified by its `impl`. A foreign key's `key_kind` is the kind of its own column (int, str or uuid), so string and UUID keys parse correctly. `_save` and the API share `Admin._assign` for setting values and computing the audit `changes`.

## 4d. Zero-config start: boot, secret key, built-in users (`users.py`)

- **`Admin._boot()`** runs once per process, on the first request. It's a router-level dependency on every fastyfold router, and `_user`/`_csrf` call it too. It:
  - creates fastyfold's tables;
  - loads the signing key from `fastyfold_setting`, or writes a new one there. A race between workers is settled by the primary key: whoever commits first wins, and the others re-read.

  `Admin.signer` raises if it's used before boot, unless `secret_key=` was passed.
- **Built-in users** (`authenticate=None`): `fastyfold_user` has username, PBKDF2 hash, `is_owner` and `last_login_at`.
  - **Sign-in:** `users.authenticate` hashes even for unknown names, so response time doesn't reveal which usernames exist.
  - **First run:** while there are no users, `/login` redirects to `/setup`. The setup page prints a one-time code, kept in `fastyfold_setting` so every worker agrees on it. Wrong codes count against the sign-in rate limit. The first account is an owner, and the code is deleted afterwards.
  - **Owners:** `Admin.is_owner(user)` is true for `owners=[...]` or a built-in user marked owner. That flag is loaded per request into `users.current_owner` by `_signed_in`, the same way role grants are.
  - **Roles:** `Admin.roles_enabled` is true with `owners=` or built-in users.
- **Users screen** (`/_users`, owners only): create, edit (password, owner flag, roles) and delete. Usernames can't be renamed, because the audit log and role membership refer to them. You can't demote or delete yourself, so there is always an owner. A reset password or a demotion ends that user's sessions.
- **My account** (`/_account`, any built-in user): change your own password. That ends your other sessions and keeps this one.
- **`register(*items)`** wraps a plain model in `type(f"{Model}Admin", (ModelAdmin,), {"model": Model})`.

## 4c. Roles (`roles.py`)

`Admin(owners=[...])` turns roles on. The pieces:

- **Tables:** `fastyfold_role` (name, description), `fastyfold_role_grant` (role, model or page slug, perm) and `fastyfold_role_member` (role, user). They live on their own `MetaData` and are created by `Admin.session()` only when roles are on. Collections use `lazy="raise"`, so load them with `selectinload`.
- **Per-request grants:** `Admin._signed_in` (called by `_user` and by the API's auth) loads the user's `(slug, perm)` pairs into the `roles.current_grants` ContextVar, with one query. FastAPI awaits async dependencies in the endpoint's task, so the endpoint and its templates see the value, and each request starts empty.
- **The check:** `Admin.can` checks the global hook, then the grants (owners skip this), then `has_permission`, then `has_object_permission`. No grant means no access, which also hides the model or page from navigation.
- **The editor:** the routes are owners only and every POST needs CSRF. `roles.matrix(admin)` lists every model and page with its perms and actions. Posted grants are filtered against it, so unknown pairs are dropped. Changes are audited with model `_roles`, and `Admin._log_models` shows those entries to owners only.

## 4b. JSON API (`api.py`)

`api.build(admin)` runs at mount time and returns an `APIRouter` with one set of routes per registered model.

- **Schemas** (`api.schemas`): `<Model>Read` (every field but `other`; m2m as a list of keys), `<Model>Create` (writable form fields; required unless the column has a default), `<Model>Update` (all optional; `exclude_unset` makes PATCH partial) and `<Model>Page` (`items`, `total`, `page`, `per_page`). `choice` becomes a `Literal`.
- **Writes** run the same steps as the form: choice and fk parsing, the target-scope check, `ModelAdmin.schema` (on the merged record, so a PATCH validates as a whole), `_assign`, `save`, audit, commit, `after_save`. The response is reloaded with `populate_existing`, so server defaults are filled in.
- **Auth:** with `api_auth` it uses that dependency, and no CSRF is needed because the credentials don't ride on cookies. Otherwise it uses the admin session, which returns 401 instead of a redirect and requires `X-CSRF-Token` on writes.

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
| Permission rule | `ModelAdmin.has_permission`, `has_object_permission`, or `Admin(permission=...)` |
| Per-user / tenant data | `ModelAdmin.get_query(request)` |
| Change a built-in template | `Admin(templates_dir=...)`; extend `original/fastyfold/<name>.html` |
