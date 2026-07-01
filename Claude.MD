# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Yamtrack is a self-hosted Django media tracker covering movies, TV shows, anime, manga, games, books, comics, and board games. The default/integration branch is `dev` (not `main`); CI runs on pushes to `main` and `dev` and on PRs.

## Commands

All Python commands run through `uv`. Django management commands run from the `src/` directory.

```bash
# Install deps (dev group = lint + test)
uv sync --group dev
uv run pre-commit install

# Run the dev server (needs Redis running, e.g. `docker run -d -p 6379:6379 redis:8-alpine`)
cd src && uv run manage.py migrate
cd src && uv run manage.py runserver

# Celery worker + beat scheduler (separate terminal)
cd src && uv run celery -A config worker --beat --scheduler django --loglevel DEBUG

# Tailwind watcher (separate terminal, requires tailwindcss CLI)
cd src && tailwindcss -i ./static/css/input.css -o ./static/css/tailwind.css --watch
```

Required `.env` keys for local dev (see `docs/env-variables.md` for the full list): `TMDB_API`, `MAL_API`, `IGDB_ID`, `IGDB_SECRET`, `STEAM_API_KEY`, `BGG_API_TOKEN`, `SECRET`, `DEBUG=True`.

### Testing

```bash
cd src && uv run playwright install        # once, before running integration tests
cd src && uv run manage.py test --parallel
cd src && uv run manage.py test app.tests --parallel          # single app
cd src && uv run manage.py test app.tests.test_models.SomeTestCase --parallel  # single test case
```

Tests use `config.test_settings` (set via `pytest.ini`) and `fakeredis` instead of real Redis. CI runs `uv run coverage run src/manage.py test app users integrations lists events --parallel`.

### Lint / format

```bash
uv run ruff check src          # CI lint gate
uv run ruff check --fix src
uv run ruff format src
uv run djlint --lint src       # template linting (Django templates)
uv run djlint --reformat src
uv run pre-commit run --all-files   # everything above + makemigrations --check + uv lock --check
```

Ruff is configured with `select = ["ALL"]` in `pyproject.toml`; migrations are excluded from linting. `src/manage.py makemigrations --check` runs as a pre-commit hook — generate migrations whenever you change `models.py` in any app.

### Docs

```bash
uv sync --group docs
uv run zensical serve
```

## Architecture

Django project with settings/URLs in `src/config/`, and five apps under `src/`: `app` (core media tracking), `events` (calendar/notifications), `integrations` (imports/exports/webhooks), `lists` (custom user lists), `users`.

### Polymorphic media model (`app/models.py`)

Every trackable thing is represented by two layers:

- **`Item`**: shared identity (`media_id`, `source`, `media_type`, `title`, `image`, optional `season_number`/`episode_number`). One `Item` row per real-world piece of media; uniqueness is enforced via partial `UniqueConstraint`s depending on whether season/episode numbers are set.
- **Per-type `Media` subclasses**: `TV`, `Season`, `Episode`, `Movie`, `Anime`, `Manga`, `Game`, `Book`, `Comic`, `BoardGame` — each is a concrete Django model inheriting the abstract `Media` model, tied to a `user` and an `Item`. These hold the user's tracking state: `status`, `score`, `progress`, `start_date`/`end_date`, `notes`. `MediaTypes.values` (a `TextChoices`) is the canonical list of valid model names, and code frequently does `apps.get_model("app", media_type)` to resolve the concrete model dynamically rather than importing each model by name — keep this in mind when adding a new media type or grepping for usages.
- **TV is hierarchical**: `TV` → `Season` (FK `related_tv`) → `Episode` (FK `related_season`). Saving an `Episode`, completing a `Season`, or completing a `TV` triggers cascading status updates (e.g., marking a season complete starts the next available season or completes the show; episodes auto-create remaining released episodes when a season is marked complete). This logic lives in `Media.save()`/`TV.save()`/`Season.save()`/`Episode.save()` in `app/models.py` — read it before touching TV/season/episode status transitions.
- **`MediaManager`** (`app/models.py`) centralizes querying: `get_media_list`, `get_home_status`, `get_media`/`get_media_prefetch`, `filter_media`/`filter_media_prefetch`. Sorting/prefetching differs per media type (TV and Season need special annotations for progress/start/end date), so prefer extending these helpers over writing one-off querysets in views.
- History is tracked via `django-simple-history` (`HistoricalRecords` on `Media`/`Episode`), with bulk operations going through `bulk_create_with_history`/`bulk_update_with_history` so history rows stay consistent — don't use plain `bulk_create`/`bulk_update` on these models.
- `app.mixins.disable_fetch_releases()` is a context manager to suppress the calendar-refresh side effects during bulk operations (e.g. imports).

### Media-type configuration (`app/config.py`)

`MEDIA_TYPE_CONFIG` is the single source of truth for per-media-type UI metadata: which `Sources` are valid/default for a type, icons, colors, unit labels ("Episode"/"Chapter"/"Issue"...), and which date field represents "release". Access it through the `get_*` helper functions rather than indexing the dict directly. Adding a new media type means updating `MediaTypes`/`Sources` in `models.py`, this config, and the provider dispatch tables in `app/providers/services.py`.

### Providers (`app/providers/`)

One module per external metadata source (`tmdb`, `mal`, `mangaupdates`, `igdb`, `openlibrary`, `hardcover`, `comicvine`, `bgg`, plus `manual` for user-created entries and `tvdb` used only by webhooks for ID lookups). `services.py` is the dispatch layer: `get_media_metadata(media_type, media_id, source, ...)` and `search(media_type, query, page, source=None)` route to the right provider based on `media_type`/`source`. All outbound HTTP goes through `services.api_request`, which uses a shared rate-limited `requests` session (per-host limiter rules already configured for each API) and raises `ProviderAPIError` on failure — new providers should reuse `api_request` rather than calling `requests` directly. `app.middleware.ProviderAPIErrorMiddleware` turns that exception into a user-facing error page/message.

### Events / calendar (`src/events/`)

`events.models.Event` stores upcoming/aired release dates per `Item`. `events/calendar/main.py:fetch_releases` is the entry point: it dispatches each `Item` to a per-media-type processor in `events/calendar/{tv,anime,comic,other}.py`, bulk-creates/updates `Event` rows, and removes events that are no longer valid (e.g. an episode got renumbered/removed upstream). This is triggered automatically via `Item.fetch_releases()` (called from `Media.process_status()`/TV-season-episode save hooks) and periodically via the Celery beat task `Reload calendar`. `events/notifications.py` + the `Send release notifications`/`Send daily digest` beat tasks push Apprise notifications for items nearing/at release.

### Integrations (`src/integrations/`)

- `integrations/imports/`: one module per import source (Trakt, Simkl, MyAnimeList, AniList, Kitsu, IMDb, Goodreads, HLTB, Steam, and Yamtrack's own CSV format). `imports/helpers.py` has shared logic for turning parsed external records into `Item`/`Media` rows.
- `integrations/webhooks/`: media-server webhook receivers (Jellyfin, Plex, Emby) built on a shared `BaseWebhookProcessor` (`webhooks/base.py`) composed from `TVWebhookMixin`/`MovieWebhookMixin`/`AnimeWebhookMixin`. Each concrete processor only needs to implement the abstract extraction methods (`_get_media_type`, `_extract_external_ids`, `_is_played`, etc.); shared TV/movie/anime processing logic lives in the mixins.
- `integrations/exports.py`: CSV export of a user's tracked media.
- `integrations/tasks.py`: Celery tasks wrapping the above for periodic/async imports.

### Users (`src/users/`)

Custom `User` model (`AUTH_USER_MODEL = "users.User"`) holds per-user UI preferences as `TextChoices`-backed fields: layout (grid/table), sort and status-filter defaults *per media type*, home page sort, calendar layout, date/time formatting, etc. When adding a new user-facing list/filter option, follow the existing pattern of a `*Choices` class in `users/models.py` plus a migration, rather than a free-text field.

### Frontend

Server-rendered Django templates (`src/templates/`, app-local `templates/` dirs) styled with Tailwind CSS (compiled via the Tailwind CLI, not a Node bundler) and interactivity via **HTMX** + **Alpine.js** (both vendored under `static/js/libraries/`, no npm/bundler step for JS). Templates are linted/formatted with `djlint` (see `.pre-commit-config.yaml`), 2-space indent. There is no JS test suite; Playwright (`pytest-playwright`) drives full-page integration tests through Django's test runner instead.

### Auth

`django-allauth` provides login/signup plus 100+ social/OIDC providers, configured at runtime via the `SOCIAL_PROVIDERS`/`SOCIALACCOUNT_PROVIDERS` env vars (appended to `INSTALLED_APPS` in `config/settings.py`). Custom login/signup forms live in `users/forms.py`. `app.middleware.AutoLoginMiddleware` (conditionally inserted) supports the `YAMTRACK_AUTO_LOGIN_USERNAME` auto-login env var.

### Async / scheduling

Celery (`config/celery.py`) with `django_celery_beat`'s `DatabaseScheduler` runs periodic tasks defined in `CELERY_BEAT_SCHEDULE` (`config/settings.py`): calendar reload, release notifications, daily digest, and user-message cleanup. Task results are stored via `django_celery_results` (`CELERY_RESULT_BACKEND = "django-db"`). Redis backs both the cache (`django-redis`) and the Celery broker.

## Personal deployment (LXC + custom image-cache fork)

This local copy has a custom local image-caching feature (downloads/serves provider images locally instead of hotlinking — see `app/tasks.py:cache_item_image`, `Item.cached_image_url`) that isn't in upstream. It's deployed to a Proxmox LXC container via Docker Compose, built from source rather than the published `ghcr.io/fuzzygrim/yamtrack` image (`docker-compose.yml` uses `build: .` + `image: yamtrack:local`).

### Branch layout

- `dev` — tracks `origin/dev` (upstream) directly, never edited locally. Only moves via `git pull`.
- `feature/image-cache` — the branch actually deployed; holds the image-cache feature on top of upstream. All work happens here.

### Updating when upstream releases something new

```bash
git checkout dev
git pull                          # fast-forward, dev has no local changes
git checkout feature/image-cache
git merge dev                     # bring upstream changes into the feature branch
```

Merge, not rebase — there's no upstream PR planned for this feature, so a merge commit recording conflict resolution once is simpler than re-resolving on every rebase.

Resolve conflicts if any. Most likely spots are the files the feature touches: `app/models.py`, `app/tasks.py`, `app/apps.py`, `app/helpers.py`, `app/views.py`, `config/settings.py`, `config/urls.py`, `lists/models.py`, the templates rendering `cached_image_url` (`home_grid.html`, `media_card.html`, `media_card_list.html`, `media_table_items.html`, `statistics.html`, `calendar_list.html`, `excluded_items.html`, `search_results.html`), `docker-compose.yml`, `docker-compose.postgres.yml`, `nginx.conf`, `entrypoint.sh`.

**Migration numbering collision** is the sharpest edge: if upstream adds a migration after `0061_episode_item_not_null`, it'll also be named `0062_*.py`. Git won't flag this as a text conflict (different filename), but Django ends up with two migrations both depending on `0061` — two leaf nodes. Fix: rename `app/migrations/0062_item_image_cached.py` to the next free number after upstream's new migration, and update its `dependencies` to point at upstream's new latest migration instead of `0061` directly.

### Testing natively on the LXC (fast pre-deploy gate)

Building/running the app still only happens via Docker Compose on the LXC. But `uv` is also installed directly on the LXC host (outside Docker) purely so tests can run in seconds instead of waiting on a full image rebuild. One-time setup on the LXC:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin, manages its own Python 3.12
source ~/.local/bin/env
cd /home/yamtrack
uv sync --group dev
cd src && uv run playwright install
sudo $(uv run which playwright) install-deps      # OS libs for headless Chromium; needs a real sudo password
```

After that, `./scripts/test.sh` (repo root) runs the same checks as CI — `ruff check` (report-only, not a hard gate — this fork carries some pre-existing lint debt under `select = ["ALL"]`), `makemigrations --check`, and the full test suite with coverage — all natively, no Docker rebuild required.

### Redeploying

```bash
rsync -avz --exclude='.git' --exclude='db' --exclude='media' --exclude='staticfiles' \
  /home/justme/Downloads/git/yamtrack/Yamtrack/ root@192.1.1.104:/home/yamtrack/
```

(Adjust the IP if it changes.) Then on the LXC container, run the native test gate before rebuilding:

```bash
cd /home/yamtrack
./scripts/test.sh
docker compose up -d --build
```

`entrypoint.sh` runs `migrate --noinput` on boot, applying both upstream's new migrations and the local `0062_item_image_cached` in one step — this is also what surfaces a migration-numbering collision if it wasn't caught by eye during the merge.

Optional extra verification before trusting the boot logs alone, using the already-built image (no extra install):

```bash
docker compose run --rm yamtrack uv run manage.py makemigrations --check
docker compose run --rm yamtrack uv run manage.py test app.tests --parallel
```

### Verify

```bash
docker logs -f yamtrack
```

Should show a clean startup with no migration errors. Open the app, track a new item, and confirm its poster resolves via `/media/items/...` rather than hotlinking the provider's CDN directly — that confirms the image-cache feature survived the merge.
