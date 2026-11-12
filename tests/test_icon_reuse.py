"""Repeated native icons reuse rendering without sharing mutable QIcons."""
import pytest
from PySide6.QtGui import QColor, QPixmap

from baseskiff.ui import icons


@pytest.fixture
def rendered_icons(qapp, monkeypatch):
    renderer = icons.QSvgRenderer
    rendered = []

    def record_renderer(svg):
        rendered.append(bytes(svg))
        return renderer(svg)

    monkeypatch.setattr(icons, 'QSvgRenderer', record_renderer)
    return rendered


def pixels(value, size=14):
    return value.pixmap(size, size).toImage()


def test_repeated_icons_and_unknown_name_fallback_share_one_render(rendered_icons):
    expected = icons.icon('database', '#126e37', 14)
    repeated = icons.icon('database', '#126e37', 14)
    fallback = icons.icon('not-an-icon', '#126e37', 14)

    assert not expected.isNull()
    assert pixels(expected) == pixels(repeated) == pixels(fallback)
    assert len(rendered_icons) == 1


def test_mutating_returned_icon_cannot_replace_later_icons(rendered_icons):
    changed = icons.icon('key', '#237f48', 14)
    original = pixels(changed)
    replacement = QPixmap(28, 28)
    replacement.fill(QColor('#ff00ff'))
    replacement.setDevicePixelRatio(2)
    changed.addPixmap(replacement)

    assert pixels(changed) != original
    assert pixels(icons.icon('key', '#237f48', 14)) == original
    assert len(rendered_icons) == 1


def test_visual_variants_keep_shape_color_and_native_size(rendered_icons):
    green = icons.icon('key', '#348f59', 14)
    blue = icons.icon('key', '#348faf', 14)
    folder = icons.icon('folder', '#348f59', 14)
    larger = icons.icon('key', '#348f59', 20)

    assert pixels(green) != pixels(blue)
    assert pixels(green) != pixels(folder)
    assert green.availableSizes()[0].toTuple() == (28, 28)
    assert larger.availableSizes()[0].toTuple() == (40, 40)
    assert green.pixmap(14, 14).devicePixelRatio() == 1
    assert len(rendered_icons) == 4


def test_icon_reuse_evicts_old_variants_instead_of_growing_forever(rendered_icons):
    original = pixels(icons.icon('key', '#450000', 14))
    icons.icon('key', '#450000', 14)
    assert len(rendered_icons) == 1

    for index in range(512):
        icons.icon('key', f'#{0x460000 + index:06x}', 14)
    before = len(rendered_icons)

    assert pixels(icons.icon('key', '#450000', 14)) == original
    assert len(rendered_icons) == before + 1
