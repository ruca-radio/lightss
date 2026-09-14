#!/usr/bin/env python3
"""Standalone Lightss desktop shell with isolated official provider pages."""

from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import socket
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import desktop_bridge

PROVIDER_HOME = {
    "youtube_music": "https://music.youtube.com/",
    "apple_music": "https://music.apple.com/",
}
PROVIDER_NAVIGATION_HOSTS = {
    "youtube_music": frozenset({"music.youtube.com", "accounts.google.com", "accounts.youtube.com"}),
    "apple_music": frozenset({"music.apple.com", "idmsa.apple.com", "appleid.apple.com"}),
}
DESKTOP_TOKEN_HEADER = "X-Lightss-Desktop-Token"


def provider_navigation_allowed(source: str, url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except (TypeError, ValueError):
        return False
    return parsed.scheme == "https" and parsed.hostname in PROVIDER_NAVIGATION_HOSTS.get(source, ()) and not parsed.username


def provider_command_url(source: str, command: str, data: dict[str, Any]) -> str | None:
    if source not in PROVIDER_HOME:
        return None
    if command == "library":
        return "https://music.youtube.com/library" if source == "youtube_music" else "https://music.apple.com/library"
    if command == "search":
        query = data.get("query")
        if not isinstance(query, str) or not query.strip():
            return None
        encoded = urllib.parse.quote_plus(query.strip())
        return f"{PROVIDER_HOME[source]}search?q={encoded}"
    if command == "play_id":
        item_id, kind = data.get("id"), data.get("kind")
        if not desktop_bridge.valid_provider_id(item_id) or kind not in {"song", "album", "playlist", "artist"}:
            return None
        if source == "youtube_music":
            if kind == "song":
                return f"https://music.youtube.com/watch?v={item_id}"
            if kind == "playlist":
                return f"https://music.youtube.com/playlist?list={item_id}"
            if kind == "album":
                return f"https://music.youtube.com/browse/{item_id}"
            return f"https://music.youtube.com/channel/{item_id}"
        return f"https://music.apple.com/us/{kind}/{item_id}"
    return None


def _build_state():
    import light_gui

    light_gui.lightctl.load_dotenv()
    client, info_client = light_gui._build_fleet_client(dry_run=False)
    return light_gui.GuiState(client, info_client=info_client)


def authenticated_handler(handler_class, token: str):
    """Require the desktop capability before any local HTTP method runs."""
    class AuthenticatedHandler(handler_class):
        desktop_token = token

        def _desktop_authorized(self) -> bool:
            if secrets.compare_digest(self.headers.get(DESKTOP_TOKEN_HEADER, ""), token):
                return True
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "46")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(b'{"ok":false,"error":"Desktop token required."}')
            return False

        def do_GET(self):
            if self._desktop_authorized():
                return super().do_GET()

        def do_POST(self):
            if self._desktop_authorized():
                return super().do_POST()

        def do_HEAD(self):
            if self._desktop_authorized():
                return super().do_HEAD()

    AuthenticatedHandler.__name__ = f"Authenticated{handler_class.__name__}"
    return AuthenticatedHandler


def is_exact_backend_url(url: str, backend_url: str) -> bool:
    try:
        candidate = urllib.parse.urlsplit(url)
        backend = urllib.parse.urlsplit(backend_url)
        return candidate.scheme == backend.scheme and candidate.hostname == backend.hostname and candidate.port == backend.port
    except (TypeError, ValueError):
        return False


def local_capture_permission_allowed(origin: str, backend_url: str, permission_type: str) -> bool:
    return is_exact_backend_url(origin, backend_url) and permission_type in {
        "MediaAudioCapture", "MediaVideoCapture", "MediaAudioVideoCapture",
    }


class BackendServer:
    """Own the loopback-only server in-process so it shares the bridge registry."""

    def __init__(self, *, server_factory: Callable[..., Any] | None = None,
                 handler_factory: Callable[..., Any] | None = None,
                 state_factory: Callable[[], Any] = _build_state,
                 token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32)):
        if server_factory is None or handler_factory is None:
            import light_gui
            from http.server import ThreadingHTTPServer
            server_factory = server_factory or ThreadingHTTPServer
            handler_factory = handler_factory or light_gui.make_handler
        self._server_factory = server_factory
        self._handler_factory = handler_factory
        self._state_factory = state_factory
        self.token = token_factory()
        self.server: Any = None
        self.state: Any = None
        self.thread: threading.Thread | None = None
        self.url: str | None = None

    def start(self) -> str:
        if self.server is not None:
            raise RuntimeError("backend already started")
        self.state = self._state_factory()
        try:
            import light_gui

            light_gui.auto_start_smart_director(self.state)
            handler = authenticated_handler(self._handler_factory(self.state), self.token)
            self.server = self._server_factory(("127.0.0.1", 0), handler)
        except Exception:
            self.close()
            raise
        port = int(self.server.server_address[1])
        self.url = f"http://127.0.0.1:{port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, name="lightss-desktop-backend", daemon=True)
        self.thread.start()
        return self.url

    def close(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
            if self.thread is not None and self.thread is not threading.current_thread():
                self.thread.join(timeout=2)
        state, self.state = self.state, None
        try:
            import smart_director

            director = smart_director.current()
            if director is not None and getattr(director, "fleet", None) is getattr(state, "client", None):
                smart_director.stop(steady=True)
        except Exception:
            pass
        for attr, method in (
            ("schedule", "stop"), ("mode1", "stop"), ("mood_session", "stop"),
            ("fade_timer", "stop"), ("_cycle", "stop"), ("_sunrise", "stop"),
            ("autonomous", "stop"), ("ai_jobs", "shutdown"), ("_mood_executor", "shutdown"),
        ):
            target = getattr(state, attr, None)
            cleanup = getattr(target, method, None)
            if callable(cleanup):
                cleanup()


def _private_or_local_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"file", "qrc"}:
        return True
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(host))
        except (OSError, ValueError):
            return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified


def provider_observation_script(source: str) -> str:
    """Fixed media observer; source never supplies executable input."""
    if source == "youtube_music":
        track_id = """(() => { const tagged=document.querySelector('[video-id]');
return (m.dataset&&m.dataset.videoId)||m.getAttribute('video-id')||(tagged&&tagged.getAttribute('video-id'))||new URL(location.href).searchParams.get('v')||null; })()"""
    elif source == "apple_music":
        track_id = """(() => { const kit=globalThis.MusicKit&&globalThis.MusicKit.getInstance&&globalThis.MusicKit.getInstance();
const item=kit&&kit.nowPlayingItem; const params=item&&item.attributes&&item.attributes.playParams;
const link=document.querySelector('[data-testid="player-bar"] a[href*="?i="],a[data-testid="now-playing-title"]');
return (item&&(item.id||item.identifier))||(params&&(params.id||params.catalogId))||(link&&new URL(link.href,location.href).searchParams.get('i'))||null; })()"""
    else:
        raise ValueError("unsupported provider source")
    return f"""(() => {{ const m=document.querySelector('video,audio'); if(!m) return null;
const trackId=()=>{track_id};
const latch=globalThis.__lightssMediaLatch||(globalThis.__lightssMediaLatch={{media:null,endedTrackId:null}});
if(latch.media!==m){{latch.media=m;m.addEventListener('ended',()=>{{latch.endedTrackId=trackId();}},{{passive:true}});}}
const md=navigator.mediaSession&&navigator.mediaSession.metadata; let id=trackId();
const ended=!!latch.endedTrackId; if(ended){{id=latch.endedTrackId;latch.endedTrackId=null;}}
return {{playing:ended?false:!m.paused&&!m.ended,ended,position:Number(m.currentTime)||0,
duration:Number.isFinite(m.duration)?m.duration:0,title:(md&&md.title)||document.title,
artist:(md&&md.artist)||'',album:(md&&md.album)||'',...(id?{{track_id:id}}:{{}}),url:location.href}}; }})()"""
_TRANSPORT = {
    "play": "(() => {const m=document.querySelector('video,audio'); if(m)m.play();})()",
    "pause": "(() => {const m=document.querySelector('video,audio'); if(m)m.pause();})()",
    "toggle": "(() => {const m=document.querySelector('video,audio'); if(m)(m.paused?m.play():m.pause());})()",
    "next": "(() => document.querySelector('.next-button,[data-testid=player-next-button],button[aria-label=Next]')?.click())()",
    "previous": "(() => document.querySelector('.previous-button,[data-testid=player-previous-button],button[aria-label=Previous]')?.click())()",
    "seek": "(() => {const m=document.querySelector('video,audio'); if(m)m.currentTime=__VALUE__;})()",
    "volume": "(() => {const m=document.querySelector('video,audio'); if(m)m.volume=__VALUE__;})()",
}


def run_desktop(*, smoke_test: bool = False, provider_engine: str = "auto") -> int:
    # Select XWayland before constructing QApplication; native foreign windows need xcb.
    from chrome_provider import CHROME_HELP, create_provider_view, find_chrome_binary
    chrome_binary = find_chrome_binary(provider_engine)
    if chrome_binary:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    # Lazy imports keep bridge/server tests independent of Qt installation and display state.
    from PySide6.QtCore import QCoreApplication, QEvent, QStandardPaths, QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

    from desktop_auth import AUTH_HELP, install_web_auth

    app = QApplication.instance() or QApplication(sys.argv[:1])
    if chrome_binary and app.platformName() != "xcb":
        if provider_engine == "chrome":
            raise RuntimeError("Native Chrome tabs require Qt xcb; restart with QT_QPA_PLATFORM=xcb.")
        chrome_binary = None
    app.setOrganizationName("Lightss")
    app.setApplicationName("lightss")
    if smoke_test:
        QWebEngineView()
        return 0

    backend = BackendServer()
    backend_url = backend.start()
    bridge = desktop_bridge.registry
    bridge.activate()

    class RemoteInterceptor(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if _private_or_local_url(info.requestUrl().toString()):
                info.block(True)

    class LocalInterceptor(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if is_exact_backend_url(info.requestUrl().toString(), backend_url):
                info.setHttpHeader(DESKTOP_TOKEN_HEADER.encode(), backend.token.encode())

    class LocalPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if is_main_frame and not is_exact_backend_url(url.toString(), backend_url):
                return False
            return True

    class ProviderPage(QWebEnginePage):
        def __init__(self, source, profile, parent=None):
            super().__init__(profile, parent)
            self.source = source
            install_web_auth(self, parent)

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if not is_main_frame:
                return True
            return provider_navigation_allowed(self.source, url.toString())

    window = None
    timer = None
    local_page = None
    local = None
    local_profile = None
    pages = {}
    provider_profiles = {}
    provider_interceptors = {}
    try:
        window = QMainWindow()
        tabs = QTabWidget(window)
        local_profile = QWebEngineProfile("lightss-local", window)
        local_interceptor = LocalInterceptor(local_profile)
        local_profile.setUrlRequestInterceptor(local_interceptor)
        local_page = LocalPage(local_profile, window)
        local = QWebEngineView(tabs)
        local.setPage(local_page)

        def request_local_permission(permission):
            kind = permission.permissionType().name
            if not local_capture_permission_allowed(permission.origin().toString(), backend_url, kind):
                permission.deny()
                return
            label = "microphone and camera" if kind == "MediaAudioVideoCapture" else ("microphone" if kind == "MediaAudioCapture" else "camera")
            answer = QMessageBox.question(
                window,
                "Lightss media permission",
                f"Allow the local Lightss interface to use your {label}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                permission.grant()
            else:
                permission.deny()

        local_page.permissionRequested.connect(request_local_permission)
        local.setUrl(QUrl(backend_url))
        tabs.addTab(local, "Lightss")
        storage_root = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)) / "providers"
        storage_root.mkdir(parents=True, exist_ok=True)
        for source, label in (("youtube_music", "YouTube Music"), ("apple_music", "Apple Music")):
            if chrome_binary:
                view = create_provider_view(source, chrome_binary, storage_root / "chrome" / source, tabs)
                tabs.addTab(view, label)
                pages[source] = (view.page(), view)
                continue
            profile = QWebEngineProfile(f"lightss-{source}", window)
            profile.setPersistentStoragePath(str(storage_root / source))
            profile.setCachePath(str(storage_root / source / "cache"))
            interceptor = RemoteInterceptor(profile)
            profile.setUrlRequestInterceptor(interceptor)
            page = ProviderPage(source, profile, window)
            page.permissionRequested.connect(lambda permission: permission.deny())
            view = QWebEngineView(tabs)
            view.setPage(page)
            view.setUrl(QUrl(PROVIDER_HOME[source]))
            tabs.addTab(view, label)
            pages[source] = (page, view)
            provider_profiles[source] = profile
            provider_interceptors[source] = interceptor
        engine_label = "Chrome (native passkeys)" if chrome_binary else "Qt (limited passkeys)"
        window.setWindowTitle(f"Lightss — {engine_label}")
        help_text = CHROME_HELP if chrome_binary else AUTH_HELP
        help_action = window.menuBar().addAction("Sign-in help")
        help_action.triggered.connect(lambda: QMessageBox.information(window, "Provider sign-in", help_text))
        window.setCentralWidget(tabs)
        window.resize(1400, 900)

        observation_generation = {source: 0 for source in pages}
        observation_pending = {source: None for source in pages}

        def provider_navigated(source):
            observation_generation[source] += 1
            observation_pending[source] = None
            bridge.reset(source)

        for source, (page, view) in pages.items():
            signal = view.navigation_started if hasattr(view, "navigation_started") else page.loadStarted
            signal.connect(lambda source=source: provider_navigated(source))

        def observation_finished(source, generation, result):
            if observation_pending.get(source) != generation:
                return
            observation_pending[source] = None
            if observation_generation.get(source) == generation and isinstance(result, dict):
                bridge.publish(source, result)

        def poll():
            for item in bridge.drain():
                source, command, data = item["source"], item["command"], item["data"]
                page, view = pages[source]
                destination = provider_command_url(source, command, data)
                if destination:
                    observation_generation[source] += 1
                    observation_pending[source] = None
                    bridge.reset(source)
                    view.setUrl(QUrl(destination))
                    tabs.setCurrentWidget(view)
                    continue
                script = _TRANSPORT.get(command)
                if script:
                    value = data.get("position", data.get("volume"))
                    if value is not None:
                        script = script.replace("__VALUE__", repr(float(value)))
                    page.runJavaScript(script)
            for source, (page, _view) in pages.items():
                if observation_pending[source] is not None:
                    continue
                generation = observation_generation[source]
                observation_pending[source] = generation
                page.runJavaScript(
                    provider_observation_script(source),
                    lambda result, source=source, generation=generation: observation_finished(source, generation, result),
                )

        timer = QTimer(window)
        timer.timeout.connect(poll)
        timer.start(500)
        app.aboutToQuit.connect(backend.close)
        app.aboutToQuit.connect(bridge.close)
        window.show()
        return app.exec()
    finally:
        if timer is not None:
            timer.stop()
        for page, view in pages.values():
            view.hide()
            if hasattr(view, "shutdown"):
                view.shutdown()
            if page is not view:
                page.deleteLater()
            view.deleteLater()
        if local is not None:
            local.hide()
            local.deleteLater()
        if local_page is not None:
            local_page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for profile in provider_profiles.values():
            profile.deleteLater()
        if local_profile is not None:
            local_profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if window is not None:
            window.close()
            window.deleteLater()
            app.processEvents()
        bridge.close()
        backend.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lightss standalone desktop")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--provider-engine", choices=("auto", "chrome", "qt"), default="auto", help="Provider browser: native Chrome tabs when available, or limited Qt fallback")
    args = parser.parse_args(argv)
    return run_desktop(smoke_test=args.smoke_test, provider_engine=args.provider_engine)


if __name__ == "__main__":
    raise SystemExit(main())
