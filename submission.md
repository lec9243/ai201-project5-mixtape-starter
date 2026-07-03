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

## Issue #1: My listening streak keeps resetting

### How you reproduced it

I reproduced this with the existing regression test `tests/test_streaks.py::test_streak_increments_on_sunday`. The test creates a user, calls `update_listening_streak` for Saturday, June 15, 2024, then calls it again for Sunday, June 16, 2024. Before the fix, the user's streak stayed at `1` instead of incrementing to `2`.

### How you found the root cause

I traced the listen flow from `POST /songs/<song_id>/listen` in `routes/songs.py` to `services/streak_service.record_listening_event`, then into `update_listening_streak`. The existing tests showed that first listens, same-day listens, Tuesday-after-Monday listens, and skipped-day resets all worked. The only failing case was Saturday to Sunday, which pointed directly at the branch that checked `days_since_last == 1 and today.weekday() != 6`.

### The root cause

`datetime.weekday()` returns `6` for Sunday, and the streak code explicitly refused to increment when `today.weekday() == 6`. That meant a legitimate consecutive-day listen from Saturday to Sunday was treated as a reset case. The actual streak rule only depends on whether the user listened exactly one calendar day after the previous listen; Sunday should not be special.

### Your fix and side-effect check

I changed the consecutive-day branch to check only `days_since_last == 1`. This preserves the existing behavior for first listens, same-day listens, normal consecutive days, and skipped-day resets while allowing Saturday-to-Sunday listening to increment correctly. I verified the side effects by running the streak test file after the change.

## Issue #5: The last song in a playlist never shows up

### How you reproduced it

I reproduced this with the existing tests in `tests/test_playlists.py`. The fixture creates a playlist with five ordered tracks. Before the fix, `test_playlist_returns_all_songs` received only four songs, and `test_playlist_returns_songs_in_order` received `Track 1` through `Track 4` but not `Track 5`.

### How you found the root cause

I traced `GET /playlists/<playlist_id>/songs` in `routes/playlists.py` to `services.playlist_service.get_playlist_songs`. The SQLAlchemy query joined `Song` to `playlist_entries`, filtered by the playlist ID, and ordered by `playlist_entries.position`, which was the right data access pattern. The suspicious part was after the query: the function returned `songs[:-1]`.

### The root cause

Python list slicing with `[:-1]` returns every element except the final one. The database query was already returning all playlist songs in the correct order, but the service dropped the last element during serialization. This affected every non-empty playlist, regardless of playlist length.

### Your fix and side-effect check

I changed the return statement to serialize `songs` directly instead of `songs[:-1]`. This keeps the existing query, ordering, and empty-playlist behavior unchanged while returning the complete ordered list. I verified the side effects by running the playlist tests, including the empty playlist case.

## Issue #2: Friends Listening Now shows people from yesterday

### How you reproduced it

I added `tests/test_feed.py::test_listening_now_excludes_yesterday_even_within_24_hours` to reproduce the issue. The test freezes the service clock at July 2, 2026, 12:00 UTC, creates one friend who listened 10 minutes ago and another friend who listened 16 hours ago on July 1, 2026, then calls `get_friends_listening_now`. Before the fix, both friends appeared in the "Listening Now" feed.

### How you found the root cause

I traced `GET /feed/<user_id>/listening-now` in `routes/feed.py` to `services.feed_service.get_friends_listening_now`. The friendship lookup, event ordering, and per-friend deduplication were correct. The value that made the old friend pass the filter was the module-level `RECENT_THRESHOLD`, which was set to `timedelta(hours=24)`.

### The root cause

The "Listening Now" service treated any listen from the past 24 hours as current. A 24-hour cutoff can include events from the previous calendar day, so users who were not currently listening still appeared in the real-time feed. The activity feed is the correct place for older friend events; "Listening Now" needs a much shorter recency window.

### Your fix and side-effect check

I changed `RECENT_THRESHOLD` from 24 hours to 30 minutes. This keeps genuinely recent listening events, excludes yesterday's events, and leaves `get_activity_feed` unchanged because that endpoint intentionally returns older activity. I verified the change by running the new feed regression test.

## Issue #4: I got notified when a friend added my song to a playlist but not when they rated it

### How you reproduced it

I added `tests/test_notifications.py::test_rating_someone_elses_song_notifies_original_sharer`. The test creates a song shared by `nova`, has `darius` rate it, and then queries `Notification` rows for `nova`. Before the fix, SQLAlchemy raised `NoResultFound` because the rating was saved but no notification was created. I also added a self-rating test to confirm users do not notify themselves.

### How you found the root cause

I traced `POST /songs/<song_id>/rate` in `routes/songs.py` to `services.notification_service.rate_song`. Then I compared that function with the working `add_to_playlist` path in the same service. `add_to_playlist` validates the song, actor, and playlist, performs the action, then calls `create_notification` for the original sharer when another user added the song. `rate_song` validated the song and rater and saved the `Rating`, but it returned immediately after committing.

### The root cause

The rating workflow did not include the notification side effect. The app's architecture puts social-interaction notifications in `notification_service`, and the playlist-add interaction followed that pattern, but the rating interaction only wrote the `Rating` row. Because the route delegates all rating behavior to `rate_song`, there was no later step that could create the missing notification.

### Your fix and side-effect check

I added a `song_rated` notification after the rating commit when `song.shared_by != user_id`. The notification goes to the original sharer and includes the rater username, song title, and score. I checked the side effects with tests for both another-user rating, which now creates one notification, and self-rating, which still creates none.

## Issue #3: The same song keeps showing up twice in search

### How you reproduced it

I used the multi-tag search fixture pattern from `tests/test_search.py`: one song titled `Crown Heights Anthem` with three tags. Running the same `outerjoin(song_tags)` shape at the SQL row level returned three rows with the same song ID. In the installed SQLAlchemy version, `db.session.query(Song).all()` collapses those duplicate entity identities, so the existing service-level duplicate test did not fail locally, but the query itself still produced the duplicate rows that explain the reported symptom.

### How you found the root cause

I traced `GET /songs/search?q=...` in `routes/songs.py` to `services.search_service.search_songs`. The service queried `Song`, outer-joined through the `song_tags` association table, filtered only on `Song.title` and `Song.artist`, and then serialized each song with `Song.to_dict()`. Since the filter did not use `song_tags` or `Tag`, the join was not needed for matching. Since `Song.to_dict()` reads tags through the model relationship, the join was not needed for serialization either.

### The root cause

The search query joined a one-to-many association table even though it only searched columns on `Song`. A song with multiple tag rows appears once per tag in the joined SQL result set. Any code path that serializes the joined rows directly, or any ORM behavior that does not uniquify entity identities, can show the same song multiple times. The duplicate-producing join was the root cause; tag serialization already came from the model relationship.

### Your fix and side-effect check

I removed the unnecessary `outerjoin(song_tags, ...)` and the unused `Tag`/`song_tags` imports. The query now searches `Song` rows directly by title or artist, which returns one row per song while preserving tag output through `Song.to_dict()`. I verified the side effects by running the search tests, including the existing multi-tag duplicate regression.
