"""Test tab: verifies core logic functions after modularization."""
from __future__ import annotations
from core.i18n import _

import io
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt


# ---------------------------------------------------------------------------
# Individual test functions
# ---------------------------------------------------------------------------

def _test_config_imports() -> None:
    from config import (  # noqa: F401
        APP_SETTINGS,
        DEFAULT_MODEL,
        LOT_ID_RE,
        MACHINES,
        NAS_HOST,
        NAS_PORT,
        NAS_ROOT,
        YIELD_MODEL_SETTINGS_PATH,
    )
    assert isinstance(DEFAULT_MODEL, str)
    assert isinstance(MACHINES, list) and len(MACHINES) > 0
    assert LOT_ID_RE.pattern


def _test_models_dataclasses() -> None:
    from core.models import BundleInfo, DefectRecord, GatherStrip, TrialContext, YieldStripBlock
    gs = GatherStrip(lot="LOT1", strip_id="LOT1-0101")
    assert gs.lot == "LOT1"
    assert gs.defect_lines == []
    assert gs.xout_applied is False

    ysb = YieldStripBlock(lot="LOT1", strip_id="LOT1-0101")
    assert ysb.block_count == 0
    assert isinstance(ysb.source_files, set)

    dr = DefectRecord(
        machine="AFVI1", trial="trial1", strip_folder="fld",
        strip_id="LOT1-0101", unit_idx=1, face="T",
        strip_pt_x=1, strip_pt_y=2, ivs_1="SM03",
        code_main="SM03", code_sub="", images=[],
        model="M1", lot="LOT1", container="Unit", img_merge_idx="0",
        def_side="F", img_pt_x=0.0, img_pt_y=0.0, source_xml="",
    )
    assert dr.unit_idx == 1


def _test_utils_basic() -> None:
    from core.utils import (
        extract_lot_id_from_text,
        infer_lot_id_from_values,
        lot_from_strip_id,
        sanitize_filename,
        split_path_list,
    )
    assert sanitize_filename("hello/world:test") != "hello/world:test"
    parts = split_path_list("a.txt; b.txt")
    assert len(parts) == 2

    lot = lot_from_strip_id("RX2262084400-0101")
    assert lot == "RX2262084400-"

    extracted = extract_lot_id_from_text("path/RX2262084400-0101/file.txt")
    assert extracted == "RX2262084400-"

    inferred = infer_lot_id_from_values("", "RX2262084400-")
    assert inferred == "RX2262084400-"


def _test_gather_parse_line() -> None:
    from core.gather import parse_gather_code, parse_xy_from_gather_line
    line = "LOT1,LOT1-0101,3,2,T,SM03"
    coord = parse_xy_from_gather_line(line)
    assert coord == (3, 2)
    code = parse_gather_code(line)
    assert code == "SM03", f"Expected 'SM03', got {code}"


def _test_gather_unique_lines() -> None:
    from core.gather import unique_lines
    lines = ["a", "b", "a", "c"]
    result = unique_lines(lines)
    assert result == ["a", "b", "c"]


def _test_gather_make_full_t_lines() -> None:
    from core.gather import make_full_t_lines
    lines = make_full_t_lines("RX1234-", "RX1234-0101", 2, 2, "170")
    assert len(lines) == 4
    for line in lines:
        assert "170" in line


def _test_gather_format_xy_coords() -> None:
    from core.gather import format_xy_coords
    coords = {(1, 2), (3, 4)}
    result = format_xy_coords(coords)
    assert len(result) == 2
    assert all("X" in r or "," in r for r in result)


def _test_gather_clone_strip() -> None:
    from core.gather import clone_gather_strip
    from core.models import GatherStrip
    gs = GatherStrip(lot="L", strip_id="L-01", defect_lines=["a", "b"])
    cloned = clone_gather_strip(gs)
    cloned.defect_lines.append("c")
    assert len(gs.defect_lines) == 2  # original unchanged


def _test_gather_merge_strips() -> None:
    from core.gather import merge_gather_strips
    from core.models import GatherStrip
    a = GatherStrip(lot="L", strip_id="L-01", defect_lines=["L-01,1,1,T,SM03"])
    b = GatherStrip(lot="L", strip_id="L-01", defect_lines=["L-01,2,2,T,SM04"])
    merged = merge_gather_strips(a, b)
    assert len(merged.defect_lines) == 2


def _test_gather_strip_payload_roundtrip() -> None:
    from core.gather import gather_strip_from_payload, gather_strip_to_payload
    from core.models import GatherStrip
    gs = GatherStrip(lot="L", strip_id="L-01", defect_lines=["L-01,1,1,T,SM03"], sources=["file.txt"])
    payload = gather_strip_to_payload(gs)
    restored = gather_strip_from_payload(payload)
    assert restored.strip_id == gs.strip_id
    assert restored.defect_lines == gs.defect_lines


def _test_gather_coords_by_code() -> None:
    from core.gather import coords_by_code
    lines = ["LOT1,LOT1-0101,1,2,T,SM03", "LOT1,LOT1-0101,3,4,T,SM04", "LOT1,LOT1-0101,1,2,T,SM03"]
    result = coords_by_code(lines)
    assert "SM03" in result
    assert (1, 2) in result["SM03"]


def _test_skipdata_regexes() -> None:
    import re
    from core.skipdata import RPT_STRIP_RE, RPT_XY_RE
    m = RPT_STRIP_RE.match("[RX2262084400-0101]")
    assert m is not None
    xy_line = "X : 5 Y : 3 = SM03"
    m2 = RPT_XY_RE.match(xy_line)
    assert m2 is not None


def _test_ivs_parse_float() -> None:
    from core.ivs import parse_float
    assert parse_float("3.14") == 3.14
    assert parse_float("") == 0.0
    assert parse_float("abc") == 0.0


def _test_ivs_build_summary_empty() -> None:
    from core.ivs import build_summary
    summary = build_summary([])
    assert summary["total_records"] == 0
    assert summary["by_defect_type"] == []


def _test_ivs_records_to_json() -> None:
    from core.ivs import records_to_json
    from core.models import DefectRecord
    rec = DefectRecord(
        machine="M1", trial="T1", strip_folder="F1",
        strip_id="LOT-01", unit_idx="1", face="T",
        strip_pt_x=1, strip_pt_y=2,
        ivs_1="SM03", code_main="SM03", code_sub="",
        images=[],
        model="M1", lot="LOT1", container="Unit", img_merge_idx="0",
        def_side="F", img_pt_x=0.0, img_pt_y=0.0, source_xml="",
    )
    result = records_to_json("M1", "LOT-01", [rec])
    assert isinstance(result, dict)
    assert result["strips"]["LOT-01"]["defect_count"] == 1


def _test_yield_parse_gather_file_empty() -> None:
    """parse_gather_file should return empty dict for a non-existent file."""
    from core.gather import parse_gather_file
    result = parse_gather_file(Path("__nonexistent__.txt"), "RX1234-")
    assert result == {}


def _test_yield_parse_yield_txt_empty() -> None:
    from core.yield_calc import parse_yield_txt
    result = parse_yield_txt(Path("__nonexistent__.txt"))
    assert result == {}


def _test_yield_build_strip_json_validation() -> None:
    from core.yield_calc import build_strip_json_validation
    json_ids = {"RX1-01", "RX1-02", "RX1-03"}
    gather_ids = {"RX1-02", "RX1-03", "RX1-04"}
    result = build_strip_json_validation(json_ids, gather_ids, "RX1-")
    assert "RX1-01" in result["json_only_strips"]
    assert "RX1-04" in result["gather_only_strips"]
    assert result["json_strip_count"] == 3
    assert result["gather_strip_count"] == 3


def _test_yield_merge_process_code_distribution() -> None:
    from core.yield_calc import merge_process_code_distribution
    dist = {
        "VRS:SM03": 5, "VRS:SM04": 3,
        "FVI:SM03": 2,
    }
    result = merge_process_code_distribution(dist)
    assert result["by_process"]["VRS"] == 8
    assert result["by_defect_code"]["SM03"] == 7


def _test_yield_lot_id_from_blocks() -> None:
    from core.models import YieldStripBlock
    from core.yield_calc import lot_id_from_blocks
    blocks = [
        YieldStripBlock(lot="RX2262-", strip_id="RX2262-0101"),
        YieldStripBlock(lot="RX2262-", strip_id="RX2262-0102"),
    ]
    lot = lot_id_from_blocks(blocks)
    assert lot == "RX2262-"


def _test_lm_data_unique_destination() -> None:
    from core.lm_data import unique_destination
    base = Path("/tmp/test.txt")
    dest = unique_destination(base)
    assert dest.name.startswith("test")


def _test_settings_helper() -> None:
    from ui.settings_helper import load_yield_model_settings, save_yield_model_settings
    original = load_yield_model_settings()
    assert isinstance(original, dict)
    # Write and read back a test entry
    test_key = "__test_model_xyz__"
    original[test_key] = {"units_per_strip": "99"}
    save_yield_model_settings(original)
    reloaded = load_yield_model_settings()
    assert reloaded.get(test_key, {}).get("units_per_strip") == "99"
    # Cleanup
    del reloaded[test_key]
    save_yield_model_settings(reloaded)


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------

TESTS: list[tuple[str, str]] = [
    ("config", _("설정 상수 임포트"), "_test_config_imports"),
    ("models", _("GatherStrip / YieldStripBlock / DefectRecord 데이터클래스"), "_test_models_dataclasses"),
    ("utils", _("기본 유틸 함수 (sanitize, split_path, lot_from_strip_id, extract_lot)"), "_test_utils_basic"),
    ("gather", "parse_xy_from_gather_line / parse_gather_code", "_test_gather_parse_line"),
    ("gather", _("unique_lines 중복 제거"), "_test_gather_unique_lines"),
    ("gather", _("make_full_t_lines 전체처리 라인 생성"), "_test_gather_make_full_t_lines"),
    ("gather", _("format_xy_coords 좌표 포맷"), "_test_gather_format_xy_coords"),
    ("gather", _("clone_gather_strip 깊은 복사"), "_test_gather_clone_strip"),
    ("gather", _("merge_gather_strips 병합"), "_test_gather_merge_strips"),
    ("gather", _("gather_strip payload 직렬화/역직렬화"), "_test_gather_strip_payload_roundtrip"),
    ("gather", _("coords_by_code 코드별 좌표 분류"), "_test_gather_coords_by_code"),
    ("skipdata", _("RPT 정규식 패턴 검증"), "_test_skipdata_regexes"),
    ("ivs", _("parse_float 숫자 파싱"), "_test_ivs_parse_float"),
    ("ivs", _("build_summary 빈 레코드 요약"), "_test_ivs_build_summary_empty"),
    ("ivs", _("records_to_json DefectRecord 직렬화"), "_test_ivs_records_to_json"),
    ("yield_calc", _("parse_gather_file 없는 파일 처리"), "_test_yield_parse_gather_file_empty"),
    ("yield_calc", _("parse_yield_txt 없는 파일 처리"), "_test_yield_parse_yield_txt_empty"),
    ("yield_calc", _("build_strip_json_validation 검증 로직"), "_test_yield_build_strip_json_validation"),
    ("yield_calc", _("merge_process_code_distribution 집계"), "_test_yield_merge_process_code_distribution"),
    ("yield_calc", _("lot_id_from_blocks 추출"), "_test_yield_lot_id_from_blocks"),
    ("lm_data", _("unique_destination 중복 회피"), "_test_lm_data_unique_destination"),
    ("settings", _("yield model settings 저장/로드"), "_test_settings_helper"),
]

_FUNC_MAP: dict[str, object] = {name: globals()[name] for _, _, name in TESTS}


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------

class _TestRunner(QThread):
    result_ready = Signal(int, bool, str)  # (index, passed, detail)
    all_done = Signal()

    def __init__(self, indices: list[int]) -> None:
        super().__init__()
        self.indices = indices

    def run(self) -> None:
        for idx in self.indices:
            _, label, func_name = TESTS[idx]
            func = _FUNC_MAP[func_name]
            buf = io.StringIO()
            try:
                func()  # type: ignore[call-arg]
                self.result_ready.emit(idx, True, "")
            except Exception:
                detail = traceback.format_exc()
                self.result_ready.emit(idx, False, detail)
        self.all_done.emit()


# ---------------------------------------------------------------------------
# Tab widget
# ---------------------------------------------------------------------------

class TestTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._runner: _TestRunner | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header_box = QGroupBox(_("Core Logic 단위 테스트"))
        header_layout = QHBoxLayout(header_box)
        self._lbl_summary = QLabel(_("테스트를 실행하세요."))
        self._lbl_summary.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_run = QPushButton(_("전체 실행"))
        btn_run.clicked.connect(self._run_all)
        btn_run_failed = QPushButton(_("실패만 재실행"))
        btn_run_failed.clicked.connect(self._run_failed)
        header_layout.addWidget(self._lbl_summary)
        header_layout.addWidget(btn_run)
        header_layout.addWidget(btn_run_failed)
        layout.addWidget(header_box)

        splitter = QSplitter(Qt.Vertical)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels([_("모듈"), _("설명"), _("결과")])
        self._tree.setColumnWidth(0, 120)
        self._tree.setColumnWidth(1, 380)
        self._tree.setColumnWidth(2, 80)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._tree)

        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText(_("실패한 테스트를 선택하면 에러 상세를 표시합니다."))
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

        # Populate tree with all tests in pending state
        self._items: list[QTreeWidgetItem] = []
        self._details: list[str] = []
        for module, label, func_name in TESTS:
            item = QTreeWidgetItem([module, label, _("대기")])
            self._tree.addTopLevelItem(item)
            self._items.append(item)
            self._details.append("")

    def _mark(self, idx: int, passed: bool, detail: str) -> None:
        item = self._items[idx]
        self._details[idx] = detail
        if passed:
            item.setText(2, "PASS")
            item.setForeground(2, Qt.darkGreen)
            item.setForeground(1, Qt.black)
        else:
            item.setText(2, "FAIL")
            item.setForeground(2, Qt.red)
            item.setForeground(1, Qt.red)

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        idx = self._items.index(items[0])
        detail = self._details[idx]
        if detail:
            self._detail.setPlainText(detail)
        else:
            self._detail.setPlainText(_("(통과 또는 미실행)"))

    def _on_result(self, idx: int, passed: bool, detail: str) -> None:
        self._mark(idx, passed, detail)
        total_run = sum(1 for item in self._items if item.text(2) in ("PASS", "FAIL"))
        passed_count = sum(1 for item in self._items if item.text(2) == "PASS")
        self._lbl_summary.setText(f"진행 중... {total_run}/{len(TESTS)} | 통과 {passed_count}")

    def _on_all_done(self) -> None:
        passed_count = sum(1 for item in self._items if item.text(2) == "PASS")
        failed_count = sum(1 for item in self._items if item.text(2) == "FAIL")
        self._lbl_summary.setText(
            f"완료: {len(TESTS)}개 실행 | 통과 {passed_count} | 실패 {failed_count}"
        )

    def _run_indices(self, indices: list[int]) -> None:
        if self._runner and self._runner.isRunning():
            return
        for idx in indices:
            self._items[idx].setText(2, _("실행중"))
            self._items[idx].setForeground(2, Qt.darkBlue)
            self._details[idx] = ""
        self._runner = _TestRunner(indices)
        self._runner.result_ready.connect(self._on_result)
        self._runner.all_done.connect(self._on_all_done)
        self._runner.start()

    def _run_all(self) -> None:
        self._run_indices(list(range(len(TESTS))))

    def _run_failed(self) -> None:
        failed = [i for i, item in enumerate(self._items) if item.text(2) == "FAIL"]
        if not failed:
            failed = list(range(len(TESTS)))
        self._run_indices(failed)
