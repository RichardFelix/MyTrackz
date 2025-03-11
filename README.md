# Yamtrack

![App Tests](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/app-tests.yml/badge.svg)
![Docker Image](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/docker-image.yml/badge.svg)
![CodeFactor](https://www.codefactor.io/repository/github/fuzzygrim/yamtrack/badge)
![Codecov](https://codecov.io/github/FuzzyGrim/Yamtrack/branch/dev/graph/badge.svg?token=PWUG660120)
![GitHub](https://img.shields.io/badge/license-AGPL--3.0-blue)

Yamtrack is a self hosted media tracker for movies, tv shows, anime, manga, video games and books.

## Demo

You can try the app at [yamtrack.fuzzygrim.com](https://yamtrack.fuzzygrim.com) using the username `demo` and password `demo`.

## Features

- Track movies, tv shows, anime, manga and games.
- Track each season of a tv show individually and episodes watched.
- Save score, status, progress, repeats (rewatches, rereads...), start and end dates, or write a note.
- Keep a tracking history with each action with a media, such as when you added it, when you started it, when you started watching it again, etc.
- Create custom media entries, for niche media that cannot be found by the supported APIs.
- Use personal lists to organize your media for any purpose, add other members to collaborate on your lists.
- Keep up with your upcoming media with a calendar.
- Easy deployment with Docker via docker-compose with SQLite or PostgreSQL.
- Multi-users functionality allowing individual accounts with personalized tracking.
- Flexible authentication options including OIDC and 100+ social providers (Google, GitHub, Discord, etc.) via django-allauth.
- Integration with [Jellyfin](https://jellyfin.org/), to automatically track new media watched.
- Import from [Trakt](https://trakt.tv/), [Simkl](https://simkl.com/), [MyAnimeList](https://myanimelist.net/), [AniList](https://anilist.co/) and [Kitsu](https://kitsu.app/).
- Export all your tracked media to a CSV file and import it back.

## Screenshots

| Homepage                                                                                       | Calendar                                                                                    |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/homepage.png?v2" alt="Homepage" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/calendar.png" alt="calendar" /> |

| Media List Grid                                                                                    | Media List Table                                                                                     |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/medialist_grid.png" alt="List Grid" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/medialist_table.png" alt="List Table" /> |

| Media Details                                                                                         | Tracking                                                                                    |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/media_details.png" alt="Media Details" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/tracking.png" alt="Tracking" /> |

| Season Details                                                                                          | Tracking Episodes                                                                                            |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/season_details.png" alt="Season Details" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/tracking_episode.png" alt="Tracking Episodes" /> |

| Lists                                                                                 | Statistics                                                                                      |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/lists.png" alt="Lists" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/statistics.png" alt="Statistics" /> |

| Create Manual Entries                                                                                         | Import Data                                                                                       |
| ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/create_custom.png" alt="Create Manual Entries" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/import_data.png" alt="Import Data" /> |

## Installing with Docker

Copy the default `docker-compose.yml` file from the repository and set the environment variables. This would use a SQlite database, which is enough for most use cases.

To start the containers run:

```bash
docker-compose up -d
```

Alternatively, if you need a PostgreSQL database, you can use the `docker-compose.postgres.yml` file.

### Reverse Proxy Setup

When using a reverse proxy, if you see a `403 - Forbidden` error, you need to set the `URLS` environment variable to the URL you are using for the app.

```bash
services:
  yamtrack:
    ...
    environment:
      - URLS=https://yamtrack.mydomain.com
    ...
```

Note that the setting must include the correct protocol (`https` or `http`), and must not include the application `/` context path. Multiple origins can be specified by separating them with a comma (`,`).

### Environment variables

| Name                          | Notes                                                                                                                                                                                               |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TMDB_API                      | The Movie Database API key for movies and tv shows, a default key is provided                                                                                                                       |
| TMDB_NSFW                     | Default to `False`, set to `True` to include adult content in tv and movie searches                                                                                                                 |
| TMDB_LANG                     | TMDB metadata language, uses a Language code in ISO 639-1 e.g `en`, for more specific results a country code in ISO 3166-1 can be added e.g `en-US`                                                 |
| MAL_API                       | MyAnimeList API key, for anime and manga, a default key is provided                                                                                                                                 |
| MAL_NSFW                      | Default to `False`, set to `True` to include adult content in anime and manga searches from MyAnimeList                                                                                             |
| MU_NSFW                       | Default to `False`, set to `True` to include adult content in manga searches from MangaUpdates                                                                                                      |
| IGDB_ID                       | IGDB API key for games, a default key is provided but it's recommended to get your own as it has a low rate limit                                                                                   |
| IGDB_SECRET                   | IGDB API secret for games, a default value is provided but it's recommended to get your own as it has a low rate limit                                                                              |
| IGDB_NSFW                     | Default to `False`, set to `True` to include adult content in game searches                                                                                                                         |
| SIMKL_ID                      | Simkl API key only needed for importing media from Simkl, a default key is provided but you can get one at [Simkl Developer](https://simkl.com/settings/developer/new/custom-search/) if needed     |
| SIMKL_SECRET                  | Simkl API secret for importing media from Simkl, a default secret is provided but you can get one at [Simkl Developer](https://simkl.com/settings/developer/new/custom-search/) if needed           |
| REDIS_URL                     | Default to `redis://localhost:6379`, Redis is needed for processing background tasks, set this to your redis server url                                                                             |
| SECRET                        | [Secret key](https://docs.djangoproject.com/en/stable/ref/settings/#secret-key) used for cryptographic signing, should be a random string                                                           |
| URLS                          | Shortcut to set both the `CSRF` and `ALLOWED_HOSTS` settings, comma separated list of URLs, e.g. `https://yamtrack.mydomain.com` or `https://yamtrack.mydomain.com, https://yamtrack.mydomain2.com` |
| ALLOWED_HOSTS                 | Comma separated list of host/domain names that this Django site can serve, e.g. `yamtrack.mydomain.com` or `yamtrack.mydomain.com, 192.168.1.1`. Default to `*` for all hosts                       |
| CSRF                          | Comma separated list of trusted origins for `POST` requests when using reverse proxies, e.g. `https://yamtrack.mydomain.com` or `https://yamtrack.mydomain.com, https://yamtrack.mydomain2.com`     |
| REGISTRATION                  | Default to `True`, set to `False` to disable user registration                                                                                                                                      |
| DEBUG                         | Default to `False`, set to `True` for debugging                                                                                                                                                     |
| PUID                          | User ID for the app, default to `1000`                                                                                                                                                              |
| PGID                          | Group ID for the app, default to `1000`                                                                                                                                                             |
| TZ                            | Timezone, like `Europe/Berlin`. Default to `UTC`                                                                                                                                                    |
| WEB_CONCURRENCY               | Number of webserver processes, default to `1` but it's recommended to have a value of [(2 x num cores) + 1](https://docs.gunicorn.org/en/latest/design.html#how-many-workers)                       |
| SOCIAL_PROVIDERS              | Comma-separated list of social authentication providers to enable, e.g. `allauth.socialaccount.providers.openid_connect,allauth.socialaccount.providers.github`                                     |
| SOCIALACCOUNT_PROVIDERS       | JSON configuration for social providers, see the [Wiki](https://github.com/FuzzyGrim/Yamtrack/wiki/Social-Authentication-in-Yamtrack) for a OIDC configuration example.                             |
| ACCOUNT_DEFAULT_HTTP_PROTOCOL | Protocol for social providers, if your `redirect_uri` in OIDC config is `https` set this to `https`, default is determined based on your `CSRF` settings                                            |
| SOCIALACCOUNT_ONLY            | Default to `False`, set to `True` to disable local authentication when using social authentication only                                                                                             |
| REDIRECT_LOGIN_TO_SSO         | Default to `False`, set to `True` to automatically redirect (using javascript) to the SSO provider when there's only one available. Useful for single sign-on setups.                               |

### Environment variables for PostgreSQL

| Name        | Notes                        |
| ----------- | ---------------------------- |
| DB_HOST     | When not set, sqlite is used |
| DB_PORT     |                              |
| DB_NAME     |                              |
| DB_USER     |                              |
| DB_PASSWORD |                              |

## Local development

Clone the repository and change directory to it.

```bash
git clone https://github.com/FuzzyGrim/Yamtrack.git
cd Yamtrack
```

Install Redis or spin up a bare redis container:

```bash
docker run -d --name redis -p 6379:6379 --restart unless-stopped redis:7-alpine
```

Create a `.env` file in the root directory and add the following variables.

```bash
TMDB_API=API_KEY
MAL_API=API_KEY
IGDB_ID=IGDB_ID
IGDB_SECRET=IGDB_SECRET
SECRET=SECRET
DEBUG=True
```

Then run the following commands.

```bash
python -m pip install -U -r requirements-dev.txt
cd src
python manage.py migrate
python manage.py runserver & celery -A config worker --beat --scheduler django --loglevel DEBUG & tailwindcss -i ./static/css/input.css -o ./static/css/tailwind.css --watch
```

Go to: http://localhost:8000

## Donate

If you like the project and want to support it, you can donate via:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/fuzzygrim)
