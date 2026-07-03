"""
tests/test_feed.py — Mixtape

Tests for friends listening now feed logic.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services import feed_service
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_listening_now_excludes_yesterday_even_within_24_hours(app, monkeypatch):
    """Listening Now should only include very recent listens."""

    fixed_now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(feed_service, "datetime", FixedDateTime)

    with app.app_context():
        current_user = User(username="nova", email="nova@example.com")
        recent_friend = User(username="darius", email="darius@example.com")
        yesterday_friend = User(username="simone", email="simone@example.com")
        db.session.add_all([current_user, recent_friend, yesterday_friend])
        db.session.flush()

        db.session.execute(
            friendships.insert().values(user_id=current_user.id, friend_id=recent_friend.id)
        )
        db.session.execute(
            friendships.insert().values(user_id=current_user.id, friend_id=yesterday_friend.id)
        )

        recent_song = Song(title="Right Now", artist="The Seconds", shared_by=current_user.id)
        older_song = Song(title="Yesterday Loop", artist="Late Friend", shared_by=current_user.id)
        db.session.add_all([recent_song, older_song])
        db.session.flush()

        db.session.add(
            ListeningEvent(
                user_id=recent_friend.id,
                song_id=recent_song.id,
                listened_at=fixed_now - timedelta(minutes=10),
            )
        )
        db.session.add(
            ListeningEvent(
                user_id=yesterday_friend.id,
                song_id=older_song.id,
                listened_at=fixed_now - timedelta(hours=16),
            )
        )
        db.session.commit()

        feed = get_friends_listening_now(current_user.id)

        usernames = [entry["friend"]["username"] for entry in feed]
        assert usernames == ["darius"]

