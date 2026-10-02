"""Question boxes with explicit French buttons.

Qt's standard buttons ("Yes", "No", "Cancel") are only translated when the Qt
translation files are found at run time. A destructive question must not
depend on that: its buttons are worded here, and say what they do.
"""

from PySide6.QtWidgets import QDialogButtonBox, QMessageBox


def ask(
    parent,
    title: str,
    text: str,
    accept: str = "Oui",
    reject: str = "Non",
    *,
    default_accept: bool = False,
    destructive: bool = False,
    icon: QMessageBox.Icon = QMessageBox.Icon.Question,
) -> bool:
    """Ask a two-way question; True when the `accept` button was chosen.

    Escape and closing the box always mean `reject`. A destructive action is
    never the default button.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if destructive else icon)
    box.setWindowTitle(title)
    box.setText(text)
    accept_button = box.addButton(
        accept,
        QMessageBox.ButtonRole.DestructiveRole if destructive else QMessageBox.ButtonRole.AcceptRole)
    reject_button = box.addButton(reject, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(accept_button if default_accept and not destructive else reject_button)
    box.setEscapeButton(reject_button)
    box.exec()
    return box.clickedButton() is accept_button


def french_button_box(accept: str = "Enregistrer", reject: str = "Annuler") -> QDialogButtonBox:
    """An accept/reject button box for a dialog, with French labels.

    Connect its `accepted` and `rejected` signals as for the standard one.
    """
    box = QDialogButtonBox()
    box.addButton(accept, QDialogButtonBox.ButtonRole.AcceptRole).setDefault(True)
    box.addButton(reject, QDialogButtonBox.ButtonRole.RejectRole)
    return box


def ask_save(parent, title: str, text: str) -> str:
    """Ask what to do with unsaved changes: "save", "discard" or "cancel".

    Escape and closing the box mean "cancel": nothing is lost by accident.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    save = box.addButton("Enregistrer", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("Ne pas enregistrer", QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return "save"
    if clicked is discard:
        return "discard"
    return "cancel"
