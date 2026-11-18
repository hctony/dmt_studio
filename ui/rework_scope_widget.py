from PySide6.QtCore import QDate
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QCheckBox, QDateEdit, QPushButton, QMessageBox
from core.i18n import _
from core import rework_scope


class ReworkScopeWidget(QGroupBox):
    def __init__(self, model_in, lot_in):
        super().__init__(_('재입고 수집 범위 (모델·LOT 공통)'))
        self.model_in=model_in;self.lot_in=lot_in
        row=QHBoxLayout(self)
        self.enabled_in=QCheckBox(_('재입고일 이후만 수집'))
        self.date_in=QDateEdit(QDate.currentDate());self.date_in.setCalendarPopup(True)
        self.date_in.setDisplayFormat('yyyy-MM-dd')
        self.reuse_xml_in=QCheckBox(_('기존 IVS XML 유지 · 결과 TXT만 재수집'))
        self.btn_load=QPushButton(_('저장된 범위 조회'));self.btn_load.clicked.connect(self.refresh)
        self.btn_save=QPushButton(_('수집 범위 적용'));self.btn_save.clicked.connect(self.save)
        for w in [self.enabled_in,self.date_in,self.reuse_xml_in,self.btn_load,self.btn_save]:row.addWidget(w)
        model_in.editingFinished.connect(self.refresh);lot_in.editingFinished.connect(self.refresh)
        self.refresh()

    def refresh(self):
        value=rework_scope.load(self.model_in.text(),self.lot_in.text())
        self.enabled_in.setChecked(value.get('enabled',False));self.reuse_xml_in.setChecked(value.get('reuse_xml',False))
        date=QDate.fromString(value.get('date',''),'yyyy-MM-dd')
        if date.isValid():self.date_in.setDate(date)

    def save(self):
        try:
            rework_scope.save(self.model_in.text(),self.lot_in.text(),self.enabled_in.isChecked(),
                self.date_in.date().toString('yyyy-MM-dd'),self.reuse_xml_in.isChecked())
            QMessageBox.information(self,_('저장 완료'),_('수집 범위가 저장되었습니다. 기존 결과는 다시 수집·계산해야 합니다.'))
        except Exception as error:QMessageBox.warning(self,_('입력 오류'),str(error))
