"""Contract checks against WLED's documented JSON API routes and effect flags."""

import ai_chat
import light_gui
import mcp_light


def test_model_context_names_actual_wled_catalog_routes():
    knowledge = light_gui.system_knowledge_prompt()
    assert "/json/eff" in knowledge
    assert "/json/fxdata" in knowledge
    assert "/json/pal" in knowledge
    assert "/json/effects" not in knowledge
    assert "/json/palettes" not in knowledge


def test_model_effect_instructions_follow_live_catalog_markers():
    knowledge = light_gui.system_knowledge_prompt()
    tool_prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT
    schema = mcp_light.safe_effect_schema()["description"]
    for text in (knowledge, tool_prompt, schema):
        assert "🚫" in text or "blocked" in text
        assert "strobe/blink/flash/lightning/fireworks/sparkle" not in text
        assert "strobe, blink, flash, lightning" not in text
