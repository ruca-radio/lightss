"""Qt auth UX contracts; fake only the browser-owned, non-constructible request."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QObject, Signal, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPushButton
pytest.importorskip("PySide6.QtWebEngineCore")
from PySide6.QtWebEngineCore import QWebEngineWebAuthUxRequest as Auth


class Request(QObject):
    stateChanged = Signal(object)

    def __init__(self, state='NotStarted'):
        super().__init__()
        self.current = getattr(Auth.WebAuthUxState, state)
        self.calls = []

    def state(self): return self.current
    def relyingPartyId(self): return 'accounts.example.test'
    def userNames(self): return ['first@example.test', 'second@example.test']
    def pinRequest(self):
        return type('Pin', (), dict(reason=Auth.PinEntryReason.Challenge,
                    error=Auth.PinEntryError.WrongPin, minPinLength=4, remainingAttempts=2))()
    def requestFailureReason(self): return Auth.RequestFailureReason.Timeout
    def cancel(self): self.calls.append(('cancel',)); self.change('Cancelled')
    def retry(self): self.calls.append(('retry',)); self.change('NotStarted')
    def setPin(self, value): self.calls.append(('pin', value))
    def setSelectedAccount(self, value): self.calls.append(('account', value))
    def change(self, state):
        self.current = getattr(Auth.WebAuthUxState, state)
        self.stateChanged.emit(self.current)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app):
    from desktop_auth import WebAuthDialog
    request = Request()
    widget = WebAuthDialog(request)
    widget.show()
    yield widget, request
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_pending_auth_displays_real_relying_party_and_honest_qr_limitation(dialog):
    widget, _ = dialog
    text = ' '.join(label.text() for label in widget.findChildren(QLabel))
    assert 'accounts.example.test' in text
    assert 'phone QR' in text
    assert 'not supported' in text
    assert 'password' in text


def test_account_selection_returns_only_user_selected_account(dialog):
    widget, request = dialog
    request.change('SelectAccount')
    widget.findChild(QComboBox, 'accounts').setCurrentIndex(1)
    widget.findChild(QPushButton, 'continue').click()
    assert request.calls == [('account', 'second@example.test')]
    assert not widget.findChild(QPushButton, 'continue').isEnabled()


def test_pin_is_masked_and_cleared_immediately_after_submission(dialog):
    widget, request = dialog
    request.change('CollectPin')
    pin = widget.findChild(QLineEdit, 'pin')
    assert pin.echoMode() == QLineEdit.EchoMode.Password
    assert '2' in widget.findChild(QLabel, 'message').text()
    pin.setText('1234')
    widget.findChild(QPushButton, 'continue').click()
    assert request.calls == [('pin', '1234')]
    assert pin.text() == ''


def test_short_pin_is_not_submitted(dialog):
    widget, request = dialog
    request.change('CollectPin')
    widget.findChild(QLineEdit, 'pin').setText('123')
    widget.findChild(QPushButton, 'continue').click()
    assert request.calls == []


def test_failure_can_retry_without_replacing_browser_request(dialog):
    widget, request = dialog
    request.change('RequestFailed')
    assert 'timed out' in widget.findChild(QLabel, 'message').text()
    widget.findChild(QPushButton, 'retry').click()
    assert request.calls == [('retry',)]
    assert widget.isVisible()


def test_closing_dialog_cancels_browser_request_once(dialog):
    widget, request = dialog
    widget.close()
    widget.reject()
    assert request.calls == [('cancel',)]


@pytest.mark.parametrize('state', ['Completed', 'Cancelled'])
def test_terminal_browser_state_closes_without_recancelling(dialog, state):
    widget, request = dialog
    request.change(state)
    assert not widget.isVisible()
    assert request.calls == []


def test_destroyed_browser_request_closes_dialog_without_dangling_access(dialog):
    widget, request = dialog
    request.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not widget.isVisible()
    widget.reject()


def test_provider_auth_signal_keeps_dialog_alive_and_cancels_replaced_request(app):
    from desktop_auth import install_web_auth
    class Page(QObject):
        webAuthUxRequested = Signal(object)
    page = Page()
    install_web_auth(page)
    first = Request()
    page.webAuthUxRequested.emit(first)
    assert page.auth_dialog.isVisible()
    second = Request('SelectAccount')
    page.webAuthUxRequested.emit(second)
    assert first.calls == [('cancel',)]
    assert page.auth_dialog.isVisible()
    second.change('Completed')
    assert page.auth_dialog is None
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

@pytest.mark.parametrize('reason', ['Set', 'Change'])
def test_new_pin_requires_matching_confirmation(dialog, reason):
    widget, request = dialog
    original = request.pinRequest
    def info():
        value = original()
        value.reason = getattr(Auth.PinEntryReason, reason)
        return value
    request.pinRequest = info
    request.change('CollectPin')
    pin = widget.findChild(QLineEdit, 'pin')
    confirm = widget.findChild(QLineEdit, 'pinConfirmation')
    assert confirm is not None
    assert confirm.isVisible()
    assert confirm.echoMode() == QLineEdit.EchoMode.Password
    pin.setText('1234')
    confirm.setText('1235')
    widget.findChild(QPushButton, 'continue').click()
    assert request.calls == []
    confirm.setText('1234')
    widget.findChild(QPushButton, 'continue').click()
    assert request.calls == [('pin', '1234')]
    assert pin.text() == confirm.text() == ''
