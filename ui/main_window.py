"""Main application window."""
from __future__ import annotations
from core.i18n import _

from PySide6.QtWidgets import QMainWindow, QTabWidget, QComboBox, QMessageBox, QApplication
from core.i18n import Translator

from ui.tabs.full_xml_barcode_tab import FullXmlBarcodeTab
from ui.tabs.gathering_strip_search_tab import GatheringStripSearchTab
from ui.tabs.gathering_tab import GatheringTab
from ui.tabs.ivs_tab import IvsStudioTab
from ui.tabs.lm_data_tab import LMDataTab
from ui.tabs.strip_diagnosis_tab import StripDiagnosisTab
from ui.tabs.strip_id_tab import StripIdTab
from ui.tabs.test_tab import TestTab
from ui.tabs.yield_tab import YieldTab
from ui.tabs.inspection_stats_tab import InspectionStatsTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DMT Analysis Studio")
        self.resize(1400, 900)
        tabs = QTabWidget()
        tabs.addTab(IvsStudioTab(), _("IVS 분석/조회"))
        tabs.addTab(StripIdTab(), _("StripID 추출"))
        tabs.addTab(FullXmlBarcodeTab(), _("전체 XML Barcode"))
        tabs.addTab(GatheringTab(), _("게더링 집계"))
        tabs.addTab(GatheringStripSearchTab(), _("게더링 Strip 검색"))
        tabs.addTab(InspectionStatsTab(), _("검사 통계"))
        tabs.addTab(YieldTab(), _("수율 계산"))
        tabs.addTab(StripDiagnosisTab(), _("StripID 진단"))
        tabs.addTab(LMDataTab(), "LM Data")
        tabs.addTab(TestTab(), _("테스트"))
        
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("한국어", "ko")
        self.lang_combo.addItem("중국어(번체)", "zh_TW")
        
        # Set current language from Translator
        current_lang = Translator().current_lang
        idx = self.lang_combo.findData(current_lang)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
            
        self.lang_combo.currentIndexChanged.connect(self.change_language)
        tabs.setCornerWidget(self.lang_combo)
        
        self.setCentralWidget(tabs)
        tabs.currentChanged.connect(lambda index: tabs.widget(6).apply_workflow_inputs() if index == 6 else None)
        
    def change_language(self, index: int) -> None:
        lang_code = self.lang_combo.itemData(index)
        Translator().load_language(lang_code)
        reply = QMessageBox.question(
            self, 
            _("언어 변경"), 
            _("언어가 변경되었습니다. 완전히 적용하려면 프로그램을 종료하고 다시 실행해야 합니다. 지금 종료하시겠습니까?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.quit()
