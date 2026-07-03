"""
tests/test_notifications.py — Mixtape

Tests for notification side effects.
"""

import pytest

from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_rating_someone_elses_song_notifies_original_sharer(app):
    """Rating another user's shared song should create a notification."""
    with app.app_context():
        owner = User(username="nova", email="nova@example.com")
        rater = User(username="darius", email="darius@example.com")
        db.session.add_all([owner, rater])
        db.session.flush()

        song = Song(title="Shared Track", artist="Open Channel", shared_by=owner.id)
        db.session.add(song)
        db.session.commit()

        rate_song(rater.id, song.id, 5)

        notification = db.session.query(Notification).filter_by(user_id=owner.id).one()
        assert notification.notification_type == "song_rated"
        assert "darius rated your song 'Shared Track'" in notification.body


def test_rating_own_song_does_not_notify_self(app):
    """Users should not receive notifications for rating their own song."""
    with app.app_context():
        owner = User(username="nova", email="nova@example.com")
        db.session.add(owner)
        db.session.flush()

        song = Song(title="Self Track", artist="Open Channel", shared_by=owner.id)
        db.session.add(song)
        db.session.commit()

        rate_song(owner.id, song.id, 4)

        notifications = db.session.query(Notification).filter_by(user_id=owner.id).all()
        assert notifications == []

