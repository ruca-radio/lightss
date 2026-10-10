"""Safe, PID-based X11 window discovery for native Chrome embedding.

This module intentionally uses XCB instead of Xlib.  XCB reports asynchronous
window-disappearance errors on the connection/reply rather than invoking Xlib's
process-global error handler, which would be unsafe to replace in a Qt process.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from collections import deque
from typing import Protocol


_MAX_WINDOWS = 4096


class X11Unavailable(RuntimeError):
    """The requested X11 display cannot be used."""


class X11QueryError(RuntimeError):
    """An X11 query failed unexpectedly."""


class _Transport(Protocol):
    root: int

    def children(self, window: int) -> tuple[int, ...]: ...
    def window_pid(self, window: int) -> int | None: ...
    def window_is_embeddable(self, window: int) -> bool: ...
    def close(self) -> None: ...


class _Screen(ctypes.Structure):
    _fields_ = [
        ("root", ctypes.c_uint32),
        ("default_colormap", ctypes.c_uint32),
        ("white_pixel", ctypes.c_uint32),
        ("black_pixel", ctypes.c_uint32),
        ("current_input_masks", ctypes.c_uint32),
        ("width_in_pixels", ctypes.c_uint16),
        ("height_in_pixels", ctypes.c_uint16),
        ("width_in_millimeters", ctypes.c_uint16),
        ("height_in_millimeters", ctypes.c_uint16),
        ("min_installed_maps", ctypes.c_uint16),
        ("max_installed_maps", ctypes.c_uint16),
        ("root_visual", ctypes.c_uint32),
        ("backing_stores", ctypes.c_uint8),
        ("save_unders", ctypes.c_uint8),
        ("root_depth", ctypes.c_uint8),
        ("allowed_depths_len", ctypes.c_uint8),
    ]


class _ScreenIterator(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.POINTER(_Screen)),
        ("rem", ctypes.c_int),
        ("index", ctypes.c_int),
    ]


class _InternAtomReply(ctypes.Structure):
    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("atom", ctypes.c_uint32),
    ]


class _QueryTreeReply(ctypes.Structure):
    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("root", ctypes.c_uint32),
        ("parent", ctypes.c_uint32),
        ("children_len", ctypes.c_uint16),
        ("pad1", ctypes.c_uint8 * 14),
    ]


class _GetPropertyReply(ctypes.Structure):
    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("format", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytes_after", ctypes.c_uint32),
        ("value_len", ctypes.c_uint32),
        ("pad0", ctypes.c_uint8 * 12),
    ]


class _GetWindowAttributesReply(ctypes.Structure):
    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("backing_store", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("visual", ctypes.c_uint32),
        ("window_class", ctypes.c_uint16),
        ("bit_gravity", ctypes.c_uint8),
        ("win_gravity", ctypes.c_uint8),
        ("backing_planes", ctypes.c_uint32),
        ("backing_pixel", ctypes.c_uint32),
        ("save_under", ctypes.c_uint8),
        ("map_is_installed", ctypes.c_uint8),
        ("map_state", ctypes.c_uint8),
        ("override_redirect", ctypes.c_uint8),
        ("colormap", ctypes.c_uint32),
        ("all_event_masks", ctypes.c_uint32),
        ("your_event_mask", ctypes.c_uint32),
        ("do_not_propagate_mask", ctypes.c_uint16),
        ("pad0", ctypes.c_uint8 * 2),
    ]


class _GetGeometryReply(ctypes.Structure):
    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("depth", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("root", ctypes.c_uint32),
        ("x", ctypes.c_int16),
        ("y", ctypes.c_int16),
        ("width", ctypes.c_uint16),
        ("height", ctypes.c_uint16),
        ("border_width", ctypes.c_uint16),
        ("pad0", ctypes.c_uint8 * 2),
    ]


class _XcbTransport:
    def __init__(self, display_name: str | None):
        library = ctypes.util.find_library("xcb")
        if not library:
            raise X11Unavailable("libxcb is unavailable")
        try:
            self._xcb = ctypes.CDLL(library)
            self._libc = ctypes.CDLL(None)
            self._bind()
        except (OSError, AttributeError) as exc:
            raise X11Unavailable(f"libxcb cannot be loaded: {exc}") from exc

        screen_number = ctypes.c_int()
        encoded = display_name.encode() if display_name is not None else None
        self._connection = self._xcb.xcb_connect(encoded, ctypes.byref(screen_number))
        if not self._connection or self._xcb.xcb_connection_has_error(self._connection):
            if self._connection:
                self._xcb.xcb_disconnect(self._connection)
            self._connection = None
            label = display_name if display_name is not None else "$DISPLAY"
            raise X11Unavailable(f"cannot open X11 display {label}")

        setup = self._xcb.xcb_get_setup(self._connection)
        iterator = self._xcb.xcb_setup_roots_iterator(setup)
        for _ in range(screen_number.value):
            self._xcb.xcb_screen_next(ctypes.byref(iterator))
        if iterator.rem <= 0 or not iterator.data:
            self.close()
            raise X11Unavailable("X11 display has no requested screen")
        self.root = int(iterator.data.contents.root)
        self._pid_atom = self._intern_atom("_NET_WM_PID")

    def _bind(self) -> None:
        xcb = self._xcb
        xcb.xcb_connect.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        xcb.xcb_connect.restype = ctypes.c_void_p
        xcb.xcb_connection_has_error.argtypes = [ctypes.c_void_p]
        xcb.xcb_connection_has_error.restype = ctypes.c_int
        xcb.xcb_disconnect.argtypes = [ctypes.c_void_p]
        xcb.xcb_get_setup.argtypes = [ctypes.c_void_p]
        xcb.xcb_get_setup.restype = ctypes.c_void_p
        xcb.xcb_setup_roots_iterator.argtypes = [ctypes.c_void_p]
        xcb.xcb_setup_roots_iterator.restype = _ScreenIterator
        xcb.xcb_screen_next.argtypes = [ctypes.POINTER(_ScreenIterator)]

        xcb.xcb_intern_atom.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_char_p]
        xcb.xcb_intern_atom.restype = ctypes.c_uint32
        xcb.xcb_intern_atom_reply.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        xcb.xcb_intern_atom_reply.restype = ctypes.POINTER(_InternAtomReply)
        xcb.xcb_query_tree.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        xcb.xcb_query_tree.restype = ctypes.c_uint32
        xcb.xcb_query_tree_reply.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        xcb.xcb_query_tree_reply.restype = ctypes.POINTER(_QueryTreeReply)
        xcb.xcb_query_tree_children.argtypes = [ctypes.POINTER(_QueryTreeReply)]
        xcb.xcb_query_tree_children.restype = ctypes.POINTER(ctypes.c_uint32)
        xcb.xcb_get_property.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
        xcb.xcb_get_property.restype = ctypes.c_uint32
        xcb.xcb_get_property_reply.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        xcb.xcb_get_property_reply.restype = ctypes.POINTER(_GetPropertyReply)
        xcb.xcb_get_property_value.argtypes = [ctypes.POINTER(_GetPropertyReply)]
        xcb.xcb_get_property_value.restype = ctypes.c_void_p
        xcb.xcb_get_window_attributes.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        xcb.xcb_get_window_attributes.restype = ctypes.c_uint32
        xcb.xcb_get_window_attributes_reply.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        xcb.xcb_get_window_attributes_reply.restype = ctypes.POINTER(_GetWindowAttributesReply)
        xcb.xcb_get_geometry.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        xcb.xcb_get_geometry.restype = ctypes.c_uint32
        xcb.xcb_get_geometry_reply.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        xcb.xcb_get_geometry_reply.restype = ctypes.POINTER(_GetGeometryReply)
        self._libc.free.argtypes = [ctypes.c_void_p]

    def _intern_atom(self, name: str) -> int:
        raw = name.encode("ascii")
        cookie = self._xcb.xcb_intern_atom(self._connection, 0, len(raw), raw)
        reply = self._xcb.xcb_intern_atom_reply(self._connection, cookie, None)
        if not reply:
            self.close()
            raise X11Unavailable(f"X11 atom {name} is unavailable")
        try:
            return int(reply.contents.atom)
        finally:
            self._libc.free(reply)

    def children(self, window: int) -> tuple[int, ...]:
        cookie = self._xcb.xcb_query_tree(self._connection, window)
        reply = self._xcb.xcb_query_tree_reply(self._connection, cookie, None)
        if not reply:  # A window may disappear between traversal requests.
            return ()
        try:
            count = int(reply.contents.children_len)
            values = self._xcb.xcb_query_tree_children(reply)
            return tuple(int(values[index]) for index in range(count)) if values else ()
        finally:
            self._libc.free(reply)

    def window_pid(self, window: int) -> int | None:
        # type=XA_CARDINAL (6), one 32-bit item. XCB lengths are in 32-bit units.
        cookie = self._xcb.xcb_get_property(self._connection, 0, window, self._pid_atom, 6, 0, 1)
        reply = self._xcb.xcb_get_property_reply(self._connection, cookie, None)
        if not reply:
            return None
        try:
            if reply.contents.format != 32 or reply.contents.value_len < 1:
                return None
            value = self._xcb.xcb_get_property_value(reply)
            return int(ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32)).contents.value) if value else None
        finally:
            self._libc.free(reply)

    def window_is_embeddable(self, window: int) -> bool:
        attributes_cookie = self._xcb.xcb_get_window_attributes(self._connection, window)
        attributes = self._xcb.xcb_get_window_attributes_reply(
            self._connection, attributes_cookie, None
        )
        if not attributes:
            return False
        try:
            # XCB_WINDOW_CLASS_INPUT_OUTPUT=1; XCB_MAP_STATE_VIEWABLE=2.
            if attributes.contents.window_class != 1 or attributes.contents.map_state != 2:
                return False
        finally:
            self._libc.free(attributes)

        geometry_cookie = self._xcb.xcb_get_geometry(self._connection, window)
        geometry = self._xcb.xcb_get_geometry_reply(self._connection, geometry_cookie, None)
        if not geometry:
            return False
        try:
            # Reject Chrome's clipboard/helper surfaces and other tiny utility windows.
            return geometry.contents.width >= 100 and geometry.contents.height >= 100
        finally:
            self._libc.free(geometry)

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection:
            self._xcb.xcb_disconnect(connection)
            self._connection = None


def _open_transport(display_name: str | None) -> _Transport:
    return _XcbTransport(display_name)


def find_window_for_pid(pid: int, display_name: str | None = None) -> int | None:
    """Return the topmost X11 window carrying ``_NET_WM_PID == pid``.

    Search is breadth-first and bounded, so an owned parent is preferred over
    any owned descendant and malformed/cyclic server data cannot loop forever.
    """
    if isinstance(pid, bool) or not isinstance(pid, int):
        raise TypeError("pid must be an integer")
    if pid <= 0:
        raise ValueError("pid must be positive")

    transport = _open_transport(display_name)
    try:
        queue = deque(transport.children(transport.root))
        visited = {transport.root}
        inspected = 0
        while queue and inspected < _MAX_WINDOWS:
            window = queue.popleft()
            if window in visited:
                continue
            visited.add(window)
            inspected += 1
            if transport.window_pid(window) == pid and transport.window_is_embeddable(window):
                return window
            queue.extend(transport.children(window))
        return None
    finally:
        transport.close()


__all__ = ["X11QueryError", "X11Unavailable", "find_window_for_pid"]
