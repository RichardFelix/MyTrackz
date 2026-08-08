<!-- --8<-- [start:docs-index-intro] -->

# MyTrackz

![GitHub](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Fork of](https://img.shields.io/badge/fork%20of-Yamtrack-8b5cf6)

MyTrackz is a self-hosted media tracker for movies, tv shows, anime, manga, video games, books, comics, and board games.

It's a personal fork of [Yamtrack](https://github.com/FuzzyGrim/Yamtrack), rebranded and extended with a redesigned UI plus fork-only features including local image caching, personalized recommendations ("Discover"), a global "Trending" page, a "Continue the Story" home widget, custom poster images, genre filtering, live library counts, and compact or fully expanded home layouts.

Every fork-only addition (marked ⭐ below) was designed and implemented with [Claude Code](https://claude.com/claude-code) and [Codex](https://openai.com/codex/).

<!-- --8<-- [end:docs-index-intro] -->

## 📚 Documentation

Since MyTrackz tracks the same core feature set as Yamtrack, the upstream documentation is useful for shared features: [fuzzygrim.github.io/Yamtrack](https://fuzzygrim.github.io/Yamtrack/). Fork-specific setup and development notes are kept in this repository; the Docker instructions below are the authoritative installation path for MyTrackz.

<!-- --8<-- [start:docs-index-body] -->

## ✨ Features

Everything from upstream Yamtrack, plus this fork's own additions (marked ⭐).

- 🎬 Track movies, tv shows, anime, manga, games, books, comics, and board games.
- 📺 Track each season of a tv show individually and episodes watched.
- 📝 Save score, status, progress, repeats (rewatches, rereads...), start and end dates, or write a note.
- 📈 Keep a tracking history with each action with a media, such as when you added it, when you started it, when you started watching it again, etc.
- ✏️ Create custom media entries, for niche media that cannot be found by the supported APIs.
- 📂 Create personal lists to organize your media for any purpose, add other members to collaborate on your lists.
- 📅 Keep up with your upcoming media with a calendar, which can be subscribed to in external applications using a iCalendar (.ics) URL.
- 🔔 Receive notifications of upcoming releases via Apprise (supports Discord, Telegram, ntfy, Slack, email, and many more).
- 🐳 Easy deployment with Docker via docker-compose with SQLite or PostgreSQL.
- 👥 Multi-users functionality allowing individual accounts with personalized tracking.
- 🔑 Flexible authentication options including OIDC and 100+ social providers (Google, GitHub, Discord, etc.) via django-allauth.
- 🦀 Integration with [Jellyfin](https://jellyfin.org/), [Plex](https://plex.tv/) and [Emby](https://emby.media/) to automatically track new media watched, including metadata-poor Plex DVR episodes and safe title-based TV matching.
- 📥 Import from [Trakt](https://trakt.tv/), [Simkl](https://simkl.com/), [MyAnimeList](https://myanimelist.net/), [AniList](https://anilist.co/), [Kitsu](https://kitsu.app/), IMDb, Goodreads, HowLongToBeat, and Steam, with support for periodic automatic imports.
- 📊 Export all your tracked media to a CSV file and import it back.
- 🍿 See where to stream a movie or show (JustWatch/TMDB providers), scoped to a region you set in preferences.
- 🧮 A statistics dashboard — activity heatmap, score/status distributions, media-type breakdown, and a timeline of everything you've tracked.
- 🔔 A daily digest notification option (one bundled Apprise message instead of one per release), a one-click test-notification button, and per-item exclusion from release notifications.
- 🔄 Re-sync any tracked item's metadata (title, image, episode list, etc.) from its provider with one click.
- ⏱️ Fine-grained preferences: a "quick watch date" mode for one-tap episode/season tracking, plus independent date format, 12/24-hour time format, and first-day-of-week settings.
- 🗓️ Calendar grid/list layout toggle and a manual "reload" to refresh upcoming release dates on demand; TV season data is reconciled during refreshes so newly announced seasons and episodes appear in tracking and calendar views.
- 🔐 Regenerate your personal webhook/ICS token any time, with granular controls for which Jellyfin events (play/stop, manual mark played/unplayed) and which Plex usernames are processed.
- 📲 ⭐ **Improved PWA support** — Android installation and offline behavior are more reliable, with an offline fallback page.
- 🛠️ ⭐ **Grouped preferences** — organize appearance, tracking, home recommendations, region and formats, and library navigation settings, with configurable Wishlist or Backlog terminology.
- 🔢 ⭐ **Live library counts** — every media library shows the number of matching items, updating as you search, filter, or switch layouts.
- 🏠 ⭐ **Show all home items** — load every item in each home row automatically, or keep the compact “Load all” control for larger libraries.
- 🏠 ⭐ **Compact home list** — switch the home screen from poster cards to a compact, swipeable list with episode details and one-tap progress controls.
- 📡 ⭐ **Aired-only home filter** — hide caught-up episodic shows until their next episode airs, with a single toggle beside the home sort control. The in-progress row refreshes automatically when that episode becomes available or when you catch up.
- 🎨 ⭐ **Colored grid progress buttons** — optionally use clear red/green backgrounds for decrease/increase progress actions in the home card grid.
- 🖼️ ⭐ **Local image caching** — provider posters/covers are downloaded and served locally (WebP) instead of hotlinking, with size-capped eviction and a manual purge option in Settings → Advanced.
- 🧭 ⭐ **Discover page** — personalized recommendations aggregated from everything you've completed or are currently tracking, ranked and interleaved across media types.
- 🔥 ⭐ **Trending page** — what's popular right now per media type (TMDB, MyAnimeList, IGDB, BGG), hiding anything you already track.
- 📚 ⭐ **"Continue the Story" home widget** — surfaces direct follow-ups (sequels, next volumes, expansions, remasters) for media you've recently finished or are still working through. Can be toggled on/off in Settings → Preferences.
- 🖌️ ⭐ **Custom poster images** — replace any item's poster with your own image URL, with one click to revert to the original.
- 🏷️ ⭐ **Genre filter** — filter your media lists by genre, sourced automatically from provider metadata.
- 🎨 ⭐ **Redesigned UI** — liquid-glass mobile bottom nav with an updated media-type grid and morphing library panel, plus a one-tap "Mark watched" control for movies.
- 🖱️ ⭐ **Hover-lift media cards** — cards scale up and lift with a soft shadow on hover, revealing unified quick-action icons (mark watched, add to lists, view history) right on the poster.
- 🔎 ⭐ **Command palette search** — press <kbd>⌘K</kbd> (or <kbd>/</kbd>) anywhere to pull up a jump-to search modal, scoped to a media type, without leaving the page.
- 🖼️ ⭐ **Profile images** — set an avatar from any image URL, with a circular drag-and-zoom cropper; downloaded and cached locally like everything else.
- ↕️ ⭐ **Home screen row order** — drag or use the arrows to choose which media type shows first on your home page and in the sidebar.
- 🧹 ⭐ **Cache management in Settings → Advanced** — see exactly how much disk the local image cache and search cache are using, with one-click buttons to purge either.
- ⚠️ ⭐ **Cache action confirmations** — confirm search-cache and image-cache purges before any cached data is removed.

## 📱 Screenshots

### Feature Walkthroughs ⭐

| Home workflow | Mobile workflow |
| ------------- | --------------- |
| ![Home workflow](docs/assets/walkthroughs/homepage.gif) | ![Mobile workflow](docs/assets/walkthroughs/mobile.gif) |

Here are a few highlights from the redesigned interface:

| Home dashboard ⭐ | Mobile navigation ⭐ |
| ----------------- | ------------------- |
| ![MyTrackz home dashboard](docs/assets/screenshots/homepage_aired_only.png) | ![MyTrackz mobile navigation](docs/assets/screenshots/mobile_home_aired_only.png) |

| Discover recommendations ⭐ | Trending media ⭐ |
| --------------------------- | ---------------- |
| ![Discover recommendations](docs/assets/screenshots/discover.webp) | ![Trending media](docs/assets/screenshots/trending.webp) |

## 🐳 Installing with Docker

Unlike upstream, this fork isn't published to a container registry — it builds from source. Clone this repository, update the environment values in `docker-compose.yml` (and an optional local `docker-compose.override.yml`), and start it:

```bash
git clone https://bitbucket.org/RichardFelix/mytrackz.git
cd mytrackz
docker compose up -d --build
```

The default Compose file uses SQLite, which is enough for most personal installs. The PostgreSQL example is available in `docker-compose.postgres.yml`; change its `yamtrack` service from the upstream `image:` to `build: .` when running this fork so the fork's code is used. See `docs/env-variables.md` for the configuration reference.

## 💻 Development

See `docs/development.md` for the shared development workflow. `AGENTS.md` contains the current fork-specific architecture notes, branch policy, validation requirements, and deployment guidance.

## 🙏 Credit

MyTrackz is a personal fork of [Yamtrack](https://github.com/FuzzyGrim/Yamtrack) by [FuzzyGrim](https://github.com/FuzzyGrim). All of the core tracking functionality and most of the codebase comes from that upstream project — go star it, and if you don't need any of the fork-only features above, you're probably better served running Yamtrack directly.

All of the ⭐ fork-only features — image caching, Discover, Trending, Continue the Story, custom poster images, genre filtering, live library counts, the redesigned UI, command palette, profile images, compact home lists, Aired-only filtering, colored progress controls, home row ordering, cache management and confirmations, grouped preferences, expanded home rows, and PWA improvements — were built with [Claude Code](https://claude.com/claude-code) and [Codex](https://openai.com/codex/).

<!-- --8<-- [end:docs-index-body] -->
