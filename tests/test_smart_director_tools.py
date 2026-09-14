"""Realtime tool handoff must stop the smart DDP owner before another starts."""
from unittest.mock import Mock
import pytest
import fleet
import lightctl
import mcp_light
import smart_director

@pytest.fixture
def wall():
    installation,controllers=fleet.load_topology({})
    return fleet.LightFleet({c.name:lightctl.LightClient(c.host,dry_run=True) for c in controllers},controllers,installation=installation)

@pytest.mark.parametrize('name,args,target',[
    ('realtime_start',{},'realtime'),('start_show',{'show':{}},'show'),
])
def test_stream_start_hands_off_before_output(monkeypatch,wall,name,args,target):
    calls=[]
    monkeypatch.setattr(smart_director,'before_external_write',lambda client:calls.append(('handoff',client)))
    monkeypatch.setattr(mcp_light.realtime,'realtime_start',lambda *a,**kw:calls.append(('realtime',wall)) or 'started')
    monkeypatch.setattr(mcp_light.shows,'start_show',lambda *a,**kw:calls.append(('show',wall)) or 'started')
    mcp_light.call_tool(wall,name,args,Mock())
    assert calls==[('handoff',wall),(target,wall)]

def test_preview_and_status_do_not_pause_smart(monkeypatch,wall):
    pause=Mock();monkeypatch.setattr(smart_director,'before_external_write',pause)
    monkeypatch.setattr(mcp_light.realtime,'realtime_status',lambda:{})
    monkeypatch.setattr(mcp_light.look_agents,'design_look',lambda *a,**kw:{'colors':[]})
    mcp_light.call_tool(wall,'realtime_status',{},Mock())
    mcp_light.call_tool(wall,'design_look',{'prompt':'calm','run':False},Mock())
    pause.assert_not_called()

def test_applied_design_hands_off_before_output(monkeypatch,wall):
    calls=[]
    monkeypatch.setattr(smart_director,'before_external_write',lambda client:calls.append('handoff'))
    monkeypatch.setattr(mcp_light.look_agents,'design_look',lambda *a,**kw:{'colors':[]})
    monkeypatch.setattr(mcp_light.look_agents,'apply_look',lambda *a,**kw:calls.append('render') or 'started')
    mcp_light.call_tool(wall,'design_look',{'prompt':'calm','run':True},Mock())
    assert calls==['handoff','render']
