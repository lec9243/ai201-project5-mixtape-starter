# AI Usage

I used AI assistance to navigate the unfamiliar Flask codebase, summarize the roles of the main files, and trace route-to-service call chains before changing code. During debugging, AI helped compare the working playlist notification path with the missing rating notification path, and helped reason about boundary cases such as Sunday streak updates, "listening now" time windows, duplicate rows from joins, and off-by-one slicing. I verified each diagnosis by reading the relevant route, service, model, and test files directly and by running the test suite before and after fixes.

# Codebase Map

## Main files and roles

- `app.py` defines the Flask application factory, configures SQLAlchemy, registers the `songs`, `playlists`, `users`, and `feed` blueprints, and creates database tables inside the app context.
- `models.py` defines the persistent data model: `User`, `Song`, `Tag`, `ListeningEvent`, `Rating`, `Playlist`, and `Notification`. It also defines association tables for friendships, song tags, and playlist entries. `playlist_entries` is more than a simple join table because it stores `position`, `added_by`, and `added_at` for ordered playlist behavior.
- `routes/songs.py` exposes song search, song detail, rating, and listening endpoints. The route layer parses request data and delegates search to `search_service`, rating to `notification_service.rate_song`, and listening/streak updates to `streak_service.record_listening_event`.
- `routes/playlists.py` exposes playlist creation, playlist detail, playlist songs, and adding songs to playlists. Playlist reads go through `playlist_service`; adding a song goes through `notification_service.add_to_playlist` because it also creates a notification for the original sharer.
- `routes/users.py` exposes user profile, streak, notification list, and mark-read endpoints. It delegates streak and notification behavior to services.
- `routes/feed.py` exposes friends-listening-now and activity-feed endpoints. Both route handlers call `feed_service`.
- `services/streak_service.py` records listening events and updates a user's `listening_streak` and `last_listened_at`.
- `services/feed_service.py` builds feed dictionaries from friends' `ListeningEvent` rows. `get_friends_listening_now` applies a recency cutoff and deduplicates to one current song per friend, while `get_activity_feed` returns recent friend activity without the same recency filter.
- `services/search_service.py` searches songs and returns serialized song dictionaries with tags from the `Song.to_dict()` model method.
- `services/notification_service.py` creates notifications and owns social interaction side effects for adding songs to playlists and rating songs.
- `services/playlist_service.py` creates playlists and retrieves playlist metadata and ordered playlist songs.
- `seed_data.py` resets and populates the development database with users, friendships, songs, tags, listening events, playlists, and sample notifications.
- `tests/` contains pytest coverage for streaks, search, and playlists, using in-memory SQLite app instances.

## Data flow example: rating a song

`POST /songs/<song_id>/rate` in `routes/songs.py` reads `user_id` and `score` from the JSON request, validates that both are present, and calls `notification_service.rate_song(user_id, song_id, score)`. The service validates the score range, loads the `Song` and rating `User`, then either updates an existing `Rating` for the same user/song pair or creates a new one. The route serializes the returned `Rating` with `to_dict()` and sends it back as JSON. Because ratings are social interactions with another user's shared song, notification behavior belongs in this service rather than the route.

## Data flow example: friends listening now

`GET /feed/<user_id>/listening-now` in `routes/feed.py` calls `feed_service.get_friends_listening_now(user_id)`. The service loads the current user, gathers IDs from the user's dynamic `friends` relationship, queries recent `ListeningEvent` rows for those friends, orders them newest first, and then walks the results while keeping a `seen_friends` set so each friend appears at most once. For each included event, it loads the friend and song models and returns a JSON-ready dictionary containing the friend, song, and timestamp.

## Patterns noticed

The route files are thin: they parse HTTP inputs, convert service exceptions into HTTP status codes, and format JSON responses. Business logic and database writes live in `services/`. The model `to_dict()` methods are the common serialization boundary. Several services intentionally work at the model level instead of returning Flask responses, which makes them straightforward to test directly with in-memory database fixtures.

# Root Cause Analysis

