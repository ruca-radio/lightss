"""Actual audio parser -> renderer -> fleet handoff, with network output captured."""
from datetime import datetime
import threading
import time
import fleet
import lightctl
import realtime
import smart_director
import wled_audio


class RecordingClient(lightctl.LightClient):
    def __init__(self, host):
        super().__init__(host,dry_run=True)
        self.posts=[]
    def post_state(self,payload):
        self.posts.append(payload)
        return {'success':True}


class Capture:
    def __init__(self):self.packets=[];self.closed=False;self.ready=threading.Event()
    def sendto(self,data,address):
        self.packets.append((bytes(data),address))
        if len(self.packets)>=8:self.ready.set()
    def close(self):self.closed=True


def test_real_audio_to_ddp_then_manual_and_night_tv(monkeypatch):
    installation,controllers=fleet.load_topology({})
    clients={c.name:RecordingClient(c.host) for c in controllers}
    wall=fleet.LightFleet(clients,controllers,installation=installation)
    listener=wled_audio.WledAudioListener()
    packet=wled_audio.PACKET_V2_STRUCT.pack(wled_audio.HEADER_V2,0,0,160.,140.,1,0,bytes([220,180,120,80]*4),0,100.,220.)
    listener.feed_packet(packet)
    transport=Capture()
    director=smart_director.SmartDirector(wall,{'enabled':True,'mode':'music'},listener=listener,
        renderer_factory=lambda *a,**kw:realtime.AudioReactiveRunner(*a,**kw,transport=transport),
        wall_clock=lambda:datetime(2026,9,12,23))
    monkeypatch.setattr(smart_director,'_director',director)
    try:
        director.step({}, {}, listener.get_snapshot())
        assert transport.ready.wait(2),director.status()
        assert director.status()['mode']=='music'
        assert director.status()['renderer']['audio_active'] is True
        assert len({address[0] for _,address in transport.packets})==2
        assert len({packet[10:] for packet,_ in transport.packets})>2
        wall.post_state({'on':True,'bri':20,'seg':[{'fx':0,'col':[[30,40,50]]}]})
        assert director.status()['mode']=='manual'
        assert transport.closed
        count=len(transport.packets);time.sleep(.05)
        assert len(transport.packets)==count
        director.configure({'mode':'tv'})
        director.step({}, {}, listener.get_snapshot())
        for client in clients.values():
            state=client.posts[-1]
            assert state['bri']==13
            assert len(state['seg'])==2
            assert all(seg['fx']==0 and seg['frz'] is False for seg in state['seg'])
    finally:director.shutdown()
