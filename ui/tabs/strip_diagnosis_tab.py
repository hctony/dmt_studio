"""Strip diagnosis and coordinate validation tab."""
from __future__ import annotations
from core.i18n import _

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_MODEL
from core.gather import format_xy_coords, parse_xy_from_gather_line
from core.skipdata import load_ski_file, load_skipdata_folder
from core.utils import infer_lot_id_from_values, split_path_list
from core.yield_calc import analyze_ski_validation, missing_strip_ids_from_files, gather_lines_for_strip, gather_line_sources_for_strip
from ui.settings_helper import restore_line_edit, save_line_edit, save_setting_text, setting_text
from workers.ivs_workers import StripDefectSearchThread
from workers.strip_workers import ModelLotListThread


class StripDiagnosisTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.txt_paths: list[Path] = []
        self.missing_strip_ids: list[str] = []
        self.ski_validation_summary: dict | None = None
        self.model_lot_thread: Optional[ModelLotListThread] = None
        self.search_thread: Optional[StripDefectSearchThread] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        files_box = QGroupBox(_("누락 StripID / 좌표 검색 입력"))
        form = QFormLayout(files_box)
        self.strip_json_in = QLineEdit()
        self.txt_in = QLineEdit()
        self.skipdata_in = QLineEdit()
        self.ski_in = QLineEdit()
        restore_line_edit(self.strip_json_in, "diagnosis/strip_json")
        restore_line_edit(self.txt_in, "diagnosis/txt_paths")
        restore_line_edit(self.skipdata_in, "diagnosis/skipdata")
        restore_line_edit(self.ski_in, "diagnosis/ski")

        json_row = QHBoxLayout()
        json_row.addWidget(self.strip_json_in)
        btn_json = QPushButton(_("StripID JSON 선택"))
        btn_json.clicked.connect(self.choose_json)
        json_row.addWidget(btn_json)

        txt_row = QHBoxLayout()
        txt_row.addWidget(self.txt_in)
        btn_txt = QPushButton(_("게더링 TXT 선택"))
        btn_txt.clicked.connect(self.choose_txt)
        txt_row.addWidget(btn_txt)

        skipdata_row = QHBoxLayout()
        skipdata_row.addWidget(self.skipdata_in)
        btn_skipdata = QPushButton(_("Skipdata 폴더 선택"))
        btn_skipdata.clicked.connect(self.choose_skipdata)
        skipdata_row.addWidget(btn_skipdata)

        ski_row = QHBoxLayout()
        ski_row.addWidget(self.ski_in)
        btn_ski = QPushButton(_("SKI 파일 선택"))
        btn_ski.clicked.connect(self.choose_ski)
        ski_row.addWidget(btn_ski)

        form.addRow("StripID JSON", json_row)
        form.addRow(_("게더링 TXT"), txt_row)
        form.addRow(_("BBT/Skipdata 폴더"), skipdata_row)
        form.addRow(_("최종 SKI 파일"), ski_row)
        layout.addWidget(files_box)
        self.autofill_ski_file()

        nas_box = QGroupBox(_("NAS 모델 / LOT"))
        nas_form = QFormLayout(nas_box)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItem(DEFAULT_MODEL)
        last_model = setting_text("diagnosis/model", DEFAULT_MODEL)
        if last_model and self.model_combo.findText(last_model) < 0:
            self.model_combo.insertItem(0, last_model)
            self.model_combo.setCurrentIndex(0)
        self.lot_combo = QComboBox()
        self.lot_combo.setEditable(True)
        last_lot = setting_text("diagnosis/lot")
        if last_lot:
            self.lot_combo.addItem(last_lot)
        self.strip_search_in = QLineEdit()
        restore_line_edit(self.strip_search_in, "diagnosis/strip_id")
        self.strip_search_in.setPlaceholderText(_("예: RX2262084400-0101"))

        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo)
        btn_models = QPushButton(_("모델 목록"))
        btn_models.clicked.connect(self.load_models)
        model_row.addWidget(btn_models)

        lot_row = QHBoxLayout()
        lot_row.addWidget(self.lot_combo)
        btn_lot_auto = QPushButton(_("LOT 자동"))
        btn_lot_auto.clicked.connect(self.auto_lot)
        lot_row.addWidget(btn_lot_auto)
        btn_lots = QPushButton(_("LOT 목록"))
        btn_lots.clicked.connect(self.load_lots)
        lot_row.addWidget(btn_lots)

        nas_form.addRow(_("모델"), model_row)
        nas_form.addRow("LOT", lot_row)
        nas_form.addRow("StripID", self.strip_search_in)
        layout.addWidget(nas_box)

        action_row = QHBoxLayout()
        self.btn_missing = QPushButton(_("누락 StripID 갱신"))
        self.btn_missing.clicked.connect(self.refresh_missing)
        self.btn_search = QPushButton(_("StripID 좌표 검색"))
        self.btn_search.clicked.connect(self.search_strip)
        self.btn_ski_validate = QPushButton(_("SKI 최종좌표 검증"))
        self.btn_ski_validate.clicked.connect(self.validate_ski)
        self.btn_strip_detail = QPushButton(_("선택 Strip 상세 로그"))
        self.btn_strip_detail.clicked.connect(self.show_selected_strip_detail)
        self.btn_save_ski_lists = QPushButton(_("검증 리스트 JSON 저장"))
        self.btn_save_ski_lists.clicked.connect(self.save_ski_validation_lists)
        action_row.addWidget(self.btn_missing)
        action_row.addWidget(self.btn_search)
        action_row.addWidget(self.btn_ski_validate)
        action_row.addWidget(self.btn_strip_detail)
        action_row.addWidget(self.btn_save_ski_lists)
        action_row.addStretch()
        layout.addLayout(action_row)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.missing_label = QLabel(_("누락 StripID: -"))
        self.missing_list = QListWidget()
        self.missing_list.itemSelectionChanged.connect(self.on_missing_selected)
        left_layout.addWidget(self.missing_label)
        left_layout.addWidget(self.missing_list)
        splitter.addWidget(left)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        splitter.addWidget(self.result)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

    def choose_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("StripID JSON 선택"), "", "JSON Files (*.json)")
        if path:
            self.strip_json_in.setText(path)
            save_line_edit(self.strip_json_in, "diagnosis/strip_json")
            self.auto_lot()

    def choose_txt(self) -> None:
        paths, _ext = QFileDialog.getOpenFileNames(self, _("게더링 TXT 선택"), "", "Text Files (*.txt);;All Files (*.*)")
        if paths:
            self.txt_paths = [Path(path) for path in paths]
            self.txt_in.setText("; ".join(paths))
            save_line_edit(self.txt_in, "diagnosis/txt_paths")

    def choose_skipdata(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("BBT/Skipdata 폴더 선택"))
        if path:
            self.skipdata_in.setText(path)
            save_line_edit(self.skipdata_in, "diagnosis/skipdata")

    def choose_ski(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("최종 SKI 파일 선택"), "", "SKI Files (*.ski);;All Files (*.*)")
        if path:
            self.ski_in.setText(path)
            save_line_edit(self.ski_in, "diagnosis/ski")

    def autofill_ski_file(self) -> None:
        if self.ski_in.text().strip():
            return
        ski_files = sorted(Path.cwd().glob("*.ski"), key=lambda path: path.stat().st_mtime, reverse=True)
        if ski_files:
            self.ski_in.setText(str(ski_files[0]))

    def current_txt_paths(self) -> list[Path]:
        return split_path_list(self.txt_in.text())

    def refresh_missing(self) -> None:
        json_path = Path(self.strip_json_in.text().strip())
        txt_paths = self.current_txt_paths()
        if not json_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("StripID JSON 파일을 선택하세요."))
            return
        if not txt_paths or any(not path.is_file() for path in txt_paths):
            QMessageBox.warning(self, _("입력 오류"), _("게더링 TXT 파일을 하나 이상 선택하세요."))
            return
        try:
            save_line_edit(self.strip_json_in, "diagnosis/strip_json")
            save_line_edit(self.txt_in, "diagnosis/txt_paths")
            lot_id, missing = missing_strip_ids_from_files(json_path, txt_paths)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("누락 계산 실패"), str(exc))
            return
        self.missing_strip_ids = missing
        self.missing_label.setText(f"누락 StripID: {len(missing)}개")
        self.missing_list.clear()
        for strip_id in missing:
            self.missing_list.addItem(strip_id)
        self.result.setPlainText(
            f"LOT: {lot_id}\n"
            f"누락 StripID: {len(missing)}개\n\n"
            + ("\n".join(missing) if missing else _("누락 없음"))
        )

    def on_missing_selected(self) -> None:
        item = self.missing_list.currentItem()
        if item:
            self.strip_search_in.setText(item.text())

    def auto_lot(self) -> None:
        lot_id = infer_lot_id_from_values(
            self.strip_json_in.text(),
            self.txt_in.text(),
            self.ski_in.text(),
            self.strip_search_in.text(),
            self.lot_combo.currentText(),
        )
        if lot_id:
            if self.lot_combo.findText(lot_id) < 0:
                self.lot_combo.insertItem(0, lot_id)
            self.lot_combo.setCurrentText(lot_id)
            save_setting_text("diagnosis/lot", lot_id)
        else:
            QMessageBox.information(self, _("LOT 자동 추출"), _("입력값에서 LOT를 찾지 못했습니다."))

    def load_models(self) -> None:
        self.btn_search.setEnabled(False)
        self.model_lot_thread = ModelLotListThread("models")
        self.model_lot_thread.finished.connect(self.on_model_lot_loaded)
        self.model_lot_thread.failed.connect(self.on_model_lot_failed)
        self.model_lot_thread.start()

    def load_lots(self) -> None:
        model = self.model_combo.currentText().strip()
        if not model:
            QMessageBox.warning(self, _("입력 오류"), _("모델을 선택하거나 입력하세요."))
            return
        self.btn_search.setEnabled(False)
        save_setting_text("diagnosis/model", model)
        self.model_lot_thread = ModelLotListThread("lots", model)
        self.model_lot_thread.finished.connect(self.on_model_lot_loaded)
        self.model_lot_thread.failed.connect(self.on_model_lot_failed)
        self.model_lot_thread.start()

    def on_model_lot_loaded(self, mode: str, values: list[str]) -> None:
        self.btn_search.setEnabled(True)
        combo = self.model_combo if mode == "models" else self.lot_combo
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        if current:
            index = combo.findText(current)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.insertItem(0, current)
                combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self.result.append(f"{_('모델') if mode == 'models' else 'LOT'} 목록 {len(values)}개 로드")

    def on_model_lot_failed(self, message: str) -> None:
        self.btn_search.setEnabled(True)
        QMessageBox.critical(self, _("NAS 목록 로드 실패"), message)

    def validate_ski(self) -> None:
        json_path = Path(self.strip_json_in.text().strip())
        txt_paths = self.current_txt_paths()
        skipdata_text = self.skipdata_in.text().strip()
        ski_text = self.ski_in.text().strip()
        skipdata_path = Path(skipdata_text) if skipdata_text else Path("__missing_skipdata__")
        ski_path = Path(ski_text) if ski_text else Path("__missing_ski__")
        if not json_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("StripID JSON 파일을 선택하세요."))
            return
        if not txt_paths or any(not path.is_file() for path in txt_paths):
            QMessageBox.warning(self, _("입력 오류"), _("게더링 TXT 파일을 하나 이상 선택하세요."))
            return
        if not skipdata_path.is_dir():
            QMessageBox.warning(self, _("입력 오류"), _("BBT/Skipdata 폴더를 선택하세요."))
            return
        if not ski_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("최종 SKI 파일을 선택하세요."))
            return
        try:
            save_line_edit(self.strip_json_in, "diagnosis/strip_json")
            save_line_edit(self.txt_in, "diagnosis/txt_paths")
            save_line_edit(self.skipdata_in, "diagnosis/skipdata")
            save_line_edit(self.ski_in, "diagnosis/ski")
            summary = analyze_ski_validation(json_path, txt_paths, skipdata_path, ski_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("SKI 검증 실패"), str(exc))
            return
        self.ski_validation_summary = summary
        self.show_ski_validation(summary)

    def show_ski_validation(self, summary: dict) -> None:
        mismatches = summary["mismatch_rows"]
        bbt_only = summary["bbt_only_strips"]
        lines = [
            f"LOT: {summary['lot_id']}",
            f"StripID JSON Strip: {summary['total_strips']}개",
            f"SKI Strip: {summary['ski_strip_count']}개",
            f"BBT 불량만 존재하고 SKI와 일치: {len(bbt_only)}개",
            f"전체 좌표 일치: {len(summary['matched_strips'])}개",
            f"좌표 불일치: {len(mismatches)}개",
            f"예상 좌표가 있는데 SKI에 없는 Strip: {len(summary['missing_in_ski'])}개",
            f"StripID JSON에 없는 SKI Strip: {len(summary['ski_only_strips'])}개",
            "",
            _("[BBT 불량만 존재하는 Strip]"),
            ", ".join(bbt_only) if bbt_only else "-",
            "",
            _("[좌표 불일치 Strip 상위 100개]"),
        ]
        if mismatches:
            for item in mismatches[:100]:
                lines.append(
                    f"{item['strip_id']} | BBT {item['bbt_count']} | 게더링 {item['gather_count']} | "
                    f"추가 {item['added_count']} | 예상 {item['expected_count']} | SKI {item['ski_count']} | "
                    f"SKI 누락 {item['missing_count']} | SKI 초과 {item['extra_count']}"
                )
                if item["missing_coords"]:
                    lines.append(f"  SKI 누락: {', '.join(item['missing_coords'][:30])}")
                if item["extra_coords"]:
                    lines.append(f"  SKI 초과: {', '.join(item['extra_coords'][:30])}")
        else:
            lines.append("-")
        if summary["ski_only_strips"]:
            lines.extend(["", _("[StripID JSON에 없는 SKI Strip]"), ", ".join(summary["ski_only_strips"][:200])])
        self.result.setPlainText("\n".join(lines))

    def show_selected_strip_detail(self) -> None:
        strip_id = self.strip_search_in.text().strip().upper()
        if not strip_id:
            QMessageBox.warning(self, _("입력 오류"), _("상세 확인할 StripID를 입력하세요."))
            return
        txt_paths = self.current_txt_paths()
        skipdata_text = self.skipdata_in.text().strip()
        ski_text = self.ski_in.text().strip()
        skipdata_path = Path(skipdata_text) if skipdata_text else Path("__missing_skipdata__")
        ski_path = Path(ski_text) if ski_text else Path("__missing_ski__")
        if not txt_paths or any(not path.is_file() for path in txt_paths):
            QMessageBox.warning(self, _("입력 오류"), _("게더링 TXT 파일을 선택하세요."))
            return
        if not skipdata_path.is_dir():
            QMessageBox.warning(self, _("입력 오류"), _("BBT/Skipdata 폴더를 선택하세요."))
            return
        if not ski_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("최종 SKI 파일을 선택하세요."))
            return
        try:
            source_rows, block_count = gather_line_sources_for_strip(txt_paths, strip_id)
            bbt_coords = set(load_skipdata_folder(skipdata_path).get(strip_id, set()))
            ski_coords = set(load_ski_file(ski_path).get(strip_id, set()))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("상세 로그 실패"), str(exc))
            return

        coord_sources: dict[tuple[int, int], list[dict]] = defaultdict(list)
        code_counter: Counter[str] = Counter()
        for row in source_rows:
            coord = row["coord"]
            if coord is None:
                continue
            coord_sources[coord].append(row)
            code_counter[str(row["code"])] += 1

        gather_coords = set(coord_sources)
        added_coords = gather_coords - bbt_coords
        expected_final = bbt_coords | gather_coords
        missing_coords = expected_final - ski_coords
        extra_coords = ski_coords - expected_final

        lines = [
            f"StripID: {strip_id}",
            f"TXT 파일: {len(txt_paths)}개 / 해당 block: {block_count}개 / 게더링 라인: {len(source_rows)}개",
            f"BBT: {len(bbt_coords)}개 / 게더링 좌표: {len(gather_coords)}개 / 추가: {len(added_coords)}개 / 예상: {len(expected_final)}개 / SKI: {len(ski_coords)}개",
            f"SKI 누락: {len(missing_coords)}개 / SKI 초과: {len(extra_coords)}개",
            "",
            _("[TXT 파일]"),
            *[str(path) for path in txt_paths],
            "",
            _("[게더링 코드 분포]"),
            *([f"{code}: {count}" for code, count in sorted(code_counter.items())] or ["-"]),
            "",
            _("[게더링 좌표별 소스]"),
        ]
        if coord_sources:
            for coord in sorted(coord_sources, key=lambda item: (item[1], item[0])):
                marker = []
                if coord in bbt_coords:
                    marker.append("BBT")
                if coord in added_coords:
                    marker.append(_("추가"))
                if coord in ski_coords:
                    marker.append("SKI")
                lines.append(f"{format_xy_coords({coord})[0]} ({'/'.join(marker) if marker else '-'})")
                for row in coord_sources[coord][:10]:
                    lines.append(f"  {row['code']} | {row['path']} | {row['line']}")
                if len(coord_sources[coord]) > 10:
                    lines.append(f"  ... {len(coord_sources[coord]) - 10}건 생략")
        else:
            lines.append("-")

        if missing_coords:
            lines.extend(["", _("[SKI에 없는 예상 좌표]"), ", ".join(format_xy_coords(missing_coords))])
        if extra_coords:
            lines.extend(["", _("[SKI 초과 좌표]"), ", ".join(format_xy_coords(extra_coords))])
        self.result.setPlainText("\n".join(lines))

    def build_ski_validation_list_payload(self) -> dict:
        if not self.ski_validation_summary:
            raise ValueError(_("먼저 SKI 최종좌표 검증을 실행하세요."))
        summary = self.ski_validation_summary
        mismatch_strips = [str(item["strip_id"]) for item in summary["mismatch_rows"]]
        return {
            "schema": "dmt-studio-ski-validation-lists-v1",
            "lot_id": summary["lot_id"],
            "total_strips": summary["total_strips"],
            "ski_strip_count": summary["ski_strip_count"],
            "counts": {
                "bbt_only_strips": len(summary["bbt_only_strips"]),
                "matched_strips": len(summary["matched_strips"]),
                "mismatch_strips": len(mismatch_strips),
                "missing_in_ski": len(summary["missing_in_ski"]),
                "ski_only_strips": len(summary["ski_only_strips"]),
            },
            "strip_ids": {
                "bbt_only_strips": summary["bbt_only_strips"],
                "matched_strips": summary["matched_strips"],
                "mismatch_strips": mismatch_strips,
                "missing_in_ski": summary["missing_in_ski"],
                "ski_only_strips": summary["ski_only_strips"],
            },
            "mismatch_details": summary["mismatch_rows"],
        }

    def save_ski_validation_lists(self) -> None:
        try:
            payload = self.build_ski_validation_list_payload()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _("저장 오류"), str(exc))
            return
        lot = payload.get("lot_id", "ski_validation")
        path, _ext = QFileDialog.getSaveFileName(
            self,
            _("SKI 검증 리스트 JSON 저장"),
            f"{lot}_ski_validation_lists.json",
            "JSON Files (*.json)",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        QMessageBox.information(self, _("저장 완료"), path)

    def search_strip(self) -> None:
        strip_id = self.strip_search_in.text().strip().upper()
        model = self.model_combo.currentText().strip()
        lot = self.lot_combo.currentText().strip()
        if not strip_id:
            QMessageBox.warning(self, _("입력 오류"), _("검색할 StripID를 입력하세요."))
            return
        if not model or not lot:
            QMessageBox.warning(self, _("입력 오류"), _("NAS 검색용 모델과 LOT를 선택하거나 입력하세요."))
            return
        save_setting_text("diagnosis/model", model)
        save_setting_text("diagnosis/lot", lot)
        save_line_edit(self.strip_search_in, "diagnosis/strip_id")
        save_line_edit(self.txt_in, "diagnosis/txt_paths")
        save_line_edit(self.skipdata_in, "diagnosis/skipdata")
        save_line_edit(self.strip_json_in, "diagnosis/strip_json")
        save_line_edit(self.ski_in, "diagnosis/ski")
        try:
            self.result.setPlainText(self.build_local_strip_report(strip_id))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("로컬 좌표 검색 실패"), str(exc))
            return
        self.btn_search.setEnabled(False)
        self.search_thread = StripDefectSearchThread(model, lot, strip_id)
        self.search_thread.progress.connect(self.result.append)
        self.search_thread.finished.connect(self.on_strip_search_finished)
        self.search_thread.failed.connect(self.on_strip_search_failed)
        self.search_thread.start()

    def build_local_strip_report(self, strip_id: str) -> str:
        txt_paths = self.current_txt_paths()
        gather_lines: list[str] = []
        block_count = 0
        if txt_paths and all(path.is_file() for path in txt_paths):
            gather_lines, block_count = gather_lines_for_strip(txt_paths, strip_id)

        skipdata_text = self.skipdata_in.text().strip()
        bbt_coords: set[tuple[int, int]] = set()
        if skipdata_text:
            bbt_coords = load_skipdata_folder(Path(skipdata_text)).get(strip_id, set())
        gather_coords = {coord for line in gather_lines if (coord := parse_xy_from_gather_line(line)) is not None}
        added_coords = gather_coords - bbt_coords if bbt_coords else gather_coords

        parts = [
            f"StripID: {strip_id}",
            "",
            f"[BBT/Skipdata 불량 좌표] {len(bbt_coords)}개",
            "\n".join(format_xy_coords(bbt_coords)) if bbt_coords else "-",
            "",
            f"[추가 게더링 불량 좌표(BBT 제외)] {len(added_coords)}개",
            "\n".join(format_xy_coords(added_coords)) if added_coords else "-",
            "",
            f"[게더링 TXT 추가 불량] 블록 {block_count}개 / 라인 {len(gather_lines)}개",
            "\n".join(gather_lines) if gather_lines else "-",
            "",
            _("[NAS IVS 불량 검색]"),
        ]
        return "\n".join(parts)

    def on_strip_search_finished(self, payload: dict) -> None:
        self.btn_search.setEnabled(True)
        matched = payload["matched_folders"]
        defects = payload["nas_defects"]
        self.result.append("")
        self.result.append(f"[NAS 매칭 폴더] {len(matched)}개")
        if matched:
            self.result.append(json.dumps(matched, ensure_ascii=False, indent=2))
        else:
            self.result.append("-")
        self.result.append("")
        self.result.append(f"[NAS IVS 불량] {len(defects)}개")
        if defects:
            lines = [
                (
                    f"{item['machine']}/{item['trial']}/{item['strip_folder']} "
                    f"U{item['unit_idx']} {item['face']} "
                    f"X:{item['strip_pt_x']} Y:{item['strip_pt_y']} "
                    f"IVS:{item['ivs_1']} {item['code_main']} {item['code_sub']}"
                )
                for item in defects
            ]
            self.result.append("\n".join(lines))
        else:
            self.result.append("-")

    def on_strip_search_failed(self, message: str) -> None:
        self.btn_search.setEnabled(True)
        QMessageBox.critical(self, _("StripID 검색 실패"), message)
