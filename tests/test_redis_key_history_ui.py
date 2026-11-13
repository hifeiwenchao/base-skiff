"""Redis search history is local, scoped and submitted explicitly."""
from types import SimpleNamespace

pytest_plugins = ("tests.test_redis_key_search_ui",)


def history_item(identifier, text, mode="prefix", favorite=False):
    return SimpleNamespace(id=identifier, text=text, mode=mode,
                           is_favorite=favorite, last_used_at="2026-11-13T00:00:00+00:00")


def test_search_draft_does_not_record_until_explicit_submission(window, monkeypatch):
    recorded = []
    monkeypatch.setattr(window.service, "record_redis_search",
                        lambda *args: recorded.append(args))
    monkeypatch.setattr(window, "_refresh_redis_keys", lambda: None)

    window.redis_key_search.setText("team:")
    assert recorded == []
    window._search_redis_keys()

    assert recorded == [(window.current_connection.id, "2", "team:", "prefix")]


def test_history_completion_uses_literal_prefix_and_restores_mode(window, monkeypatch):
    entries = [history_item("one", "team:cart", "exact"),
               history_item("two", "other:key", "prefix")]
    monkeypatch.setattr(window.service, "list_redis_search_history",
                        lambda *args: entries)
    scope = window._redis_scope(window.current_connection)
    window._redis_search_history_loaded.discard(scope)
    window._load_redis_search_history(scope)
    window.redis_key_search.setText("team")
    window._refresh_redis_search_completer()

    assert window.redis_key_history_model.stringList() == ["team:cart", "other:key"]
    window._select_redis_search_history(entries[0])
    assert window.redis_key_search.text() == "team:cart"
    assert window.redis_key_mode.currentData() == "exact"


def test_history_favorite_and_delete_update_local_menu_state(window, monkeypatch):
    entry = history_item("one", "team:cart")
    scope = window._redis_scope(window.current_connection)
    window._redis_search_history[scope] = [entry]
    window._redis_search_history_loaded.add(scope)
    calls = []
    monkeypatch.setattr(window.service, "set_redis_search_favorite",
                        lambda *args: calls.append(("favorite", args)) or True)
    monkeypatch.setattr(window.service, "delete_redis_search_history",
                        lambda *args: calls.append(("delete", args)) or True)

    window._toggle_redis_search_favorite(entry)
    assert entry.is_favorite is True
    window._delete_redis_search_history(entry)
    assert window._redis_search_history[scope] == []
    assert [kind for kind, _args in calls] == ["favorite", "delete"]
