"""Explicit X11 integration check; no real accounts, LEDs, or personal profiles.

Run: xvfb-run -a .venv/bin/python tests/native_chrome_smoke.py --output-dir /tmp/lightss-native-check
Optional --package-root points at an extracted wheel/deb site-packages directory.
Native QR screenshots require visual review; this does not authenticate a phone.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import wave
from functools import partial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--package-root')
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(Path(args.package_root).resolve() if args.package_root else repo))
    with tempfile.TemporaryDirectory(prefix='lightss-native-check-') as temporary:
        home = Path(temporary)
        runtime = home / 'runtime'
        runtime.mkdir(mode=0o700)
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home/'config'),
                          XDG_DATA_HOME=str(home/'data'), XDG_RUNTIME_DIR=str(runtime), QT_QPA_PLATFORM='xcb')
        return check(repo, home, output)


def check(repo, home, output):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
    import chrome_provider
    import desktop_bridge
    import light_desktop
    import light_gui
    import runpy

    (home/'index.html').write_text('''<!doctype html><meta charset="utf-8"><title>Lightss passkey fixture</title>
<style>body{font:20px sans-serif;padding:30px}button{font:inherit;padding:18px}</style>
<h1>Lightss native phone-passkey check</h1><p>Controlled test origin — no provider account involved.</p>
<button id="start">Use phone passkey</button><pre id="result"></pre>
<script>start.onclick=async()=>{window.authAbort=new AbortController();try{
await navigator.credentials.get({signal:authAbort.signal,publicKey:{challenge:crypto.getRandomValues(new Uint8Array(32)),
rpId:'localhost',allowCredentials:[{type:'public-key',id:crypto.getRandomValues(new Uint8Array(32)),transports:['hybrid']}],
userVerification:'required',timeout:120000}});result.textContent='completed'}catch(e){result.textContent=e.name}};</script>''')
    with wave.open(str(home/'silence.wav'), 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0' * 8000 * 60)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(home)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://localhost:{server.server_port}'
    # Test-only exceptions for one controlled fixture origin. Production denies loopback.
    light_desktop.PROVIDER_HOME.update(youtube_music=origin+'/', apple_music=origin+'/')
    original_policy = chrome_provider.request_allowed
    chrome_provider.request_allowed = lambda source, url, main_frame: url.startswith(origin+'/') or original_policy(source, url, main_frame=main_frame)
    original_url = desktop_bridge.provider_url_allowed
    desktop_bridge.provider_url_allowed = lambda source, url: isinstance(url, str) and (url.startswith(origin+'/') or original_url(source, url))
    make_fleet = runpy.run_path(str(repo/'tests/test_calibration_service.py'))['make_fleet']
    original_backend = light_desktop.BackendServer

    class FixtureBackend(original_backend):
        def __init__(self):
            super().__init__(state_factory=lambda: light_gui.GuiState(make_fleet(), dry_run=True))

    light_desktop.BackendServer = FixtureBackend
    app = QApplication([])
    result = {'ok': False}
    state = {'phase': 0, 'deadline': time.monotonic()+35}
    owned = []

    def fail(error):
        result['error'] = str(error)
        app.quit()

    def later(fn, delay=700):
        def guarded():
            try:
                fn()
            except Exception as error:
                fail(error)
        QTimer.singleShot(delay, guarded)

    def screenshot(name):
        assert app.primaryScreen().grabWindow(0).save(str(output/name))

    def tick():
        try:
            if time.monotonic() > state['deadline']:
                raise TimeoutError('Native player check timed out')
            if state['phase']:
                return
            window = next((w for w in app.topLevelWidgets() if isinstance(w, QMainWindow)), None)
            if window is None:
                return
            tabs = window.findChild(QTabWidget)
            views = [tabs.widget(i) for i in (1, 2)]
            if not all(getattr(view, 'container', None) is not None for view in views):
                return
            owned.extend(view.worker.transport.process for view in views)
            state.update(phase=1, window=window, tabs=tabs, views=views)
            tabs.setCurrentIndex(1)
            views[0].runJavaScript('document.getElementById("start").click()')
            later(first_qr, 1800)
        except Exception as error:
            fail(error)

    def first_qr():
        screenshot('youtube-tab-qr.png')
        state['window'].resize(1250, 850)
        state['tabs'].setCurrentIndex(0)
        later(lambda: (state['tabs'].setCurrentIndex(1), later(cancel_first)), 300)

    def cancel_first():
        screenshot('resized-qr.png')
        state['views'][0].runJavaScript('window.authAbort.abort()')
        later(check_cancel)

    def check_cancel():
        def received(value):
            if value != 'AbortError':
                fail('Native QR cancellation failed')
                return
            state['tabs'].setCurrentIndex(2)
            state['views'][1].runJavaScript('document.getElementById("start").click()')
            later(second_qr, 1200)
        state['views'][0].runJavaScript('document.getElementById("result").textContent', received)

    def second_qr():
        screenshot('apple-tab-qr.png')
        state['views'][1].runJavaScript('window.authAbort.abort()')
        state['tabs'].setCurrentIndex(1)
        state['views'][0].runJavaScript('''fetch('/silence.wav').then(r=>r.blob()).then(blob => { const audio=document.createElement('audio');
audio.src=URL.createObjectURL(blob);audio.muted=true;audio.dataset.videoId='fixture-track';document.body.append(audio);
navigator.mediaSession.metadata=new MediaMetadata({title:'Fixture track',artist:'Fixture artist'}); })''')
        later(start_audio)

    def start_audio():
        desktop_bridge.registry.command('youtube_music', 'play')
        later(seek_audio, 700)

    def seek_audio():
        desktop_bridge.registry.command('youtube_music', 'seek', {'position': 42})
        later(check_clock, 1000)

    def check_clock():
        snapshot = desktop_bridge.registry.snapshot('youtube_music')
        clock = light_gui.playback_clock_payload()['clock']
        assert snapshot['playing'] and 42 <= snapshot['position'] < 47, snapshot
        assert clock['source'] == 'desktop_player' and 42 <= clock['position_s'] < 47, clock
        assert snapshot['track_id'] == 'fixture-track'
        result.update(clock=clock, engines=[view.engine for view in state['views']])
        desktop_bridge.registry.command('youtube_music', 'pause')
        later(check_pause)

    def check_pause():
        assert not desktop_bridge.registry.snapshot('youtube_music')['playing']
        result['ok'] = True
        app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    try:
        code = light_desktop.run_desktop(provider_engine='chrome')
        result['clean_shutdown'] = bool(owned) and all(process.poll() is not None for process in owned)
        result['ok'] = result['ok'] and result['clean_shutdown'] and code == 0
    finally:
        server.shutdown()
        server.server_close()
    (output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
