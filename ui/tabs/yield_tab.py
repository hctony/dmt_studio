"""Yield calculation tab."""
from __future__ import annotations
from core.i18n import _

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_MODEL
from core.gather import load_latest_process_blocks, parse_gather_file
from core.ivs import load_ivs_dummy_scrap_strips
from core.skipdata import load_skipdata_folder
from core.utils import split_path_list, to_json_plain
from core.yield_calc import (
    build_strip_json_validation,
    calculate_yield_summary_by_process,
    calculate_yield_summary_many,
    load_strip_id_json,
    merge_process_code_distribution,
    parse_yield_txt,
    split_170_added_distribution,
    sample_bbt_rpt_folder,
)
from ui.settings_helper import (
    load_yield_model_settings,
    restore_line_edit,
    save_line_edit,
    save_setting_text,
    save_yield_model_settings,
    setting_text,
)


class YieldTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.summary: dict = {}
        self.txt_paths: list[Path] = []
        self._build_ui()

    def apply_workflow_inputs(self, force=False) -> None:
        from core.yield_handoff import latest, PATH_FIELDS
        entry = latest()
        if not entry:
            self.handoff_status.setText(_("이전 완료 데이터가 없습니다. IVS 조회 또는 게더링을 먼저 실행하세요."))
            return
        identity = entry['model'] + '/' + entry['lot']
        changed = setting_text('yield/handoff/context', '') != identity
        if changed or force:
            self.model_in.setText(entry['model'])
            self.units_per_strip_in.clear()
            self.xout_limit_in.clear()
            self.restore_model_units()
            # Model profiles can contain an older LOT's paths. Keep quantities,
            # but only import paths belonging to this completed context.
            for name in PATH_FIELDS:
                getattr(self, name).clear()
            self.txt_paths = []
        for name, item in entry['fields'].items():
            key = 'yield/handoff/' + identity + '/' + name
            if changed or force or setting_text(key, '') != item['revision']:
                value = item['value']
                if name in ('vrs_txt_in', 'fvi_txt_in', 'ivs_txt_in', 'ivs_json_in', 'strip_json_in') and not Path(value).is_file():
                    value = ''
                getattr(self, name).setText(value)
                save_setting_text(key, item['revision'])
        save_setting_text('yield/handoff/context', identity)
        settings = {'model_in': 'model', 'vrs_txt_in': 'vrs_txt_paths', 'fvi_txt_in': 'fvi_txt_paths',
                    'ivs_txt_in': 'ivs_txt_paths', 'ivs_json_in': 'ivs_json_paths', 'txt_in': 'txt_paths',
                    'strip_json_in': 'strip_json', 'skipdata_in': 'skipdata', 'ski_in': 'ski',
                    'units_per_strip_in': 'units_per_strip', 'xout_limit_in': 'xout_limit'}
        for name, setting in settings.items():
            save_line_edit(getattr(self, name), 'yield/' + setting)
        self.handoff_status.setText(_("최근 완료 데이터 연결") + ': ' + identity + '\n' +
            _("빈 항목은 아직 완료 데이터가 없습니다. 확인 후 수율을 계산하세요."))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.handoff_status = QLabel(_("수율 화면 진입 시 최근 완료 데이터를 자동으로 연결합니다."))
        self.handoff_status.setWordWrap(True)
        layout.addWidget(self.handoff_status)
        self.btn_workflow_inputs = QPushButton(_("최근 완료 데이터 다시 불러오기"))
        self.btn_workflow_inputs.clicked.connect(lambda: self.apply_workflow_inputs(force=True))
        layout.addWidget(self.btn_workflow_inputs)
        config = QGroupBox(_("수율 계산 입력"))
        form = QFormLayout(config)
        self.model_in = QLineEdit(DEFAULT_MODEL)
        self.vrs_txt_in = QLineEdit()
        self.fvi_txt_in = QLineEdit()
        self.ivs_txt_in = QLineEdit()
        self.ivs_json_in = QLineEdit()
        self.txt_in = QLineEdit()
        self.strip_json_in = QLineEdit()
        self.skipdata_in = QLineEdit()
        self.ski_in = QLineEdit()
        self.units_per_strip_in = QLineEdit("88")
        self.xout_limit_in = QLineEdit("13")
        restore_line_edit(self.model_in, "yield/model", DEFAULT_MODEL)
        self.restore_model_units()
        restore_line_edit(self.vrs_txt_in, "yield/vrs_txt_paths")
        restore_line_edit(self.fvi_txt_in, "yield/fvi_txt_paths")
        restore_line_edit(self.ivs_txt_in, "yield/ivs_txt_paths")
        restore_line_edit(self.ivs_json_in, "yield/ivs_json_paths")
        restore_line_edit(self.txt_in, "yield/txt_paths")
        restore_line_edit(self.strip_json_in, "yield/strip_json")
        restore_line_edit(self.skipdata_in, "yield/skipdata")
        restore_line_edit(self.ski_in, "yield/ski")
        restore_line_edit(self.units_per_strip_in, "yield/units_per_strip", "88")
        restore_line_edit(self.xout_limit_in, "yield/xout_limit", "13")
        self.restore_model_units()
        self.model_in.editingFinished.connect(self.restore_model_units)
        self.units_per_strip_in.editingFinished.connect(self.save_model_yield_settings)
        self.xout_limit_in.editingFinished.connect(self.save_model_yield_settings)

        vrs_row = QHBoxLayout()
        vrs_row.addWidget(self.vrs_txt_in)
        btn_vrs_txt = QPushButton(_("VRS TXT 선택"))
        btn_vrs_txt.clicked.connect(self.choose_vrs_txt)
        vrs_row.addWidget(btn_vrs_txt)

        fvi_row = QHBoxLayout()
        fvi_row.addWidget(self.fvi_txt_in)
        btn_fvi_txt = QPushButton(_("FVI TXT 선택"))
        btn_fvi_txt.clicked.connect(self.choose_fvi_txt)
        fvi_row.addWidget(btn_fvi_txt)

        ivs_row = QHBoxLayout()
        ivs_row.addWidget(self.ivs_txt_in)
        btn_ivs_txt = QPushButton(_("IVS TXT 선택"))
        btn_ivs_txt.clicked.connect(self.choose_ivs_txt)
        ivs_row.addWidget(btn_ivs_txt)

        ivs_json_row = QHBoxLayout()
        ivs_json_row.addWidget(self.ivs_json_in)
        btn_ivs_json = QPushButton(_("IVS JSON 선택"))
        btn_ivs_json.clicked.connect(self.choose_ivs_json)
        ivs_json_row.addWidget(btn_ivs_json)

        txt_row = QHBoxLayout()
        txt_row.addWidget(self.txt_in)
        btn_txt = QPushButton(_("TXT 선택"))
        btn_txt.clicked.connect(self.choose_txt)
        txt_row.addWidget(btn_txt)

        json_row = QHBoxLayout()
        json_row.addWidget(self.strip_json_in)
        btn_json = QPushButton(_("StripID JSON 선택"))
        btn_json.clicked.connect(self.choose_json)
        json_row.addWidget(btn_json)

        skipdata_row = QHBoxLayout()
        skipdata_row.addWidget(self.skipdata_in)
        btn_skipdata = QPushButton(_("BBT/Skipdata 폴더 선택"))
        btn_skipdata.clicked.connect(self.choose_skipdata)
        skipdata_row.addWidget(btn_skipdata)

        ski_row = QHBoxLayout()
        ski_row.addWidget(self.ski_in)
        btn_ski = QPushButton(_("SKI 파일 선택"))
        btn_ski.clicked.connect(self.choose_ski)
        ski_row.addWidget(btn_ski)

        form.addRow(_("모델명"), self.model_in)
        form.addRow(_("VRS TXT들"), vrs_row)
        form.addRow(_("FVI TXT들"), fvi_row)
        form.addRow(_("IVS TXT들"), ivs_row)
        form.addRow(_("IVS JSON들(Dummy 9999 양폐)"), ivs_json_row)
        form.addRow(_("생성 TXT들(구 방식)"), txt_row)
        form.addRow("StripID JSON", json_row)
        form.addRow(_("BBT/Skipdata 폴더"), skipdata_row)
        form.addRow(_("최종 SKI 파일"), ski_row)
        form.addRow(_("스트립당 Unit 수"), self.units_per_strip_in)
        form.addRow(_("X-out 초과 기준"), self.xout_limit_in)
        layout.addWidget(config)

        self.btn_calc = QPushButton(_("수율 계산"))
        self.btn_calc.clicked.connect(self.calculate)
        action_row = QHBoxLayout()
        action_row.addWidget(self.btn_calc)
        self.btn_validate_strip_json = QPushButton(_("StripID JSON 검증"))
        self.btn_validate_strip_json.clicked.connect(self.validate_strip_json)
        action_row.addWidget(self.btn_validate_strip_json)
        self.btn_debug_bbt = QPushButton(_("BBT/RPT 로딩 진단"))
        self.btn_debug_bbt.clicked.connect(self.debug_bbt_loading)
        action_row.addWidget(self.btn_debug_bbt)
        self.btn_export_json = QPushButton(_("결과 JSON 내보내기"))
        self.btn_export_json.clicked.connect(self.export_summary_json)
        action_row.addWidget(self.btn_export_json)
        
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel(_("Excel 포함 공정:")))
        self.chk_excel_bbt = QCheckBox("BBT")
        self.chk_excel_vrs = QCheckBox("VRS")
        self.chk_excel_fvi = QCheckBox("FVI")
        self.chk_excel_ivs = QCheckBox("IVS")
        for chk in [self.chk_excel_bbt, self.chk_excel_vrs, self.chk_excel_fvi, self.chk_excel_ivs]:
            chk.setChecked(True)
            filter_layout.addWidget(chk)
        action_row.addLayout(filter_layout)
        
        self.btn_export_excel = QPushButton(_("성적서 엑셀 내보내기"))
        self.btn_export_excel.clicked.connect(self.export_excel)
        action_row.addWidget(self.btn_export_excel)
        self.btn_code_preview = QPushButton(_("코드 미리보기"))
        self.btn_code_preview.clicked.connect(self.preview_report_codes)
        action_row.addWidget(self.btn_code_preview)
        self.btn_report_template = QPushButton(_("성적서 템플릿 변경"))
        self.btn_report_template.clicked.connect(self.choose_report_template)
        action_row.addWidget(self.btn_report_template)
        action_row.addStretch()
        layout.addLayout(action_row)

        table_filter_row = QHBoxLayout()
        table_filter_row.addWidget(QLabel(_("하단 테이블")))
        self.radio_table_all = QRadioButton(_("전체"))
        self.radio_table_mismatch = QRadioButton(_("불일치만"))
        self.radio_table_all.setChecked(True)
        self.radio_table_all.toggled.connect(self.apply_table_filter)
        self.radio_table_mismatch.toggled.connect(self.apply_table_filter)
        table_filter_row.addWidget(self.radio_table_all)
        table_filter_row.addWidget(self.radio_table_mismatch)
        table_filter_row.addStretch()
        layout.addLayout(table_filter_row)

        splitter = QSplitter(Qt.Vertical)
        self.result_tabs = QTabWidget()
        self.result = QTextEdit()
        self.report_result = QTextEdit()
        self.result.setReadOnly(True)
        self.report_result.setReadOnly(True)
        self.result_tabs.addTab(self.result, _("기본/판정"))
        self.result_tabs.addTab(self.report_result, _("성적서 집계"))
        splitter.addWidget(self.result_tabs)
        self.xout_table = QTableWidget(0, 2)
        self.xout_table.setHorizontalHeaderLabels([_("X-out 수"), _("Strip 수")])
        splitter.addWidget(self.xout_table)
        self.strip_table = QTableWidget(0, 8)
        self.strip_table.setHorizontalHeaderLabels(
            ["StripID", _("합산 X-out"), _("BBT 불량"), _("추가 불량"), _("게더링 원본"), _("BBT 중복"), _("판정"), _("TXT 블록 수")]
        )
        splitter.addWidget(self.strip_table)
        splitter.setStretchFactor(2, 2)
        layout.addWidget(splitter, stretch=1)

    def _get_default_dir(self, line_edit: QLineEdit) -> str:
        text = line_edit.text().split(";")[0].strip()
        if text:
            p = Path(text)
            if p.exists():
                return str(p.parent.parent if p.is_file() else p.parent)
            return str(p.parent)
        return ""

    def choose_txt(self) -> None:
        default_dir = self._get_default_dir(self.txt_in)
        paths, _ext = QFileDialog.getOpenFileNames(self, _("생성 TXT 선택"), default_dir, "Text Files (*.txt);;All Files (*.*)")
        if paths:
            self.txt_paths = [Path(path) for path in paths]
            self.txt_in.setText("; ".join(paths))
            save_line_edit(self.txt_in, "yield/txt_paths")

    def choose_vrs_txt(self) -> None:
        default_dir = self._get_default_dir(self.vrs_txt_in)
        paths, _ext = QFileDialog.getOpenFileNames(self, _("VRS TXT 선택"), default_dir, "Text Files (*.txt);;All Files (*.*)")
        if paths:
            self.vrs_txt_in.setText("; ".join(paths))
            save_line_edit(self.vrs_txt_in, "yield/vrs_txt_paths")
            self.save_model_yield_settings()

    def choose_fvi_txt(self) -> None:
        default_dir = self._get_default_dir(self.fvi_txt_in)
        paths, _ext = QFileDialog.getOpenFileNames(self, _("FVI TXT 선택"), default_dir, "Text Files (*.txt);;All Files (*.*)")
        if paths:
            self.fvi_txt_in.setText("; ".join(paths))
            save_line_edit(self.fvi_txt_in, "yield/fvi_txt_paths")
            self.save_model_yield_settings()

    def choose_ivs_txt(self) -> None:
        default_dir = self._get_default_dir(self.ivs_txt_in)
        paths, _ext = QFileDialog.getOpenFileNames(self, _("IVS TXT 선택"), default_dir, "Text Files (*.txt);;All Files (*.*)")
        if paths:
            self.ivs_txt_in.setText("; ".join(paths))
            save_line_edit(self.ivs_txt_in, "yield/ivs_txt_paths")
            self.save_model_yield_settings()

    def choose_ivs_json(self) -> None:
        default_dir = self._get_default_dir(self.ivs_json_in)
        paths, _ext = QFileDialog.getOpenFileNames(self, _("IVS JSON 선택"), default_dir, "JSON Files (*.json);;All Files (*.*)")
        if paths:
            self.ivs_json_in.setText("; ".join(paths))
            save_line_edit(self.ivs_json_in, "yield/ivs_json_paths")
            self.save_model_yield_settings()

    def restore_model_units(self) -> None:
        model = self.model_in.text().strip()
        if not model:
            return
        model_settings = load_yield_model_settings().get(model, {})
        fields = [
            ("units_per_strip", "units_per_strip_in", "model_units"),
            ("xout_limit", "xout_limit_in", "model_xout_limit"),
            ("vrs_txt_paths", "vrs_txt_in", "model_vrs_txt"),
            ("fvi_txt_paths", "fvi_txt_in", "model_fvi_txt"),
            ("ivs_txt_paths", "ivs_txt_in", "model_ivs_txt"),
            ("ivs_json_paths", "ivs_json_in", "model_ivs_json"),
            ("skipdata_path", "skipdata_in", "model_skipdata"),
            ("ski_path", "ski_in", "model_ski"),
        ]
        for key, attribute, setting in fields:
            if key in model_settings:
                # Saved blanks must clear a previous model's path, not revive it.
                getattr(self, attribute).setText(str(model_settings[key]).strip())
            else:
                value = setting_text(f"yield/{setting}/{model}", "")
                if value:
                    getattr(self, attribute).setText(value)

    def save_model_yield_settings(self) -> None:
        model = self.model_in.text().strip()
        if not model:
            return
        model_settings = load_yield_model_settings()
        model_settings[model] = {
            "units_per_strip": self.units_per_strip_in.text().strip(),
            "xout_limit": self.xout_limit_in.text().strip(),
            "vrs_txt_paths": self.vrs_txt_in.text().strip(),
            "fvi_txt_paths": self.fvi_txt_in.text().strip(),
            "ivs_txt_paths": self.ivs_txt_in.text().strip(),
            "ivs_json_paths": self.ivs_json_in.text().strip(),
            "skipdata_path": self.skipdata_in.text().strip(),
            "ski_path": self.ski_in.text().strip(),
        }
        save_yield_model_settings(model_settings)
        save_setting_text(f"yield/model_units/{model}", self.units_per_strip_in.text().strip())
        save_setting_text(f"yield/model_xout_limit/{model}", self.xout_limit_in.text().strip())
        save_setting_text(f"yield/model_vrs_txt/{model}", self.vrs_txt_in.text().strip())
        save_setting_text(f"yield/model_fvi_txt/{model}", self.fvi_txt_in.text().strip())
        save_setting_text(f"yield/model_ivs_txt/{model}", self.ivs_txt_in.text().strip())
        save_setting_text(f"yield/model_ivs_json/{model}", self.ivs_json_in.text().strip())
        save_setting_text(f"yield/model_skipdata/{model}", self.skipdata_in.text().strip())
        save_setting_text(f"yield/model_ski/{model}", self.ski_in.text().strip())

    def choose_json(self) -> None:
        default_dir = self._get_default_dir(self.strip_json_in)
        path, _ext = QFileDialog.getOpenFileName(self, _("StripID JSON 선택"), default_dir, "JSON Files (*.json)")
        if path:
            self.strip_json_in.setText(path)
            save_line_edit(self.strip_json_in, "yield/strip_json")

    def choose_skipdata(self) -> None:
        default_dir = self._get_default_dir(self.skipdata_in)
        path = QFileDialog.getExistingDirectory(self, _("BBT/Skipdata 폴더 선택"), default_dir)
        if path:
            self.skipdata_in.setText(path)
            save_line_edit(self.skipdata_in, "yield/skipdata")
            self.save_model_yield_settings()

    def choose_ski(self) -> None:
        default_dir = self._get_default_dir(self.ski_in)
        path, _ext = QFileDialog.getOpenFileName(self, _("최종 SKI 파일 선택"), default_dir, "SKI Files (*.ski);;All Files (*.*)")
        if path:
            self.ski_in.setText(path)
            save_line_edit(self.ski_in, "yield/ski")
            self.save_model_yield_settings()

    def get_process_filters(self) -> list[str]:
        filters: list[str] = []
        if self.chk_excel_bbt.isChecked(): filters.append("BBT")
        if self.chk_excel_vrs.isChecked(): filters.append("VRS")
        if self.chk_excel_fvi.isChecked(): filters.append("FVI")
        if self.chk_excel_ivs.isChecked(): filters.append("IVS")
        return filters
    def calculate(self) -> None:
        txt_paths = split_path_list(self.txt_in.text())
        vrs_paths = split_path_list(self.vrs_txt_in.text())
        fvi_paths = split_path_list(self.fvi_txt_in.text())
        ivs_paths = split_path_list(self.ivs_txt_in.text())
        ivs_json_paths = split_path_list(self.ivs_json_in.text())
        json_path = Path(self.strip_json_in.text().strip())
        skipdata_text = self.skipdata_in.text().strip()
        skipdata_path = Path(skipdata_text) if skipdata_text else None
        ski_text = self.ski_in.text().strip()
        ski_path = Path(ski_text) if ski_text else None
        process_mode = bool(vrs_paths or fvi_paths or ivs_paths)
        selected_paths = vrs_paths + fvi_paths + ivs_paths if process_mode else txt_paths
        if not selected_paths or any(not path.is_file() for path in selected_paths):
            QMessageBox.warning(self, _("입력 오류"), _("VRS/FVI/IVS TXT 또는 생성 TXT 파일을 하나 이상 선택하세요."))
            return
        if any(not path.is_file() for path in ivs_json_paths):
            QMessageBox.warning(self, _("입력 오류"), _("IVS JSON 파일 경로를 확인하세요."))
            return
        if skipdata_path and not skipdata_path.is_dir():
            QMessageBox.warning(self, _("입력 오류"), _("Skipdata 폴더를 선택하세요."))
            return
        if ski_path and not ski_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("최종 SKI 파일을 선택하세요."))
            return
        try:
            units_per_strip = int(self.units_per_strip_in.text().strip())
            xout_limit = int(self.xout_limit_in.text().strip())
        except ValueError:
            QMessageBox.warning(self, _("입력 오류"), _("스트립당 Unit 수와 X-out 기준은 숫자여야 합니다."))
            return
        try:
            save_line_edit(self.txt_in, "yield/txt_paths")
            save_line_edit(self.vrs_txt_in, "yield/vrs_txt_paths")
            save_line_edit(self.fvi_txt_in, "yield/fvi_txt_paths")
            save_line_edit(self.ivs_txt_in, "yield/ivs_txt_paths")
            save_line_edit(self.ivs_json_in, "yield/ivs_json_paths")
            save_line_edit(self.model_in, "yield/model")
            save_line_edit(self.strip_json_in, "yield/strip_json")
            save_line_edit(self.skipdata_in, "yield/skipdata")
            save_line_edit(self.ski_in, "yield/ski")
            save_line_edit(self.units_per_strip_in, "yield/units_per_strip")
            self.save_model_yield_settings()
            save_line_edit(self.xout_limit_in, "yield/xout_limit")
            json_lot_id = ""
            if json_path.is_file():
                try:
                    json_lot_id, _ext = load_strip_id_json(json_path)
                except Exception:
                    json_lot_id = ""
            ivs_dummy_scrap_strips = load_ivs_dummy_scrap_strips(ivs_json_paths, json_lot_id)
            if process_mode:
                self.summary = calculate_yield_summary_by_process(
                    vrs_paths,
                    fvi_paths,
                    ivs_paths,
                    ivs_json_paths,
                    json_path if json_path.is_file() else None,
                    units_per_strip,
                    xout_limit,
                    skipdata_path,
                    ski_path,
                    self.get_process_filters(),
                    ivs_dummy_scrap_strips,
                )
            else:
                self.summary = calculate_yield_summary_many(
                    txt_paths,
                    json_path if json_path.is_file() else None,
                    units_per_strip,
                    xout_limit,
                    skipdata_path,
                    ski_path,
                    ivs_dummy_scrap_strips,
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("계산 실패"), str(exc))
            return
        self._report_input_snapshot = self.report_input_values()
        self.refresh_result()
        audit=self.summary.get('its_validation',{})
        if audit.get('enabled') and (not audit.get('auto_pass') or audit.get('untracked_strips')):
            from core.its_validation import format_audits
            QMessageBox.warning(self,_('ITS 검증 미완료'),format_audits(audit)+'\n'+_('소스 누락 또는 SKI 갱신을 확인한 뒤 수율 계산을 다시 실행하세요.'))

    def report_input_values(self):
        names=('model_in','vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','txt_in','strip_json_in',
               'skipdata_in','ski_in','units_per_strip_in','xout_limit_in')
        return tuple(getattr(self,name).text().strip() for name in names)+tuple(self.get_process_filters() or [])

    def report_ready(self):
        if not self.summary:
            QMessageBox.warning(self,_("저장 없음"),_("먼저 수율 계산을 실행하세요."));return False
        saved=getattr(self,'_report_input_snapshot',None)
        if saved is not None and saved!=self.report_input_values():
            QMessageBox.warning(self,_("입력 변경"),_("입력이 변경되었습니다. 수율을 다시 계산한 뒤 미리보기·성적서를 생성하세요."));return False
        return True

    def preview_report_codes(self):
        if not self.report_ready():return
        from core.yield_calc import build_yield_report_aggregation
        from ui.report_preview import ReportPreviewDialog
        self.report_preview_dialog=ReportPreviewDialog(self.summary,build_yield_report_aggregation(self.summary,self.model_in.text().strip()),self)
        self.report_preview_dialog.show()

    def validate_strip_json(self) -> None:
        json_path = Path(self.strip_json_in.text().strip())
        txt_paths = split_path_list(self.txt_in.text())
        vrs_paths = split_path_list(self.vrs_txt_in.text())
        fvi_paths = split_path_list(self.fvi_txt_in.text())
        ivs_paths = split_path_list(self.ivs_txt_in.text())
        process_mode = bool(vrs_paths or fvi_paths or ivs_paths)
        selected_paths = vrs_paths + fvi_paths + ivs_paths if process_mode else txt_paths
        if not json_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("검증할 StripID JSON 파일을 선택하세요."))
            return
        if not selected_paths or any(not path.is_file() for path in selected_paths):
            QMessageBox.warning(self, _("입력 오류"), _("검증할 게더링 TXT 파일을 선택하세요."))
            return
        try:
            lot_id, strip_json_ids = load_strip_id_json(json_path)
            if process_mode:
                vrs_blocks, _ext = load_latest_process_blocks(vrs_paths, lot_id) if vrs_paths else ({}, 0)
                fvi_blocks, _ext = load_latest_process_blocks(fvi_paths, lot_id) if fvi_paths else ({}, 0)
                ivs_blocks, _ext = load_latest_process_blocks(ivs_paths, lot_id) if ivs_paths else ({}, 0)
                gather_ids = set(vrs_blocks) | set(fvi_blocks) | set(ivs_blocks)
            else:
                gather_ids = set()
                for txt_path in txt_paths:
                    gather_ids.update(parse_yield_txt(txt_path).keys())
            validation = build_strip_json_validation(strip_json_ids, gather_ids, lot_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("StripID JSON 검증 실패"), str(exc))
            return
        self.show_strip_json_validation(lot_id, validation)

    def debug_bbt_loading(self) -> None:
        skipdata_text = self.skipdata_in.text().strip()
        if not skipdata_text:
            QMessageBox.warning(self, _("입력 오류"), _("Skipdata 폴더를 먼저 선택하세요."))
            return
        skipdata_path = Path(skipdata_text)
        txt_paths = split_path_list(self.txt_in.text())
        vrs_paths = split_path_list(self.vrs_txt_in.text())
        fvi_paths = split_path_list(self.fvi_txt_in.text())
        ivs_paths = split_path_list(self.ivs_txt_in.text())
        process_mode = bool(vrs_paths or fvi_paths or ivs_paths)
        selected_paths = vrs_paths + fvi_paths + ivs_paths if process_mode else txt_paths
        json_path = Path(self.strip_json_in.text().strip())
        lot_id = ""
        if json_path.is_file():
            try:
                lot_id, _ext = load_strip_id_json(json_path)
            except Exception:
                lot_id = ""
        try:
            folder_probe = sample_bbt_rpt_folder(skipdata_path)
            bbt_map = load_skipdata_folder(skipdata_path)
            gather_ids: set[str] = set()
            for txt_path in selected_paths:
                if not txt_path.is_file():
                    continue
                if process_mode:
                    gather_ids.update(parse_gather_file(txt_path, lot_id).keys())
                else:
                    gather_ids.update(parse_yield_txt(txt_path).keys())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("BBT/RPT 진단 실패"), str(exc))
            return
        matched = sorted(set(bbt_map) & gather_ids)
        bbt_only = sorted(set(bbt_map) - gather_ids)
        gather_only = sorted(gather_ids - set(bbt_map))
        lines = [
            _("[BBT/RPT 로딩 진단]"),
            f"Skipdata 폴더: {skipdata_path}",
            f"폴더 존재: {folder_probe.get('exists')}",
            f".rpt 파일 수: {folder_probe.get('rpt_files', 0)}개",
            f"파싱된 BBT Strip: {len(bbt_map)}개",
            f"파싱된 BBT 좌표: {sum(len(coords) for coords in bbt_map.values())}개",
            f"게더링 대상 Strip: {len(gather_ids)}개",
            f"BBT ∩ 게더링 Strip: {len(matched)}개",
            f"BBT에만 있는 Strip: {len(bbt_only)}개",
            f"게더링에만 있는 Strip: {len(gather_only)}개",
            "",
            _("[파싱된 BBT 샘플 30개]"),
        ]
        for strip_id in sorted(bbt_map)[:30]:
            lines.append(f"{strip_id}: {len(bbt_map[strip_id])}개")
        lines.extend(["", _("[RPT 파일 샘플]")])
        for sample in folder_probe.get("samples", []):
            lines.append(
                f"{sample['stem']} | 좌표라인 {sample['coord_count']} | 전체라인 {sample['line_count']} | "
                f"헤더 {', '.join(sample['strip_headers']) if sample['strip_headers'] else '-'} | {sample['file']}"
            )
        if folder_probe.get("errors"):
            lines.extend(["", _("[에러]"), *folder_probe["errors"][:20]])
        lines.extend(
            [
                "",
                _("[BBT에만 있는 Strip 상위 100개]"),
                ", ".join(bbt_only[:100]) if bbt_only else "-",
                "",
                _("[게더링에만 있는 Strip 상위 100개]"),
                ", ".join(gather_only[:100]) if gather_only else "-",
            ]
        )
        self.result.setPlainText("\n".join(lines))

    def show_strip_json_validation(self, lot_id: str, validation: dict) -> None:
        self.result.setPlainText(
            f"LOT: {lot_id}\n"
            f"StripID JSON Strip: {validation['json_strip_count']}개\n"
            f"게더링 데이터 Strip: {validation['gather_strip_count']}개\n"
            f"JSON에는 있으나 게더링 데이터에 없는 StripID: {len(validation['json_only_strips'])}개\n"
            f"게더링 데이터에는 있으나 JSON에 없는 StripID: {len(validation['gather_only_strips'])}개\n\n"
            f"JSON 혼입 LOT StripID: {len(validation.get('json_mixed_lot_strips', []))}개\n"
            f"게더링 혼입 LOT StripID: {len(validation.get('gather_mixed_lot_strips', []))}개\n\n"
            f"[JSON only]\n{', '.join(validation['json_only_strips']) if validation['json_only_strips'] else '-'}\n\n"
            f"[Gathering only]\n{', '.join(validation['gather_only_strips']) if validation['gather_only_strips'] else '-'}\n\n"
            f"[JSON 혼입 LOT]\n{', '.join(validation.get('json_mixed_lot_strips', [])) if validation.get('json_mixed_lot_strips') else '-'}\n\n"
            f"[게더링 혼입 LOT]\n{', '.join(validation.get('gather_mixed_lot_strips', [])) if validation.get('gather_mixed_lot_strips') else '-'}"
        )

    def export_summary_json(self) -> None:
        if not self.summary:
            QMessageBox.warning(self, _("저장 없음"), _("먼저 수율 계산을 실행하세요."))
            return
        lot = str(self.summary.get("lot_id", "yield_summary")).strip() or "yield_summary"
        path, _ext = QFileDialog.getSaveFileName(
            self,
            _("수율 계산 결과 JSON 저장"),
            f"{lot}_yield_summary.json",
            "JSON Files (*.json)",
        )
        if not path:
            return
        summary = to_json_plain(self.summary)
        process_defect_summary = summary.get("merged_process_defect_distribution") or merge_process_code_distribution(
            summary.get("process_code_distribution", {})
        )
        split_170_summary = summary.get("split_170_added_distribution") or split_170_added_distribution(
            summary.get("process_code_distribution", {})
        )
        report_text = self.result.toPlainText()
        marker = _("[양폐 적용]")
        report_until_dummy_applied = report_text
        marker_index = report_text.find(marker)
        if marker_index >= 0:
            next_section_index = report_text.find("\n\n[", marker_index + len(marker))
            report_until_dummy_applied = report_text if next_section_index < 0 else report_text[:next_section_index]
        payload = {
            "schema": "dmt-studio-yield-summary-v1",
            **summary,
            "existing_process_bbt_defect_summary": summary.get("existing_bbt_defect_summary", {}),
            "existing_bbt_defect_code_totals": summary.get("existing_bbt_code_distribution", {}),
            "existing_bbt_process_defect_totals": summary.get("existing_bbt_process_code_distribution", {}),
            "process_defect_summary": process_defect_summary,
            "split_170_added_distribution": split_170_summary,
            "normal_defect_summary": split_170_summary.get("normal", {}),
            "code_170_summary": split_170_summary.get("code_170", {}),
            "process_totals": process_defect_summary.get("by_process", {}),
            "defect_code_totals": process_defect_summary.get("by_defect_code", {}),
            "process_defect_totals": process_defect_summary.get("by_process_and_defect_code", {}),
            "report_text_until_dummy_applied": report_until_dummy_applied,
            "summary": summary,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        QMessageBox.information(self, _("저장 완료"), path)

    def choose_report_template(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("성적서 템플릿 선택"), "", "Excel Files (*.xlsx)")
        if not path:
            return
        from core.report_templates import remember_template
        try:
            remember_template(path)
            QMessageBox.information(self, _("저장 완료"), _("성적서 템플릿을 저장했습니다. 다음부터 자동으로 사용합니다."))
        except Exception as exc:
            QMessageBox.critical(self, _("템플릿 오류"), str(exc))

    def export_excel(self) -> None:
        if not self.report_ready():
            return
        audit=self.summary.get('its_validation',{})
        if audit.get('enabled') and (not audit.get('auto_pass') or audit.get('untracked_strips')):
            QMessageBox.warning(self,_('ITS 검증 미완료'),_('최신 이력·누락 좌표·SKI 갱신 상태를 확인하고 수율을 다시 계산하세요.'))
            return
        if not self.summary:
            QMessageBox.warning(self, _("저장 없음"), _("먼저 수율 계산을 실행하세요."))
            return

        from core.report_templates import resolve_template
        try:
            template_path = resolve_template()
        except Exception as exc:
            QMessageBox.critical(self, _("템플릿 오류"), str(exc))
            return

        lot = str(self.summary.get("lot_id", "yield_summary")).strip() or "yield_summary"
        output_path, _ext = QFileDialog.getSaveFileName(
            self,
            _("1차 성적서 저장"),
            f"{lot}_1차성적서.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not output_path:
            return

        from core.yield_calc import build_yield_report_aggregation
        from core.export_excel import export_report_to_2xlsx

        report = build_yield_report_aggregation(self.summary, self.model_in.text().strip())
        result = export_report_to_2xlsx(report, template_path, output_path)

        if result.get("success"):
            ng = result["ng_qty"]
            total_defects = result["sum_defects"]
            match_icon = "✓" if total_defects == ng else "⚠"
            template_note = "" if result.get("used_template") else f"[{_('템플릿 없이 생성')}]\n"
            msg = (
                f"{template_note}"
                f"{match_icon} {_('불량 합계')}: {total_defects:,} / {_('NG 수량')}: {ng:,}\n"
            )
            if total_defects != ng:
                msg += _("⚠ 불량 합계와 NG 수량이 다릅니다. 공정 선택 또는 Dummy/170 처리 확인 요망.\n")
            high = result.get("high_rate_codes", [])
            if high:
                msg += f"\n{_('3% 이상 불량')}: " + ", ".join(high)
            QMessageBox.information(self, _("1차 성적서 생성 완료"), msg)
        else:
            QMessageBox.critical(self, _("에러 발생"), result.get("error", "Unknown error"))

    def refresh_result(self) -> None:
        summary = self.summary
        if summary.get("mode") == "process":
            self.refresh_process_result(summary)
            if summary.get('its_validation',{}).get('enabled'):
                from core.its_validation import format_audits
                self.result.append('\n'+format_audits(summary['its_validation']))
            return
        self.result.setPlainText(
            f"LOT: {summary['lot_id']}\n"
            f"총 Strip: {summary['total_strips']}개\n"
            f"스트립당 Unit: {summary['units_per_strip']}개\n"
            f"총 Unit: {summary['total_units']}개\n"
            f"양품 Unit: {summary['good_units']}개\n"
            f"불량 Unit: {summary['defective_units']}개\n"
            f"Unit 수율: {summary['unit_yield_percent']}%\n"
            f"양품 Strip: {summary['good_strips']}개\n"
            f"양폐 Strip(합산 X-out = Unit): {summary['bad_strips']}개\n"
            f"양폐 Strip Unit: {summary.get('scrap_units', summary['bad_strips'] * summary['units_per_strip'])}개\n"
            f"IVS Dummy 9999 양폐 Strip: {summary.get('ivs_dummy_9999_scrap_strip_count', 0)}개\n"
            f"IVS Dummy 9999 양폐 Unit: {summary.get('ivs_dummy_9999_scrap_units', 0)}개\n"
            f"양품 Strip 내 불량 Unit(SKI 기준): {summary.get('good_strip_defective_units', 0)}개\n"
            f"혼입 StripID: {len(summary['mixed_strips'])}개\n"
            f"중복 StripID: {len(summary['duplicate_strips'])}개\n"
            f"공정 병합 StripID: {summary.get('merged_strip_count', 0)}개\n"
            f"혼입/중복 없는 TXT 고유 Strip: {summary['clean_txt_strip_count']}개\n"
            f"TXT에만 있는 StripID: {len(summary['txt_only_strips'])}개\n"
            f"TXT에 없는 StripID: {len(summary['missing_strips'])}개\n"
            f"Skipdata Strip: {summary['skipdata_strip_count']}개\n"
            f"BBT 불량 Unit: {summary['total_bbt_units']}개\n"
            f"BBT 불량 Strip: {summary.get('bbt_strip_count', 0)}개\n"
            f"추가 불량 Unit: {summary['total_added_units']}개\n"
            f"게더링 원본 불량 Unit: {summary['total_gather_units']}개\n"
            f"BBT와 게더링 겹침 Unit(정보): {summary['total_bbt_duplicate_units']}개\n"
            f"StripID JSON에 없는 Skipdata Strip: {len(summary['skipdata_only_strips'])}개\n\n"
            f"[혼입]\n{', '.join(summary['mixed_strips']) if summary['mixed_strips'] else '-'}\n\n"
            f"[중복]\n{', '.join(summary['duplicate_strips']) if summary['duplicate_strips'] else '-'}\n\n"
            f"[공정 병합]\n{', '.join(summary.get('merged_strips', [])) if summary.get('merged_strips') else '-'}"
        )
        self.append_ivs_dummy_9999_validation_summary(summary)
        self.append_ski_validation_summary(summary)
        self.append_bbt_full_unit_summary(summary)
        self.append_ski_gather_xout_mismatch_summary(summary)
        self.append_strip_json_validation_summary(summary)
        self.append_bbt_validation_summary(summary)

        distribution = summary["xout_distribution"]
        self.xout_table.setRowCount(len(distribution))
        self.xout_table.setHorizontalHeaderLabels([_("X-out 수"), _("Strip 수")])
        for row, (xout, count) in enumerate(distribution.items()):
            self.xout_table.setItem(row, 0, QTableWidgetItem(str(xout)))
            self.xout_table.setItem(row, 1, QTableWidgetItem(str(count)))
        self.xout_table.resizeColumnsToContents()

        rows = self.filtered_strip_rows(summary["per_strip_rows"], "xout_count")
        self.strip_table.setColumnCount(10)
        self.strip_table.setHorizontalHeaderLabels(
            ["StripID", _("합산 X-out"), "SKI", _("SKI 판정"), _("BBT 불량"), _("추가 불량"), _("게더링 원본"), _("BBT 중복"), _("판정"), _("TXT 블록 수")]
        )
        self.strip_table.setRowCount(len(rows) + 1)
        totals = {column: 0 for column in (1, 2, 4, 5, 6, 7, 9)}
        for row, item in enumerate(rows):
            self.strip_table.setItem(row, 0, QTableWidgetItem(str(item["strip_id"])))
            self.strip_table.setItem(row, 1, QTableWidgetItem(str(item["xout_count"])))
            self.strip_table.setItem(row, 2, QTableWidgetItem(str(item["ski_xout_count"])))
            self.strip_table.setItem(row, 3, QTableWidgetItem(str(item["ski_status"])))
            self.strip_table.setItem(row, 4, QTableWidgetItem(str(item["bbt_xout_count"])))
            self.strip_table.setItem(row, 5, QTableWidgetItem(str(item["added_xout_count"])))
            self.strip_table.setItem(row, 6, QTableWidgetItem(str(item["raw_xout_count"])))
            self.strip_table.setItem(row, 7, QTableWidgetItem(str(item["bbt_duplicate_count"])))
            self.strip_table.setItem(row, 8, QTableWidgetItem(str(item["status"])))
            self.strip_table.setItem(row, 9, QTableWidgetItem(str(item["block_count"])))
            self.mark_table_row_if_mismatch(row, int(item.get("xout_count", 0)), int(item.get("ski_xout_count", 0)), 10)
            totals[1] += int(item.get("xout_count", 0))
            totals[2] += int(item.get("ski_xout_count", 0))
            totals[4] += int(item.get("bbt_xout_count", 0))
            totals[5] += int(item.get("added_xout_count", 0))
            totals[6] += int(item.get("raw_xout_count", 0))
            totals[7] += int(item.get("bbt_duplicate_count", 0))
            totals[9] += int(item.get("block_count", 0))
        self.set_sum_row(len(rows), totals, 10)
        self.strip_table.resizeColumnsToContents()

    def filtered_strip_rows(self, rows: list[dict], xout_key: str) -> list[dict]:
        if not hasattr(self, "radio_table_mismatch") or not self.radio_table_mismatch.isChecked():
            return rows
        return [
            item for item in rows
            if int(item.get(xout_key, 0)) != int(item.get("ski_xout_count", 0))
        ]

    def mark_table_row_if_mismatch(self, row: int, xout: int, ski: int, column_count: int) -> None:
        if xout == ski:
            return
        color = QColor(210, 170, 0)
        for column in range(column_count):
            item = self.strip_table.item(row, column)
            if item is not None:
                item.setForeground(color)

    def apply_table_filter(self) -> None:
        if self.summary:
            self.refresh_result()

    def set_sum_row(self, row: int, numeric_totals: dict[int, int], column_count: int, label: str = "SUM") -> None:
        self.strip_table.setItem(row, 0, QTableWidgetItem(label))
        for column in range(1, column_count):
            value = numeric_totals.get(column, "")
            self.strip_table.setItem(row, column, QTableWidgetItem(str(value) if value != "" else ""))

    def refresh_yield_report_result(self, summary: dict) -> None:
        from core.yield_calc import build_yield_report_aggregation
        report = build_yield_report_aggregation(summary, self.model_in.text().strip())
        lines = [
            _("[성적서 집계]"),
            f"{_('모델명')}: {report.get('model_name', '')}",
            f"{_('LOT 번호')}: {report.get('lot_id', '')}",
            f"{_('총 유닛 수량')}: {report.get('total_units', 0)}",
            f"{_('OK 유닛 수량')}: {report.get('ok_units', 0)}",
            f"{_('NG 유닛 수량')}: {report.get('ng_units', 0)}",
            f"{_('OK 유닛 비율')}: {report.get('ok_unit_aggregation', {}).get('ok_unit_percent', 0)}%",
            f"{_('기존 BBT 불량 수량(OT14)')}: {report.get('existing_bbt_ot14_units', 0)}",
            f"{_('실제 검사 SM03 수량')}: {report.get('real_sm03_units', 0)}",
            f"{_('Dummy 9999 패스 SM03 수량(BBT 제외)')}: {report.get('dummy_9999_sm03_units', 0)}",
            f"{_('x-out 초과 170 수량')}: {report.get('xout_over_170_units', 0)}",
            f"{_('SKI 기준 불량 유닛 수')}: {report.get('ski_defect_units', 0)}",
            f"{_('최종 불량명 합계 - SKI 차이')}: {report.get('final_vs_ski_delta', 0)}",
            "",
            _("[추가 공정 불량 merge 통계]"),
        ]
        process_rows = report.get("process_unique_defects_by_code", {})
        if process_rows:
            for code, item in process_rows.items():
                breakdown = ", ".join(f"{process} {count}" for process, count in item.get("process_breakdown", {}).items())
                lines.append(
                    f"{code}: {item.get('count', 0)}{_('개')} / {item.get('percent_of_total_units', 0)}%"
                    + (f" ({breakdown})" if breakdown else "")
                )
        else:
            lines.append("-")
        lines.extend(["", _("[BBT 포함 최종 불량명 집계]")])
        final_rows = report.get("final_defects_by_code_including_bbt", {})
        if final_rows:
            for code, item in final_rows.items():
                breakdown = ", ".join(f"{process} {count}" for process, count in item.get("process_breakdown", {}).items())
                lines.append(
                    f"{code}: {item.get('count', 0)}{_('개')} / {item.get('percent_of_total_units', 0)}%"
                    + (f" ({breakdown})" if breakdown else "")
                )
        else:
            lines.append("-")
        self.report_result.setPlainText("\n".join(lines))

    def refresh_process_result(self, summary: dict) -> None:
        self.refresh_yield_report_result(summary)
        self.result.setPlainText(
            f"LOT: {summary['lot_id']}\n"
            f"총 Strip: {summary['total_strips']}개\n"
            f"스트립당 Unit: {summary['units_per_strip']}개\n"
            f"총 Unit: {summary['total_units']}개\n"
            f"양품 Unit: {summary['good_units']}개\n"
            f"불량 Unit(BBT+VRS/FVI/IVS 추가): {summary['defective_units']}개\n"
            f"Unit 수율: {summary['unit_yield_percent']}%\n"
            f"양품 Strip: {summary['good_strips']}개\n"
            f"양폐 Strip(합산 X-out = Unit): {summary['bad_strips']}개\n"
            f"양폐 Strip Unit: {summary.get('scrap_units', summary['bad_strips'] * summary['units_per_strip'])}개\n"
            f"IVS Dummy 9999 양폐 Strip: {summary.get('ivs_dummy_9999_scrap_strip_count', 0)}개\n"
            f"IVS Dummy 9999 양폐 Unit: {summary.get('ivs_dummy_9999_scrap_units', 0)}개\n"
            f"양품 Strip 내 불량 Unit(SKI 기준): {summary.get('good_strip_defective_units', 0)}개\n"
            f"BBT 기존 불량 Unit: {summary['bbt_units']}개\n"
            f"BBT 기존 불량 Strip: {summary.get('bbt_strip_count', 0)}개\n"
            f"양품 Strip 내 BBT 기존 불량 Unit: {summary.get('bbt_good_strip_units', 0)}개\n"
            f"양품 Strip 내 BBT 기존 불량 Strip: {summary.get('bbt_good_strip_count', 0)}개\n"
            f"양폐 Strip 내 BBT 기존 불량 Unit(참고): {summary.get('bbt_scrap_strip_units', 0)}개\n"
            f"양폐 Strip 내 BBT 기존 불량 Strip(참고): {summary.get('bbt_scrap_strip_count', 0)}개\n"
            f"VRS/FVI/IVS 추가 불량 Unit(게더링 기준): {summary['added_units']}개\n"
            f"VRS Strip: {summary['vrs_strip_count']}개 / 최신 갱신 {summary['vrs_replaced_count']}개\n"
            f"FVI Strip: {summary['fvi_strip_count']}개 / 최신 갱신 {summary['fvi_replaced_count']}개\n"
            f"IVS Strip: {summary['ivs_strip_count']}개 / 최신 갱신 {summary['ivs_replaced_count']}개\n"
            f"VRS만 있는 Strip: {len(summary['vrs_only'])}개\n"
            f"FVI만 있는 Strip: {len(summary['fvi_only'])}개\n"
            f"IVS만 있는 Strip: {len(summary['ivs_only'])}개\n"
            f"JSON 혼입 LOT StripID: {len(summary.get('strip_json_validation', {}).get('json_mixed_lot_strips', []))}개\n"
            f"게더링 혼입 LOT StripID: {len(summary.get('strip_json_validation', {}).get('gather_mixed_lot_strips', []))}개\n\n"
        )
        self.append_split_170_added_distribution_summary(summary)
        self.append_existing_bbt_code_distribution_summary(summary)
        self.append_ivs_dummy_9999_validation_summary(summary)
        self.append_ski_validation_summary(summary)
        self.append_bbt_full_unit_summary(summary)
        self.append_ski_gather_xout_mismatch_summary(summary)
        self.append_strip_json_validation_summary(summary)
        self.append_bbt_validation_summary(summary)

        distribution = summary["xout_distribution"]
        self.xout_table.setRowCount(len(distribution))
        self.xout_table.setHorizontalHeaderLabels([_("합산 X-out"), _("Strip 수")])
        for row, (xout, count) in enumerate(distribution.items()):
            self.xout_table.setItem(row, 0, QTableWidgetItem(str(xout)))
            self.xout_table.setItem(row, 1, QTableWidgetItem(str(count)))
        self.xout_table.resizeColumnsToContents()

        self.strip_table.setColumnCount(11)
        self.strip_table.setHorizontalHeaderLabels(["StripID", _("합산 X-out"), "SKI", _("SKI 판정"), "BBT", _("추가"), "VRS", "FVI", "IVS", _("양품 Unit"), _("판정")])
        rows = self.filtered_strip_rows(summary["per_strip_rows"], "final_xout")
        self.strip_table.setRowCount(len(rows) + 1)
        totals = {column: 0 for column in (1, 2, 4, 5, 6, 7, 8, 9)}
        for row, item in enumerate(rows):
            self.strip_table.setItem(row, 0, QTableWidgetItem(str(item["strip_id"])))
            self.strip_table.setItem(row, 1, QTableWidgetItem(str(item["final_xout"])))
            self.strip_table.setItem(row, 2, QTableWidgetItem(str(item["ski_xout_count"])))
            self.strip_table.setItem(row, 3, QTableWidgetItem(str(item["ski_status"])))
            self.strip_table.setItem(row, 4, QTableWidgetItem(str(item["bbt_count"])))
            self.strip_table.setItem(row, 5, QTableWidgetItem(str(item["added_count"])))
            self.strip_table.setItem(row, 6, QTableWidgetItem(str(item["vrs_count"])))
            self.strip_table.setItem(row, 7, QTableWidgetItem(str(item["fvi_count"])))
            self.strip_table.setItem(row, 8, QTableWidgetItem(str(item["ivs_count"])))
            self.strip_table.setItem(row, 9, QTableWidgetItem(str(item["good_units"])))
            self.strip_table.setItem(row, 10, QTableWidgetItem(str(item["status"])))
            self.mark_table_row_if_mismatch(row, int(item.get("final_xout", 0)), int(item.get("ski_xout_count", 0)), 11)
            totals[1] += int(item.get("final_xout", 0))
            totals[2] += int(item.get("ski_xout_count", 0))
            totals[4] += int(item.get("bbt_count", 0))
            totals[5] += int(item.get("added_count", 0))
            totals[6] += int(item.get("vrs_count", 0))
            totals[7] += int(item.get("fvi_count", 0))
            totals[8] += int(item.get("ivs_count", 0))
            totals[9] += int(item.get("good_units", 0))
        self.set_sum_row(len(rows), totals, 11)
        self.strip_table.resizeColumnsToContents()

    def append_split_170_added_distribution_summary(self, summary: dict) -> None:
        split_summary = summary.get("split_170_added_distribution") or split_170_added_distribution(
            summary.get("process_code_distribution", {})
        )

        def block(title: str, payload: dict) -> str:
            by_process = payload.get("by_process", {})
            by_code = payload.get("by_defect_code", {})
            by_process_code = payload.get("by_process_and_defect_code", {})
            return (
                f"\n[{title} - 공정별]\n"
                + ("\n".join(f"{key}: {value}" for key, value in by_process.items()) or "-")
                + f"\n\n[{title} - 불량명별]\n"
                + ("\n".join(f"{key}: {value}" for key, value in by_code.items()) or "-")
                + f"\n\n[{title} - 공정/불량명별]\n"
                + ("\n".join(f"{key}: {value}" for key, value in by_process_code.items()) or "-")
            )

        self.result.append(
            block(_("기존 불량코드 추가 불량(170 제외)"), split_summary.get("normal", {}))
            + "\n"
            + block(_("170 양폐/허용수초과 불량"), split_summary.get("code_170", {}))
        )

    def append_existing_bbt_code_distribution_summary(self, summary: dict) -> None:
        distribution = summary.get("existing_bbt_code_distribution") or {}
        process_distribution = summary.get("existing_bbt_process_code_distribution") or {}
        if not distribution:
            return
        self.result.append(
            _("\n[BBT 기존 불량명별]\n")
            + ("\n".join(f"{code}: {count}" for code, count in distribution.items()) or "-")
            + _("\n\n[BBT 기존 공정/불량명별]\n")
            + ("\n".join(f"{code}: {count}" for code, count in process_distribution.items()) or "-")
        )

    def append_ski_validation_summary(self, summary: dict) -> None:
        validation = summary.get("ski_validation") or {}
        if not validation:
            return
        distribution = validation.get("ski_distribution", {})
        distribution_text = "\n".join(f"{xout}: {count}" for xout, count in distribution.items()) or "-"
        full_strips = validation.get("ski_full_xout_strips", [])
        scrap_strips = sorted(set(summary.get("scrap_strip_ids", [])))
        ski_full_set = set(full_strips)
        scrap_set = set(scrap_strips)
        ski_full_not_scrap = sorted(ski_full_set - scrap_set)
        scrap_not_ski_full = sorted(scrap_set - ski_full_set)
        self.result.append(
            _("\n[SKI 검증]\n") +
            f"SKI 좌표 존재 Strip: {validation.get('ski_strip_count', 0)}개\n" +
            f"SKI 총 불량 Unit: {validation.get('ski_total_units', 0)}개\n" +
            f"SKI 기준 허용수초과 Strip(불량수 = Unit): {validation.get('ski_full_xout_strip_count', 0)}개\n" +
            f"수율 계산 양폐 Strip: {len(scrap_strips)}개\n" +
            f"SKI full이나 수율 양폐가 아닌 Strip: {len(ski_full_not_scrap)}개\n" +
            f"수율 양폐이나 SKI full이 아닌 Strip: {len(scrap_not_ski_full)}개\n" +
            f"게더링 기준에는 없고 SKI에만 있는 Strip: {len(validation.get('ski_only_strips', []))}개\n" +
            f"[SKI X-out 분포]\n{distribution_text}\n\n" +
            f"[SKI 기준 허용수초과 Strip]\n{', '.join(full_strips) if full_strips else '-'}\n\n" +
            f"[SKI full이나 수율 양폐가 아닌 Strip]\n{', '.join(ski_full_not_scrap) if ski_full_not_scrap else '-'}\n\n" +
            f"[수율 양폐이나 SKI full이 아닌 Strip]\n{', '.join(scrap_not_ski_full) if scrap_not_ski_full else '-'}\n\n" +
            f"[SKI only]\n{', '.join(validation.get('ski_only_strips', [])) if validation.get('ski_only_strips') else '-'}"
        )

    def append_ivs_dummy_9999_validation_summary(self, summary: dict) -> None:
        validation = summary.get("ivs_dummy_9999_validation") or {}
        if not validation or not validation.get("candidate_strips"):
            return
        recovered = validation.get("recovered_strips", [])
        applied = validation.get("applied_strips", [])
        unverified = validation.get("unverified_strips", [])
        recovery_details = [
            item for item in validation.get("details", [])
            if item.get("strip_id") in set(recovered)
        ]
        with_gather = [item["strip_id"] for item in recovery_details if item.get("has_gathering_data")]
        without_gather = [item["strip_id"] for item in recovery_details if not item.get("has_gathering_data")]
        detail_lines = []
        for item in recovery_details[:100]:
            detail_lines.append(
                f"{item['strip_id']} | SKI {item.get('ski_xout_count')} | "
                f"게더링 데이터 {_('있음') if item.get('has_gathering_data') else _('없음')}"
            )
        self.result.append(
            _("\n[IVS Dummy 9999 SKI 검증]\n") +
            f"JSON Dummy 9999 후보 Strip: {len(validation.get('candidate_strips', []))}개\n" +
            f"SKI 기준 양폐 적용 Strip: {len(applied)}개\n" +
            f"SKI 기준 복구/취소 Strip: {len(recovered)}개\n" +
            f"SKI 미선택으로 미검증 적용 Strip: {len(unverified)}개\n" +
            f"복구/취소 중 게더링 데이터 있음: {len(with_gather)}개\n"
            f"복구/취소 중 게더링 데이터 없음: {len(without_gather)}개\n\n"
            f"[양폐 적용]\n{', '.join(applied) if applied else '-'}\n\n"
            f"[복구/취소]\n{', '.join(recovered) if recovered else '-'}\n\n"
            f"[복구/취소 상세 상위 100개]\n{chr(10).join(detail_lines) if detail_lines else '-'}"
        )

    def append_bbt_full_unit_summary(self, summary: dict) -> None:
        validation = summary.get("bbt_full_unit_summary") or {}
        if not validation:
            return
        strip_ids = validation.get("strip_ids", [])
        self.result.append(
            _("\n[BBT/RPT 입고 전 양폐]\n") +
            f"BBT 불량수 = Unit Strip: {validation.get('strip_count', 0)}개\n" +
            f"BBT 입고 전 양폐 Unit: {validation.get('unit_count', 0)}개\n" +
            f"[StripID]\n{', '.join(strip_ids) if strip_ids else '-'}"
        )

    def append_ski_gather_xout_mismatch_summary(self, summary: dict) -> None:
        rows = summary.get("ski_gather_xout_mismatch_rows") or []
        if not rows:
            return
        lines = [
            "",
            _("[SKI/게더링 X-out 불일치 상세]"),
            f"불일치 Strip: {len(rows)}개",
        ]
        for item in rows[:100]:
            lines.append(
                f"{item['strip_id']} | 게더링 {item['gather_xout']} | SKI {item['ski_xout']} | "
                f"BBT {item['bbt_count']} | 추가 {item['added_count']}"
            )
            for process in ("IVS", "VRS", "FVI"):
                payload = item.get("processes", {}).get(process, {})
                code_parts = []
                for code, coords in payload.get("codes", {}).items():
                    preview = ", ".join(coords[:20])
                    suffix = " ..." if len(coords) > 20 else ""
                    code_parts.append(f"{code}({len(coords)}): {preview}{suffix}")
                sources = [Path(source).name for source in payload.get("sources", [])]
                source_text = ", ".join(sources[:5]) + (" ..." if len(sources) > 5 else "")
                lines.append(
                    f"  {process}: 좌표 {payload.get('coord_count', 0)}개 / 라인 {payload.get('line_count', 0)}개"
                    + (f" / 파일 {source_text}" if source_text else "")
                )
                if code_parts:
                    lines.append("    " + " | ".join(code_parts))
        if len(rows) > 100:
            lines.append(f"... {len(rows) - 100}개 추가 불일치는 JSON 내보내기에서 확인")
        self.result.append("\n".join(lines))

    def append_strip_json_validation_summary(self, summary: dict) -> None:
        validation = summary.get("strip_json_validation") or {}
        if not validation:
            return
        self.result.append(
            _("\n[StripID JSON 검증]\n") +
            f"StripID JSON Strip: {validation.get('json_strip_count', 0)}개\n" +
            f"게더링 데이터 Strip: {validation.get('gather_strip_count', 0)}개\n" +
            f"JSON에는 있으나 게더링 데이터에 없는 StripID: {len(validation.get('json_only_strips', []))}개\n" +
            f"게더링 데이터에는 있으나 JSON에 없는 StripID: {len(validation.get('gather_only_strips', []))}개\n" +
            f"JSON 혼입 LOT StripID: {len(validation.get('json_mixed_lot_strips', []))}개\n" +
            f"게더링 혼입 LOT StripID: {len(validation.get('gather_mixed_lot_strips', []))}개"
        )

    def append_bbt_validation_summary(self, summary: dict) -> None:
        validation = summary.get("bbt_validation") or {}
        if not validation:
            return
        bbt_only = validation.get("bbt_only_strips", [])
        mismatch_rows = validation.get("mismatch_rows", [])
        lines = [
            "",
            _("[BBT/RPT 검증]"),
            f"RPT 전체 BBT Strip: {validation.get('bbt_total_strips', 0)}개",
            f"RPT 전체 BBT Unit: {validation.get('bbt_total_units', 0)}개",
            f"수율 계산 대상 BBT Strip: {validation.get('used_bbt_strip_count', 0)}개",
            f"수율 계산 대상 BBT Unit: {validation.get('used_bbt_units', 0)}개",
            f"수율 계산 대상 밖 BBT Strip: {validation.get('bbt_only_strip_count', 0)}개",
            f"BBT와 게더링 겹침 좌표 Unit(정보): {validation.get('duplicate_with_gather_units', 0)}개",
            f"SKI에 없는 BBT+게더링 좌표 Unit: {validation.get('missing_in_ski_units', 0)}개",
            f"BBT+게더링에 없는 SKI 좌표 Unit: {validation.get('extra_in_ski_units', 0)}개",
        ]
        if bbt_only:
            lines.extend(["", _("[수율 계산 대상 밖 BBT Strip 상위 100개]"), ", ".join(bbt_only[:100])])
        if mismatch_rows:
            lines.extend(["", _("[BBT+게더링 vs SKI 불일치 상세 상위 50개]")])
            for item in mismatch_rows[:50]:
                lines.append(
                    f"{item['strip_id']} | BBT {item['bbt_count']} | 게더링 {item.get('gather_count', 0)} | "
                    f"예상 {item.get('expected_count', 0)} | SKI {item.get('ski_count', 0)} | "
                    f"SKI 누락 {item['ski_missing_count']} | SKI 초과 {item.get('ski_extra_count', 0)}"
                )
                if item["ski_missing_coords"]:
                    lines.append(f"  SKI 누락: {', '.join(item['ski_missing_coords'][:20])}")
                if item.get("ski_extra_coords"):
                    lines.append(f"  SKI 초과: {', '.join(item['ski_extra_coords'][:20])}")
        self.result.append("\n".join(lines))
