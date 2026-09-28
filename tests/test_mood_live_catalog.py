from types import SimpleNamespace
from unittest.mock import patch

import pytest
import light_gui
import mcp_light


def controller():
    effects = ['Solid'] * 220
    effects[56] = 'Tri Fade'
    effects[1] = 'Blink'
    return SimpleNamespace(host='http://mood-regression.test', get_device_snapshot=lambda: {'effects': effects, 'fxdata': [], 'state': {}})


def test_mood_accepts_tri_fade_from_live_controller_catalog():
    client = controller()
    plan = {'actions': [{'action': 'effect', 'effect': 56, 'red': 90, 'green': 15, 'blue': 110}]}
    with patch.dict(mcp_light._fx_allowed, {}, clear=True), patch.object(light_gui, 'call_openai_for_plan', return_value=plan):
        payload = light_gui.generate_mood_for_song(client, {'title': 'Ride', 'artist': 'twenty one pilots'})
    assert payload['seg'][0]['fx'] == 56
    assert payload['seg'][0]['col'][0][:3] == [90, 15, 110]


def test_mood_preserves_live_controller_blink_effect():
    plan = {'actions': [{'action': 'effect', 'effect': 1}]}
    with patch.dict(mcp_light._fx_allowed, {}, clear=True), patch.object(light_gui, 'call_openai_for_plan', return_value=plan):
        assert light_gui.generate_mood_for_song(controller(), {'title': 'Ride'})['seg'][0]['fx'] == 1


def test_mood_still_rejects_controller_effect_marked_strobe():
    client = controller()
    effects = client.get_device_snapshot()['effects']
    effects[1] = 'Strobe'
    plan = {'actions': [{'action': 'effect', 'effect': 1}]}
    with patch.dict(mcp_light._fx_allowed, {}, clear=True), patch.object(light_gui, 'call_openai_for_plan', return_value=plan):
        with pytest.raises(ValueError):
            light_gui.generate_mood_for_song(client, {'title': 'Ride'})


def test_mood_without_catalog_cannot_inherit_unrelated_client_policy():
    client = SimpleNamespace(get_fleet_snapshot=lambda: {'devices': {'left': {'state': {}}}})
    with patch.dict(mcp_light._fx_allowed, {'': {0, 1}}, clear=True), patch.object(light_gui, 'call_openai_for_plan', return_value={'actions':[{'action':'effect','effect':28}]}):
        assert light_gui.generate_mood_for_song(client, {'title':'Test'})['seg'][0]['fx'] == 28
