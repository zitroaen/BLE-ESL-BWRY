"""The drawcustom renderer, without Home Assistant in the way."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from custom_components.esl_zhsunyco import drawcustom

WIDTH, HEIGHT = 184, 384

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)


def render(elements, **kwargs):
    return drawcustom.render_payload(elements, WIDTH, HEIGHT, **kwargs)


def test_an_empty_payload_gives_a_blank_panel() -> None:
    image = render([])
    assert image.size == (WIDTH, HEIGHT)
    assert image.getcolors() == [(WIDTH * HEIGHT, WHITE)]


def test_the_background_can_be_named_or_hex() -> None:
    assert render([], background="red").getpixel((0, 0)) == RED
    assert render([], background="#ffff00").getpixel((0, 0)) == YELLOW


def test_a_rectangle_lands_where_it_was_asked_to() -> None:
    image = render(
        [
            {
                "type": "rectangle",
                "x_start": 10,
                "y_start": 20,
                "x_end": 40,
                "y_end": 50,
                "fill": "red",
                "outline": None,
            }
        ]
    )
    assert image.getpixel((25, 35)) == RED
    assert image.getpixel((5, 35)) == WHITE


def test_percentages_are_read_as_a_share_of_the_panel() -> None:
    image = render(
        [
            {
                "type": "rectangle",
                "x_start": "0%",
                "y_start": "0%",
                "x_end": "50%",
                "y_end": "100%",
                "fill": "black",
            }
        ]
    )
    assert image.getpixel((WIDTH // 4, HEIGHT // 2)) == BLACK
    assert image.getpixel((WIDTH - 5, HEIGHT // 2)) == WHITE


def test_text_is_drawn_in_the_colour_it_asks_for() -> None:
    image = render(
        [{"type": "text", "value": "Hallo", "x": 0, "y": 0, "size": 40, "color": "red"}]
    )
    colours = {colour for _, colour in image.getcolors(100000)}
    assert RED in colours


def test_a_missing_y_stacks_below_the_element_before_it() -> None:
    """Two lines with no y must not land on top of each other."""
    elements = [
        {"type": "text", "value": "one", "x": 0, "y": 0, "size": 20},
        {"type": "text", "value": "two", "x": 0, "size": 20},
    ]
    image = render(elements).convert("L")
    rows = [
        y for y in range(HEIGHT) if min(image.getpixel((x, y)) for x in range(60)) < 128
    ]
    assert rows, "nothing was drawn"
    # A gap between the two lines proves the second one moved down.
    assert max(rows) - min(rows) > 20


def test_a_colour_tag_only_applies_when_asked_for() -> None:
    tagged = {"type": "text", "value": "a[red]b[/red]", "x": 0, "y": 0, "size": 30}
    plain = render([tagged])
    coloured = render([{**tagged, "parse_colors": True}])
    assert RED in {colour for _, colour in coloured.getcolors(100000)}
    assert RED not in {colour for _, colour in plain.getcolors(100000)}


def test_multiline_splits_on_its_delimiter() -> None:
    one = render(
        [{"type": "multiline", "value": "a|b|c", "delimiter": "|", "x": 0, "y": 0}]
    )
    other = render([{"type": "multiline", "value": "a", "x": 0, "y": 0}])
    assert one.tobytes() != other.tobytes()


def test_a_progress_bar_fills_from_the_left() -> None:
    image = render(
        [
            {
                "type": "progress_bar",
                "x_start": 0,
                "y_start": 0,
                "x_end": 100,
                "y_end": 20,
                "progress": 50,
                "fill": "red",
            }
        ]
    )
    assert image.getpixel((25, 10)) == RED
    assert image.getpixel((75, 10)) == WHITE


def test_an_icon_is_drawn_from_the_bundled_font() -> None:
    image = render(
        [{"type": "icon", "value": "mdi:cake-variant", "x": 0, "y": 0, "size": 48}]
    )
    black = dict((colour, count) for count, colour in image.getcolors(100000))
    assert black.get(BLACK, 0) > 50, "the icon glyph drew nothing"


def test_an_unknown_icon_name_says_so() -> None:
    with pytest.raises(drawcustom.DrawError, match="no Material Design Icon"):
        render([{"type": "icon", "value": "mdi:not-a-real-icon"}])


def test_a_qrcode_is_drawn() -> None:
    image = render([{"type": "qrcode", "data": "hello", "x": 0, "y": 0, "boxsize": 2}])
    assert BLACK in {colour for _, colour in image.getcolors(100000)}


def test_dlimg_pastes_what_the_caller_downloaded() -> None:
    source = Image.new("RGB", (20, 20), RED)
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    image = render(
        [{"type": "dlimg", "url": "https://example.com/a.png", "x": 5, "y": 5}],
        resources={"https://example.com/a.png": buffer.getvalue()},
    )
    assert image.getpixel((10, 10)) == RED
    assert image.getpixel((30, 30)) == WHITE


def test_dlimg_without_a_download_says_which_url_is_missing() -> None:
    with pytest.raises(drawcustom.DrawError, match="nothing was downloaded"):
        render([{"type": "dlimg", "url": "https://example.com/a.png"}])


def test_an_invisible_element_is_skipped() -> None:
    hidden = {
        "type": "rectangle",
        "x_start": 0,
        "y_start": 0,
        "x_end": 50,
        "y_end": 50,
        "fill": "red",
        "visible": False,
    }
    assert render([hidden]).getcolors() == [(WIDTH * HEIGHT, WHITE)]


def test_rotating_swaps_the_canvas_so_the_layout_still_fits() -> None:
    """A layout designed landscape must not be cropped on a tall panel."""
    image = render(
        [
            {
                "type": "rectangle",
                "x_start": 0,
                "y_start": 0,
                "x_end": "100%",
                "y_end": "100%",
                "fill": "red",
            }
        ],
        rotate=90,
    )
    assert image.size == (WIDTH, HEIGHT)
    assert image.getpixel((WIDTH - 2, HEIGHT - 2)) == RED


def test_halftones_become_a_mixed_colour_for_the_ditherer() -> None:
    assert drawcustom.parse_color("half_black") == (128, 128, 128)
    assert drawcustom.parse_color("hr") == (255, 128, 128)
    assert drawcustom.parse_color("accent") == RED


# --- payload handling -----------------------------------------------------


def test_a_bare_list_is_a_payload_with_no_options() -> None:
    elements, options = drawcustom.normalise([{"type": "text", "value": "a"}])
    assert options == {}
    assert elements[0]["type"] == "text"


def test_a_service_call_block_is_unwrapped() -> None:
    elements, options = drawcustom.normalise(
        {"background": "red", "rotate": 90, "payload": [{"type": "text", "value": "a"}]}
    )
    assert options == {"background": "red", "rotate": 90}
    assert len(elements) == 1


def test_a_whole_export_with_a_data_block_is_unwrapped() -> None:
    elements, _ = drawcustom.normalise(
        {
            "service": "open_epaper_link.drawcustom",
            "data": {"payload": [{"type": "text", "value": "a"}]},
        }
    )
    assert len(elements) == 1


def test_json_text_is_parsed() -> None:
    elements, _ = drawcustom.normalise('[{"type": "text", "value": "a"}]')
    assert elements[0]["value"] == "a"


def test_broken_json_says_it_is_broken() -> None:
    with pytest.raises(drawcustom.DrawError, match="not valid JSON"):
        drawcustom.normalise("[{")


def test_validate_rejects_an_unknown_type() -> None:
    with pytest.raises(drawcustom.DrawError, match="unknown type"):
        drawcustom.validate([{"type": "sparkline"}])


def test_validate_explains_why_plot_is_missing() -> None:
    with pytest.raises(drawcustom.DrawError, match="recorder history"):
        drawcustom.validate([{"type": "plot"}])


def test_validate_names_the_element_that_is_wrong() -> None:
    with pytest.raises(drawcustom.DrawError, match="element 1"):
        drawcustom.validate(
            [{"type": "text", "value": "a"}, {"type": "circle", "x": 1}]
        )


def test_validate_catches_a_colour_that_does_not_exist() -> None:
    with pytest.raises(drawcustom.DrawError, match="unknown colour"):
        drawcustom.validate([{"type": "text", "value": "a", "color": "turquoise"}])


def test_every_supported_type_renders() -> None:
    """A smoke test over the whole element vocabulary."""
    source = BytesIO()
    Image.new("RGB", (10, 10), RED).save(source, format="PNG")
    elements = [
        {"type": "debug_grid", "spacing": 50},
        {"type": "text", "value": "text", "x": 0, "y": 0},
        {"type": "multiline", "value": "a\nb", "x": 0, "y": 30},
        {"type": "line", "x_start": 0, "y_start": 60, "x_end": 100, "y_end": 60},
        {"type": "rectangle", "x_start": 0, "y_start": 70, "x_end": 40, "y_end": 90},
        {
            "type": "rectangle_pattern",
            "x_start": 50,
            "y_start": 70,
            "x_size": 10,
            "y_size": 10,
            "x_repeat": 3,
            "y_repeat": 2,
            "x_offset": 2,
            "y_offset": 2,
        },
        {"type": "polygon", "points": [[0, 100], [20, 120], [0, 140]]},
        {"type": "circle", "x": 60, "y": 120, "radius": 10},
        {"type": "ellipse", "x_start": 80, "y_start": 110, "x_end": 120, "y_end": 130},
        {
            "type": "arc",
            "x_start": 130,
            "y_start": 110,
            "x_end": 170,
            "y_end": 150,
            "start": 0,
            "end": 180,
        },
        {
            "type": "progress_bar",
            "x_start": 0,
            "y_start": 160,
            "x_end": 150,
            "y_end": 180,
            "progress": 30,
            "show_percentage": True,
        },
        {"type": "icon", "value": "mdi:home", "x": 0, "y": 190, "size": 24},
        {
            "type": "icon_sequence",
            "icons": ["mdi:home", "mdi:wifi"],
            "x": 40,
            "y": 190,
            "size": 24,
        },
        {"type": "qrcode", "data": "x", "x": 0, "y": 220},
        {"type": "dlimg", "url": "u", "x": 120, "y": 220, "xsize": 20, "ysize": 20},
    ]
    drawcustom.validate(elements)
    image = render(elements, resources={"u": source.getvalue()})
    assert image.size == (WIDTH, HEIGHT)


def test_collect_urls_finds_the_downloads() -> None:
    assert drawcustom.collect_urls(
        [
            {"type": "dlimg", "url": "a"},
            {"type": "text", "value": "b"},
            {"type": "dlimg", "url": "a"},
            {"type": "dlimg", "url": "c"},
        ]
    ) == ["a", "c"]
