"""The rendered page must actually run the live-pixel visualizer wiring."""

import light_gui


def test_page_starts_preview_polling_and_rebuilds_ranges_on_state_updates():
    html = light_gui.render_html(player_enabled=True)
    assert "setInterval(pollLedPreview, 300);" in html
    # SSE state updates carry the full fleet map; ranges must be rebuilt there.
    assert html.count("rebuildChannelRanges(payload.state);") >= 1
    assert html.count("rebuildChannelRanges(data.state);") >= 1


def test_state_readout_badges_the_preview_source():
    html = light_gui.render_html(player_enabled=True)
    assert "function peekPreviewFresh()" in html
    assert "peekPreviewFresh() ? 'Live pixels' : 'Approx preview'" in html


def test_strip_frame_prefers_ddp_then_peek_pixels():
    html = light_gui.render_html(player_enabled=True)
    body = html[html.index("function updateStripFrame") : html.index("function updateLightPreview")]
    assert "const ddpPixels = controllerPreviewColors();" in body
    assert "livePeekColors()" in body


def test_led_preview_endpoint_registered_for_plain_get():
    assert "/api/led-preview" in light_gui.API_GET_PATHS
