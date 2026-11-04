"""Gathering strip search tab."""
from __future__ import annotations
from core.i18n import _

from typing import Optional

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_MODEL
from ui.settings_helper import restore_line_edit, save_line_edit
from workers.gather_workers import StripGatherSearchThread


class GatheringStripSearchTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.search_thread: Optional[StripGatherSearchThread] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        box = QGroupBox(_("게더링 설정 경로 StripID 검색"))
        row = QHBoxLayout(box)
        self.model_in = QLineEdit(DEFAULT_MODEL)
        restore_line_edit(self.model_in, "gather_search/model", DEFAULT_MODEL)
        self.strip_in = QLineEdit()
        restore_line_edit(self.strip_in, "gather_search/strip_id")
        self.strip_in.setPlaceholderText(_("예: RX125E000800-4A06"))
        self.btn_search = QPushButton(_("검색"))
        self.btn_search.clicked.connect(self.search)
        row.addWidget(QLabel(_("모델")))
        row.addWidget(self.model_in)
        row.addWidget(QLabel("StripID"))
        row.addWidget(self.strip_in)
        row.addWidget(self.btn_search)
        layout.addWidget(box)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result, stretch=1)

    def search(self) -> None:
        strip_id = self.strip_in.text().strip().upper()
        model = self.model_in.text().strip()
        if not strip_id or "-" not in strip_id:
            QMessageBox.warning(self, _("입력 오류"), _("{lot}{stripID} 포맷의 StripID를 입력하세요."))
            return
        save_line_edit(self.model_in, "gather_search/model")
        save_line_edit(self.strip_in, "gather_search/strip_id")
        self.btn_search.setEnabled(False)
        self.result.setPlainText(f"{strip_id} 검색 시작\n")
        self.search_thread = StripGatherSearchThread(strip_id, model)
        self.search_thread.progress.connect(self.result.append)
        self.search_thread.finished.connect(self.on_finished)
        self.search_thread.failed.connect(self.on_failed)
        self.search_thread.start()

    def on_finished(self, rows: list[dict]) -> None:
        self.btn_search.setEnabled(True)
        hits = [row for row in rows if not row.get("error")]
        errors = [row for row in rows if row.get("error")]
        lines = ["", f"검색 완료: Hit {len(hits)}개 / 오류 {len(errors)}개", ""]
        for row in hits:
            lines.append(f"[{row['mode']}] {row['scope']} | 좌표 {row['coord_count']}개 | 라인 {row['defect_line_count']}개")
            lines.append(str(row["path"]))
            if row.get("codes"):
                lines.append("  Codes: " + ", ".join(f"{code}:{count}" for code, count in row["codes"].items()))
            if row.get("defect_lines"):
                lines.append("  Lines:")
                lines.extend(f"    {line}" for line in row["defect_lines"][:30])
            lines.append("")
        if errors:
            lines.extend([_("[오류/경로 없음]")])
            for row in errors[:100]:
                lines.append(f"[{row.get('mode')}] {row.get('scope')} | {row.get('path')} | {row.get('error')}")
        self.result.append("\n".join(lines))

    def on_failed(self, message: str) -> None:
        self.btn_search.setEnabled(True)
        QMessageBox.critical(self, _("검색 실패"), message)
