"""Redis search history is local, scoped, bounded, and restart-safe."""

import pytest

from baseskiff.service import DatabaseService


def test_search_history_round_trips_and_deduplicates_by_scope_and_text(tmp_path):
    service = DatabaseService(tmp_path)
    first = service.record_redis_search("redis-a", "2", "用户:* ", "prefix")
    assert first["text"] == "用户:* "
    assert first["mode"] == "prefix"

    updated = service.record_redis_search("redis-a", "2", "用户:* ", "exact")
    assert updated["id"] == first["id"]
    assert updated["mode"] == "exact"
    assert service.list_redis_search_history("redis-a", "2") == [updated]
    assert service.list_redis_search_history("redis-a", "3") == []
    assert service.list_redis_search_history("redis-b", "2") == []

    reopened = DatabaseService(tmp_path)
    assert reopened.list_redis_search_history("redis-a", "2")[0]["text"] == "用户:* "


def test_search_history_keeps_favorites_beyond_one_hundred_and_evicts_oldest(tmp_path):
    service = DatabaseService(tmp_path)
    favorite = service.record_redis_search("redis", "0", "keep", "prefix")
    assert service.set_redis_search_favorite(favorite["id"], True)
    for index in range(101):
        service.record_redis_search("redis", "0", f"key:{index}", "prefix")

    entries = service.list_redis_search_history("redis", "0")
    assert len(entries) == 101
    assert {entry["text"] for entry in entries} >= {"keep", "key:99"}
    assert "key:0" not in {entry["text"] for entry in entries}
    assert service.set_redis_search_favorite(favorite["id"], False)
    service.record_redis_search("redis", "0", "newest", "exact")
    entries = service.list_redis_search_history("redis", "0")
    assert len(entries) == 100
    assert "keep" not in {entry["text"] for entry in entries}


def test_search_history_can_be_deleted_and_empty_text_is_ignored(tmp_path):
    service = DatabaseService(tmp_path)
    assert service.record_redis_search("redis", "0", "", "prefix") is None
    entry = service.record_redis_search("redis", "0", "key", "prefix")
    assert service.delete_redis_search_history(entry["id"])
    assert service.list_redis_search_history("redis", "0") == []
    assert not service.delete_redis_search_history(entry["id"])


@pytest.mark.parametrize("mode", ["fuzzy", "", None])
def test_search_history_rejects_unknown_modes(tmp_path, mode):
    service = DatabaseService(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        service.record_redis_search("redis", "0", "key", mode)


def test_legacy_metadata_without_search_table_is_migrated(tmp_path):
    service = DatabaseService(tmp_path)
    service.record_redis_search("redis", "0", "old", "prefix")
    with service._metadata() as db:
        db.execute("DROP TABLE redis_searches")
    migrated = DatabaseService(tmp_path)
    assert migrated.list_redis_search_history("redis", "0") == []
