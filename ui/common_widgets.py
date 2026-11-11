"""Common UI helpers shared across tabs."""
from __future__ import annotations
from core.i18n import _

from typing import Callable, Iterable

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QPushButton, QWidget

from ui.settings_helper import save_line_edit, save_setting_text
from workers.strip_workers import ModelLotListThread


def start_line_edit_lot_lookup(
    owner: QWidget,
    model: str,
    lot_in: QLineEdit,
    setting_key: str,
    buttons: Iterable[QPushButton] = (),
    logger: Callable[[str], None] | None = None,
) -> None:
    model = model.strip()
    if not model:
        QMessageBox.warning(owner, _("입력 오류"), _("모델명을 입력하세요."))
        return
    for button in buttons:
        button.setEnabled(False)
    save_setting_text(setting_key.rsplit("/", 1)[0] + "/model", model)
    thread = ModelLotListThread("lots", model)
    owner.lot_lookup_thread = thread  # type: ignore[attr-defined]
    thread.finished.connect(
        lambda mode, values: finish_line_edit_lot_lookup(
            owner, model, lot_in, setting_key, list(buttons), logger, values
        )
    )
    thread.failed.connect(lambda message: fail_line_edit_lot_lookup(owner, list(buttons), message))
    from ui.worker_lifetime import start_worker
    start_worker(owner, thread)


def finish_line_edit_lot_lookup(
    owner: QWidget,
    model: str,
    lot_in: QLineEdit,
    setting_key: str,
    buttons: list[QPushButton],
    logger: Callable[[str], None] | None,
    values: list[str],
) -> None:
    for button in buttons:
        button.setEnabled(True)
    values = sorted(values)
    if not values:
        QMessageBox.information(owner, _("LOT 검색"), _("검색된 LOT가 없습니다."))
        return
    current = lot_in.text().strip()
    index = values.index(current) if current in values else 0
    selected, ok = QInputDialog.getItem(owner, _("LOT 검색"), f"{model} LOT 선택", values, index, False)
    if ok and selected:
        lot_in.setText(selected)
        save_line_edit(lot_in, setting_key)
        lot_in.editingFinished.emit()
        if logger:
            logger(f"LOT 선택: {selected}")


def fail_line_edit_lot_lookup(owner: QWidget, buttons: list[QPushButton], message: str) -> None:
    for button in buttons:
        button.setEnabled(True)
    QMessageBox.critical(owner, _("LOT 검색 실패"), message)
