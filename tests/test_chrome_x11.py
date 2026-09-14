from __future__ import annotations

import pytest

import chrome_x11


class FakeTransport:
    def __init__(self, tree, pids=None, embeddable=None, *, root=1):
        self.root = root
        self.tree = tree
        self.pids = pids or {}
        self.embeddable = embeddable or {}
        self.closed = False
        self.pid_reads = []

    def children(self, window):
        return self.tree.get(window, ())

    def window_pid(self, window):
        self.pid_reads.append(window)
        return self.pids.get(window)

    def window_is_embeddable(self, window):
        return self.embeddable.get(window, True)

    def close(self):
        self.closed = True


def test_finds_topmost_window_owned_by_exact_pid(monkeypatch):
    transport = FakeTransport(
        {1: (10, 20), 10: (11,), 11: (12,), 20: (21,)},
        {10: 400, 11: 400, 20: 40},
    )
    monkeypatch.setattr(chrome_x11, "_open_transport", lambda _display: transport)

    assert chrome_x11.find_window_for_pid(400) == 10
    assert 11 not in transport.pid_reads
    assert transport.closed


def test_matches_pid_exactly_not_by_title_or_partial_value(monkeypatch):
    transport = FakeTransport({1: (10, 20)}, {10: 1234, 20: 234})
    monkeypatch.setattr(chrome_x11, "_open_transport", lambda _display: transport)

    assert chrome_x11.find_window_for_pid(34) is None
    assert transport.closed


def test_skips_hidden_helper_before_visible_browser_window(monkeypatch):
    transport = FakeTransport(
        {1: (10, 20)},
        {10: 400, 20: 400},
        {10: False, 20: True},
    )
    monkeypatch.setattr(chrome_x11, "_open_transport", lambda _display: transport)

    assert chrome_x11.find_window_for_pid(400) == 20


def test_tree_walk_is_bounded_and_cycle_safe(monkeypatch):
    transport = FakeTransport({1: (2,), 2: (1, 3), 3: (4,)}, {4: 99})
    monkeypatch.setattr(chrome_x11, "_MAX_WINDOWS", 2)
    monkeypatch.setattr(chrome_x11, "_open_transport", lambda _display: transport)

    assert chrome_x11.find_window_for_pid(99) is None
    assert transport.pid_reads == [2, 3]
    assert transport.closed


def test_closes_connection_when_query_fails(monkeypatch):
    class BrokenTransport(FakeTransport):
        def children(self, window):
            raise chrome_x11.X11QueryError("window disappeared")

    transport = BrokenTransport({})
    monkeypatch.setattr(chrome_x11, "_open_transport", lambda _display: transport)

    with pytest.raises(chrome_x11.X11QueryError, match="window disappeared"):
        chrome_x11.find_window_for_pid(1)
    assert transport.closed


@pytest.mark.parametrize("pid", [0, -1, True, "42"])
def test_rejects_invalid_pid_without_opening_display(monkeypatch, pid):
    monkeypatch.setattr(
        chrome_x11,
        "_open_transport",
        lambda _display: pytest.fail("display must not be opened"),
    )

    with pytest.raises((TypeError, ValueError)):
        chrome_x11.find_window_for_pid(pid)


def test_passes_optional_display_name_to_transport(monkeypatch):
    seen = []
    transport = FakeTransport({1: ()})
    monkeypatch.setattr(
        chrome_x11,
        "_open_transport",
        lambda display: seen.append(display) or transport,
    )

    assert chrome_x11.find_window_for_pid(7, ":88") is None
    assert seen == [":88"]


def test_missing_x11_library_is_reported_as_clean_exception(monkeypatch):
    def fail(_display):
        raise chrome_x11.X11Unavailable("libxcb is unavailable")

    monkeypatch.setattr(chrome_x11, "_open_transport", fail)

    with pytest.raises(chrome_x11.X11Unavailable, match="libxcb"):
        chrome_x11.find_window_for_pid(7)
