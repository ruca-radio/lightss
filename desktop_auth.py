"""Native UI for the WebAuthn states exposed by Qt (not Chrome's hybrid QR UX).

Never log or persist authentication requests, account lists, or authenticator PINs.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

AUTH_HELP = (
    "In this embedded browser, phone QR passkey sign-in is not supported. "
    "Use a supported security key, or cancel and choose the provider's password / "
    "other sign-in option. Signing in to an external browser does not sign in here."
)


class WebAuthDialog(QDialog):
    """One browser-owned request; cancellation and destruction are terminal."""

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self.setWindowTitle("Lightss — security key / passkey")
        self.setMinimumWidth(460)
        self.setWindowModality(Qt.WindowModality.NonModal)
        layout = QVBoxLayout(self)
        origin = QLabel(f"Authenticate with: {request.relyingPartyId()}")
        origin.setTextFormat(Qt.TextFormat.PlainText)
        origin.setWordWrap(True)
        layout.addWidget(origin)
        self.message = QLabel()
        self.message.setObjectName('message')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.accounts = QComboBox()
        self.accounts.setObjectName('accounts')
        layout.addWidget(self.accounts)
        self.pin = QLineEdit()
        self.pin.setObjectName('pin')
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setPlaceholderText("Security key PIN (not your account password)")
        layout.addWidget(self.pin)
        self.pin_confirmation = QLineEdit()
        self.pin_confirmation.setObjectName("pinConfirmation")
        self.pin_confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin_confirmation.setPlaceholderText("Confirm the new security key PIN")
        layout.addWidget(self.pin_confirmation)
        help_label = QLabel(AUTH_HELP)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.proceed = QPushButton('Continue')
        self.proceed.setObjectName('continue')
        buttons.addButton(self.proceed, QDialogButtonBox.ButtonRole.ActionRole)
        self.retry_button = QPushButton('Try again')
        self.retry_button.setObjectName('retry')
        buttons.addButton(self.retry_button, QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        buttons.rejected.connect(self.reject)
        self.proceed.clicked.connect(self.submit)
        self.pin.returnPressed.connect(self.submit)
        self.pin_confirmation.returnPressed.connect(self.submit)
        self.retry_button.clicked.connect(self.retry)
        request.stateChanged.connect(self.update_state)
        request.destroyed.connect(self.request_finished)
        self.update_state(request.state())

    def update_state(self, state):
        if self.request is None:
            return
        name = state.name
        if name in {'Completed', 'Cancelled'}:
            self.request_finished()
            return
        self.pin.clear()
        self.pin_confirmation.clear()
        self.pin.setVisible(name == 'CollectPin')
        self.pin_confirmation.setVisible(name == 'CollectPin' and self.request.pinRequest().reason.name in {'Set', 'Change'})
        self.accounts.setVisible(name == 'SelectAccount')
        self.proceed.setVisible(name in {'SelectAccount', 'CollectPin'})
        self.proceed.setEnabled(True)
        self.retry_button.setVisible(name == 'RequestFailed')
        self.retry_button.setEnabled(True)
        if name == 'SelectAccount':
            self.accounts.clear()
            self.accounts.addItems(self.request.userNames())
            self.proceed.setEnabled(self.accounts.count() > 0)
            self.message.setText('Choose the account on your security key.')
        elif name == 'CollectPin':
            info = self.request.pinRequest()
            verb = {'Set': 'Set', 'Change': 'Change'}.get(info.reason.name, 'Enter')
            detail = '' if info.error.name == 'NoError' else f' Previous attempt: {info.error.name}.'
            attempts = f' Attempts remaining: {info.remainingAttempts}.' if info.remainingAttempts >= 0 else ''
            self.message.setText(f'{verb} the security key PIN (at least {info.minPinLength} characters).{detail}{attempts}')
            self.pin.setFocus()
        elif name == 'RequestFailed':
            reason = self.request.requestFailureReason().name
            detail = 'The authentication request timed out.' if reason == 'Timeout' else f'Authentication failed: {reason}.'
            self.message.setText(detail + ' Retry or cancel to choose another sign-in method.')
        else:
            self.message.setText('Connect and touch your security key when prompted.')

    def submit(self):
        request = self.request
        if request is None or not self.proceed.isEnabled():
            return
        state = request.state().name
        if state == 'CollectPin':
            value = self.pin.text()
            if len(value) < max(1, request.pinRequest().minPinLength):
                return
            if request.pinRequest().reason.name in {"Set", "Change"} and value != self.pin_confirmation.text():
                self.message.setText("The new PIN and confirmation must match.")
                return
            self.proceed.setEnabled(False)
            self.pin.clear()
            self.pin_confirmation.clear()
            request.setPin(value)
        elif state == 'SelectAccount' and self.accounts.currentIndex() >= 0:
            self.proceed.setEnabled(False)
            request.setSelectedAccount(self.accounts.currentText())

    def retry(self):
        if self.request is not None and self.request.state().name == 'RequestFailed':
            self.retry_button.setEnabled(False)
            self.request.retry()

    def request_finished(self, *_args):
        self.request = None
        self.pin.clear()
        self.pin_confirmation.clear()
        self.accounts.clear()
        super().done(QDialog.DialogCode.Rejected)

    def reject(self):
        request = self.request
        self.request_finished()
        if request is not None:
            request.cancel()


def install_web_auth(page, parent=None):
    """Keep the dialog alive per page; never share an auth request across profiles."""
    page.auth_dialog = None

    def requested(request):
        if page.auth_dialog is not None:
            page.auth_dialog.reject()
        dialog = WebAuthDialog(request, parent)
        page.auth_dialog = dialog

        def released(*_args):
            if page.auth_dialog is dialog:
                page.auth_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(released)
        page.destroyed.connect(dialog.request_finished)
        if dialog.request is not None:
            dialog.show()
        else:
            released()

    page.webAuthUxRequested.connect(requested)
