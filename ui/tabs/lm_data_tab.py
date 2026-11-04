"""LM Data cleanup tab."""
from __future__ import annotations
from core.i18n import _

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.lm_data import execute_lm_data_cleanup, preview_lm_data_cleanup
from ui.settings_helper import restore_line_edit, save_line_edit


class LMDataTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.last_preview: dict | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        config = QGroupBox(_("LM Data 정리"))
        form = QFormLayout(config)

        self.json_in = QLineEdit()
        self.smk_folder_in = QLineEdit()
        self.pmk_folder_in = QLineEdit()
        restore_line_edit(self.json_in, "lm_data/json")
        restore_line_edit(self.smk_folder_in, "lm_data/smk_folder")
        restore_line_edit(self.pmk_folder_in, "lm_data/pmk_folder")

        json_row = QHBoxLayout()
        json_row.addWidget(self.json_in)
        btn_json = QPushButton(_("검증 JSON 선택"))
        btn_json.clicked.connect(self.choose_json)
        json_row.addWidget(btn_json)

        smk_row = QHBoxLayout()
        smk_row.addWidget(self.smk_folder_in)
        btn_smk = QPushButton(_("SMK 폴더 선택"))
        btn_smk.clicked.connect(self.choose_smk_folder)
        smk_row.addWidget(btn_smk)

        pmk_row = QHBoxLayout()
        pmk_row.addWidget(self.pmk_folder_in)
        btn_pmk = QPushButton(_("PMK 폴더 선택"))
        btn_pmk.clicked.connect(self.choose_pmk_folder)
        pmk_row.addWidget(btn_pmk)

        form.addRow(_("SKI 검증 리스트 JSON"), json_row)
        form.addRow(_("SMK 폴더"), smk_row)
        form.addRow(_("PMK 폴더"), pmk_row)
        layout.addWidget(config)

        buttons = QHBoxLayout()
        self.btn_preview = QPushButton(_("미리보기"))
        self.btn_execute = QPushButton(_("BK 이동 / PMK 저장"))
        self.btn_preview.clicked.connect(self.preview)
        self.btn_execute.clicked.connect(self.execute)
        buttons.addWidget(self.btn_preview)
        buttons.addWidget(self.btn_execute)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result, stretch=1)

    def choose_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("SKI 검증 리스트 JSON 선택"), "", "JSON Files (*.json)")
        if path:
            self.json_in.setText(path)
            save_line_edit(self.json_in, "lm_data/json")

    def choose_smk_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("SMK 폴더 선택"))
        if path:
            self.smk_folder_in.setText(path)
            save_line_edit(self.smk_folder_in, "lm_data/smk_folder")

    def choose_pmk_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("PMK 폴더 선택"))
        if path:
            self.pmk_folder_in.setText(path)
            save_line_edit(self.pmk_folder_in, "lm_data/pmk_folder")

    def read_paths(self) -> tuple[Path, Path, Path] | None:
        json_path = Path(self.json_in.text().strip())
        smk_folder = Path(self.smk_folder_in.text().strip())
        pmk_folder = Path(self.pmk_folder_in.text().strip())
        if not json_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("SKI 검증 리스트 JSON 파일을 선택하세요."))
            return None
        if not smk_folder.is_dir():
            QMessageBox.warning(self, _("입력 오류"), _("SMK 폴더를 선택하세요."))
            return None
        if not pmk_folder.is_dir():
            QMessageBox.warning(self, _("입력 오류"), _("PMK 폴더를 선택하세요."))
            return None
        save_line_edit(self.json_in, "lm_data/json")
        save_line_edit(self.smk_folder_in, "lm_data/smk_folder")
        save_line_edit(self.pmk_folder_in, "lm_data/pmk_folder")
        return json_path, smk_folder, pmk_folder

    def preview(self) -> None:
        paths = self.read_paths()
        if not paths:
            return
        try:
            self.last_preview = preview_lm_data_cleanup(*paths)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("미리보기 실패"), str(exc))
            return
        self.result.setPlainText(json.dumps(self.last_preview, ensure_ascii=False, indent=2))

    def execute(self) -> None:
        paths = self.read_paths()
        if not paths:
            return
        try:
            preview = preview_lm_data_cleanup(*paths)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("실행 전 점검 실패"), str(exc))
            return
        answer = QMessageBox.question(
            self,
            _("LM Data 정리 실행"),
            (
                f"대상 StripID {preview['target_strip_count']}개\n" +
                f"이동할 SMK {preview['smk_move_count']}개\n" +
                f"수정할 PMK {preview['pmk_update_count']}개\n\n" +
                _("SMK는 BK 폴더로 이동하고, PMK는 백업 후 대상 라인을 삭제합니다. 실행할까요?")
            ),
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = execute_lm_data_cleanup(*paths)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("LM Data 정리 실패"), str(exc))
            return
        self.last_preview = result
        self.result.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        QMessageBox.information(self, _("완료"), _("LM Data 정리가 완료되었습니다."))
