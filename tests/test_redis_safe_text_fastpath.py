"""Ordinary key labels avoid per-character escaping and retain edge semantics."""
import pytest

from baseskiff import redis_codec


@pytest.mark.parametrize('value, expected', [
    ('', ''),
    (b'', ''),
    ('app:tenant:001:profile', 'app:tenant:001:profile'),
    ('用户:订单:😀', '用户:订单:😀'),
    ('  literal \\ : [] * ?  ', '  literal \\ : [] * ?  '),
    ('用户:订单'.encode(), '用户:订单'),
    ('é e\u0301', 'é e\u0301'),
    ('x' * 4080, 'x' * 4080),
])
def test_short_printable_key_labels_skip_character_escape(monkeypatch, value, expected):
    escape = redis_codec._escape
    escaped = []

    def record_escape(*args, **kwargs):
        escaped.append(args[0])
        return escape(*args, **kwargs)

    monkeypatch.setattr(redis_codec, '_escape', record_escape)

    assert redis_codec.safe_text(value) == expected
    assert escaped == []


@pytest.mark.parametrize('value, expected', [
    ('a\x00b\n\r\t\ud800', r'a\x00b\n\r\t\ud800'),
    ('a\u00a0b\u200dc', r'a\xa0b\u200dc'),
    ('a\u2028b\u2029c', r'a\u2028b\u2029c'),
    (b'a\x00', '<binary: 61 00; 2 bytes>'),
    (b'a\n', '<binary: 61 0a; 2 bytes>'),
    (b'\xff', '<binary: ff; 1 bytes>'),
    ('\u00a0'.encode(), '<binary: c2 a0; 2 bytes>'),
    ('x' * 4081, 'x' * 4080 + '… (truncated)'),
    (b'x' * 4081, 'x' * 4080 + '… (truncated)'),
    ('😀' * 4081, '😀' * 4080 + '… (truncated)'),
    ('x' * 4079 + '\x00', 'x' * 4079 + '… (truncated)'),
    ('x' * 4076 + '\x00', 'x' * 4076 + r'\x00'),
])
def test_fast_labels_keep_control_binary_and_truncation_semantics(value, expected):
    assert redis_codec.safe_text(value) == expected
