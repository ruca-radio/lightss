import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import light_gui


def test_audio_sse_stream_has_low_latency_without_player_or_director():
    class Monitor:
        sequence = 0
        def get_snapshot(self):
            self.sequence += 1
            return {'active': True, 'receive_sequence': self.sequence, 'level': .5, 'fft': [.2] * 16}
    state = SimpleNamespace(controller_audio=Monitor(), _closed=False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), light_gui.make_handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/api/audio/events'
        with urllib.request.urlopen(url, timeout=2) as response:
            assert response.headers['Content-Type'] == 'text/event-stream'
            arrivals = []
            values = []
            while len(values) < 3:
                line = response.readline().decode().strip()
                if line.startswith('data: '):
                    arrivals.append(time.monotonic())
                    values.append(json.loads(line[6:])['audio'])
            assert values[-1]['receive_sequence'] > values[0]['receive_sequence']
            assert arrivals[-1] - arrivals[0] < .15
            assert light_gui.AUDIO_STREAM_INTERVAL_S <= 1 / 60
    finally:
        state._closed = True
        server.shutdown()
        server.server_close()
