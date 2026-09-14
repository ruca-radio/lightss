"""Full Chrome provider tabs. Browser I/O stays off the Qt GUI thread.

Only app-owned scripts execute, only on the music origin; authentication stays in
Chrome's native UI and private profile. No browser protocol is exposed over HTTP.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


CHROME_HELP = (
    "Provider tabs use full Chrome with separate Lightss profiles. For a phone passkey, "
    "choose another device / phone or tablet in the provider's sign-in flow, then scan "
    "Chrome's QR code. Keep Bluetooth enabled on the computer and phone. "
    "Your normal Chrome and older Qt sessions are separate; sign in again here. "
    "Provider account policies may still restrict embedded/controlled browser sessions."
)


def find_chrome_binary(mode='auto', *, environ=None, platform=None, which=None):
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    if mode not in {'auto', 'chrome', 'qt'}:
        raise ValueError('Unknown provider engine')
    if mode == 'qt':
        return None
    available = platform.startswith('linux') and bool(env.get('DISPLAY')) and env.get('QT_QPA_PLATFORM', 'xcb').split(':')[0] == 'xcb'
    binary = next((path for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser') if (path := which(name))), None) if available else None
    if binary is None and mode == 'chrome':
        raise RuntimeError('Native provider tabs require Chrome/Chromium and an X11/XWayland display (QT_QPA_PLATFORM=xcb).')
    return binary


def request_allowed(source, url, *, main_frame):
    from light_desktop import _private_or_local_url, provider_navigation_allowed
    if url == 'about:blank':
        return True
    try:
        if _private_or_local_url(url):
            return False
        if main_frame:
            return provider_navigation_allowed(source, url)
        return urlsplit(url).scheme in {'https', 'http', 'data', 'blob'}
    except (TypeError, ValueError):
        return False


def guarded_script(source, script):
    from light_desktop import PROVIDER_HOME
    origin = PROVIDER_HOME[source].rstrip('/')
    return f'(() => {{ if (location.origin !== {json.dumps(origin)}) return null; return ({script.rstrip().rstrip(";")}); }})()'


class BrowserWorker:
    def __init__(self, source, binary, profile, complete, ready, failed, *, max_pending=64, navigation=None, window_finder=None):
        self.source, self.binary, self.profile = source, binary, Path(profile)
        self.navigation = navigation or (lambda epoch: None)
        self.epoch = 0
        self.window_finder = window_finder
        self.complete, self.ready, self.failed = complete, ready, failed
        self.pending = queue.Queue(maxsize=max_pending)
        self.stop = threading.Event()
        self.transport = None
        self.thread = None
        self.session = None
        self.frames = {}
        self.sessions = {}
        self.target_sessions = {}

    def start(self):
        self.thread = threading.Thread(target=self.run, name=f'lightss-chrome-{self.source}', daemon=True)
        self.thread.start()

    def submit(self, kind, value, callback=None):
        if not self.stop.is_set():
            try:
                self.pending.put_nowait((kind, value, callback))
                return True
            except queue.Full:
                pass
        if callback is not None:
            self.complete(callback, None)
        return False

    def on_event(self, event):
        method, params = event.get('method'), event.get('params', {})
        session = event.get('sessionId')
        if method == 'Target.attachedToTarget':
            attached = params['sessionId']
            info = params['targetInfo']
            self.target_sessions[info['targetId']] = attached
            self.sessions[attached] = info.get('type')
            self.frames.setdefault(attached, None)
            if info.get('type') in {'page', 'iframe'}:
                self.transport.send('Page.enable', session_id=attached)
            self.transport.send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session_id=attached)
            self.transport.send('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True}, session_id=attached)
            self.transport.send('Runtime.runIfWaitingForDebugger', session_id=attached)
        elif method == 'Target.detachedFromTarget':
            detached = params.get('sessionId')
            self.frames.pop(detached, None)
            self.sessions.pop(detached, None)
            self.target_sessions = {k: v for k, v in self.target_sessions.items() if v != detached}
            if detached is not None and detached == self.session:
                self.session = None
                self.epoch += 1
                self.navigation(self.epoch)
                raise ConnectionError('Chrome player page closed')
        elif method == 'Inspector.targetCrashed' and session == self.session:
            raise ConnectionError('Chrome player renderer stopped')
        elif method == 'Page.frameNavigated':
            frame = params.get('frame', {})
            if not frame.get('parentId'):
                self.frames[session] = frame.get('id')
                if session == self.session:
                    self.epoch += 1
                    self.navigation(self.epoch)
        elif method == 'Page.navigatedWithinDocument' and session == self.session:
            if params.get('frameId') == self.frames.get(session):
                self.epoch += 1
                self.navigation(self.epoch)
        elif method == 'Fetch.requestPaused':
            main = params.get('resourceType') == 'Document' and self.sessions.get(session) != 'iframe' and (
                self.frames.get(session) is None or params.get('frameId') == self.frames.get(session))
            permitted = request_allowed(self.source, params.get('request', {}).get('url', ''), main_frame=main)
            command = 'Fetch.continueRequest' if permitted else 'Fetch.failRequest'
            args = {'requestId': params['requestId']}
            if not permitted:
                args['errorReason'] = 'BlockedByClient'
            self.transport.send(command, args, session_id=session)

    def run(self):
        from chrome_transport import ChromeTransport
        from light_desktop import PROVIDER_HOME
        try:
            self.transport = ChromeTransport(self.binary, self.profile, initial_url="data:text/html,<title>Lightss player</title>")
            if self.stop.is_set():
                return
            self.transport.on_event = self.on_event
            self.transport.call('Browser.getVersion')
            # Browser-enforced permissions cover DNS-resolved LAN/loopback targets,
            # in addition to the literal-host Fetch gate. Native FIDO is separate.
            for name in ('local-network-access', 'local-network', 'loopback-network', 'microphone', 'camera', 'geolocation'):
                self.transport.call('Browser.setPermission', {'permission': {'name': name}, 'setting': 'denied'})
            # Auto-attach before navigating, including authentication popups and OOPIFs.
            self.transport.call('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True})
            deadline = time.monotonic() + 12
            while not self.stop.is_set():
                targets = self.transport.call('Target.getTargets')['targetInfos']
                target = next((t for t in targets if t.get('type') == 'page'), None)
                if target is not None:
                    self.session = self.target_sessions.get(target['targetId'])
                    if self.session is None:
                        self.session = self.transport.call('Target.attachToTarget', {'targetId': target['targetId'], 'flatten': True})['sessionId']
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Chrome app page startup')
                self.stop.wait(.1)
            if self.stop.is_set():
                return
            self.transport.call('Page.enable', session_id=self.session)
            tree = self.transport.call('Page.getFrameTree', session_id=self.session)
            self.frames[self.session] = tree['frameTree']['frame']['id']
            self.transport.call('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session_id=self.session)
            self.transport.call('Page.navigate', {'url': PROVIDER_HOME[self.source]}, session_id=self.session)
            from chrome_x11 import find_window_for_pid
            finder = self.window_finder or find_window_for_pid
            deadline = time.monotonic() + 10
            while not self.stop.is_set():
                window_id = finder(self.transport.process.pid)
                if window_id is not None:
                    self.ready(window_id)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Chrome native window startup')
                self.transport.call('Browser.getVersion', timeout=3)
                self.stop.wait(.1)
            while not self.stop.is_set():
                try:
                    kind, value, callback = self.pending.get(timeout=.1)
                except queue.Empty:
                    # Pump network/auth events even when the control UI is idle.
                    self.transport.call('Browser.getVersion', timeout=3)
                    continue
                result = None
                epoch = self.epoch
                try:
                    if kind == 'navigate':
                        if not request_allowed(self.source, value, main_frame=True):
                            raise ValueError('Provider navigation denied')
                        self.transport.call('Page.navigate', {'url': value}, session_id=self.session)
                    elif kind == 'eval':
                        response = self.transport.call('Runtime.evaluate', {
                            'expression': guarded_script(self.source, value), 'returnByValue': True,
                            'userGesture': callback is None, 'timeout': 1500,
                        }, session_id=self.session, timeout=3)
                        if not response.get('exceptionDetails'):
                            result = response.get('result', {}).get('value')
                except (RuntimeError, TimeoutError):
                    # Navigation can destroy an execution context between polls.
                    # A failed command is not evidence that the browser has died.
                    # Heartbeat/pipe EOF still terminates a genuinely lost browser.
                    pass
                finally:
                    if callback is not None:
                        self.complete(callback, result if epoch == self.epoch else None)
        except Exception:
            if not self.stop.is_set():
                # Browser errors can contain authentication URLs. Never echo them.
                self.failed('Chrome provider stopped or could not start. Close and reopen Lightss to retry. Check Chrome installation and profile availability.')
        finally:
            self.stop.set()
            if self.transport is not None:
                self.transport.close()
            self._finish_pending()

    def _finish_pending(self):
        while True:
            try:
                _, _, callback = self.pending.get_nowait()
            except queue.Empty:
                return
            if callback is not None:
                self.complete(callback, None)

    def close(self):
        self.stop.set()
        if self.transport is not None:
            self.transport.close()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)
        self._finish_pending()


def create_provider_view(source, binary, profile, parent=None):
    """Qt adapter with the existing setUrl/page().runJavaScript surface."""
    from PySide6.QtCore import Qt, Signal, Slot
    from PySide6.QtGui import QWindow
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

    class ChromeProviderView(QWidget):
        completed = Signal(object, object)
        browser_ready = Signal(int)
        browser_failed = Signal(str)
        browser_navigation = Signal(int)
        navigation_started = Signal()

        def __init__(self):
            super().__init__(parent)
            self.source = source
            self.engine = 'chrome'
            self.foreign = None
            self.container = None
            self._closed = False
            self.layout_box = QVBoxLayout(self)
            self.layout_box.setContentsMargins(0, 0, 0, 0)
            self.status = QLabel('Starting full Chrome — native phone passkeys enabled…', self)
            self.status.setWordWrap(True)
            self.status.setTextFormat(Qt.TextFormat.PlainText)
            self.layout_box.addWidget(self.status)
            self.completed.connect(self._complete, Qt.ConnectionType.QueuedConnection)
            self.browser_ready.connect(self._ready, Qt.ConnectionType.QueuedConnection)
            self.browser_failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
            self.browser_navigation.connect(self._navigation, Qt.ConnectionType.QueuedConnection)
            self.worker = BrowserWorker(
                source, binary, profile,
                lambda cb, value: self.completed.emit(cb, (self.worker.epoch, value)),
                self.browser_ready.emit, self.browser_failed.emit,
                navigation=self.browser_navigation.emit,
            )
            self.worker.start()

        @Slot(object, object)
        def _complete(self, callback, packet):
            if not self._closed and callback is not None:
                epoch, result = packet
                callback(result if epoch == self.worker.epoch else None)

        @Slot(int)
        def _navigation(self, epoch):
            if not self._closed:
                self.navigation_started.emit()

        @Slot(int)
        def _ready(self, window_id):
            if self._closed:
                return
            try:
                self.foreign = QWindow.fromWinId(window_id)
                if self.foreign is None:
                    raise RuntimeError('Native window unavailable')
                self.container = QWidget.createWindowContainer(self.foreign, self)
                self.container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                self.layout_box.addWidget(self.container)
                self.status.hide()
            except Exception:
                self._failed('Could not embed Chrome. Restart with --provider-engine=qt for the limited browser fallback.')
                self.worker.close()

        @Slot(str)
        def _failed(self, message):
            if self._closed:
                return
            self.status.setText(message)
            self.status.show()
            if self.container is not None:
                self.container.hide()

        def page(self):
            return self

        def setUrl(self, url):
            return self.worker.submit('navigate', url.toString())

        def runJavaScript(self, script, callback=None):
            self.worker.submit('eval', script, callback)

        def shutdown(self):
            if self._closed:
                return
            self._closed = True
            # Release foreign-window wrapping before killing its owning browser.
            if self.foreign is not None:
                self.foreign.setParent(None)
            self.worker.close()

    return ChromeProviderView()
