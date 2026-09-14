import typing
from unittest.mock import Mock

import pytest
import ai_chat
import light_gui
import lightctl
import mcp_light


@pytest.fixture(autouse=True)
def clean_policy():
    mcp_light._fx_allowed.clear()
    yield
    mcp_light._fx_allowed.clear()


def test_all_forbidden_refresh_revokes_previous_allowlist():
    client = lightctl.LightClient('http://example.test', dry_run=True)
    mcp_light.seed_effect_catalog(client.host, ['Solid', 'Breathe'])
    mcp_light.seed_effect_catalog(client.host, ['Strobe', 'Blink'])
    assert mcp_light.allowed_effects_for(client, 'all') == set()


def test_failed_refresh_cannot_reuse_old_catalog():
    client = lightctl.LightClient('http://example.test', dry_run=True)
    mcp_light.seed_effect_catalog(client.host, ['Solid','Unusual'])
    mcp_light.seed_effect_catalog(client.host, None)
    assert mcp_light.allowed_effects_for(client, 'all') is None


def test_raw_write_cannot_bypass_offline_effect_safety():
    client = lightctl.LightClient('http://example.test', dry_run=True)
    client.post_state = Mock()
    with pytest.raises(ValueError):
        mcp_light.call_tool(client, 'wled_write', {'payload': {'seg': [{'fx': 1}]}}, Mock())
    client.post_state.assert_not_called()


def test_different_controller_catalogs_are_both_explained():
    devices = {name: {'state': {'on':True, 'seg':[]}, 'info':{}, 'effects':['Solid',effect]} for name,effect in [('left','Breathe'),('right','OceanOnlyOnRight')]}
    text = light_gui.device_snapshot_text(devices)
    assert 'OceanOnlyOnRight' in text
    assert 'Breathe' in text


def test_same_catalog_is_not_repeated_in_prompt():
    devices = {name: {'state': {'on':True,'seg':[]}, 'info':{}, 'effects':['Solid','UniqueSharedEffect']} for name in ['left','right']}
    text = light_gui.device_snapshot_text(devices)
    assert text.count('UniqueSharedEffect') == 1


def test_snapshot_distinguishes_unavailable_state_from_power_off():
    text = light_gui.device_snapshot_text({'state':{'error':'offline'},'info':{},'effects':[]})
    assert 'power=off' not in text
    assert 'offline' in text


def test_effect_history_type_annotations_resolve():
    assert typing.get_type_hints(lightctl.record_fx_use)['fx_id'] is typing.Any


def test_prompt_does_not_override_runtime_orientation():
    assert 'pixel 0 is bottom.' not in ai_chat.TOOL_CHAT_SYSTEM_PROMPT


def test_player_search_no_match_stays_empty():
    import audio_player
    assert audio_player._filter_query([{'title':'Alpha'}], 'does not exist') == []
