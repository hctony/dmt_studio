"""The Excel code list and exact Unit assignment / source comparisons."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QAbstractItemView,
    QPushButton,QGroupBox,QTextEdit,QSplitter,QComboBox)
from core.i18n import _
from core.report_codes import ordered_report_codes
from core.report_trace import trace_rows,matching_groups,REASONS
from core.report_evidence import frozen_text
from ui.worker_lifetime import start_worker
from workers.report_workers import ReportEvidenceWorker


class ReportPreviewDialog(QDialog):
    def __init__(self,summary,report,parent=None):
        super().__init__(parent)
        self.setWindowTitle(_('성적서 코드 미리보기'));self.resize(1400,950)
        self.setProperty('dmt_presentation','report-code-preview')
        self.summary=summary;self.report=report;self.trace=summary.get('report_trace',{})
        self.codes=ordered_report_codes(report);self.code='';self.offset=0;self.rows=[];self._selection=None
        self._generation=0;self._pending=None;self.worker=None;self._closed=False
        self.destroyed.connect(lambda:setattr(self,'_closed',True))
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(f"{report.get('model_name','')} / {report.get('lot_id','')} · "+_('전체 Unit')+f": {report.get('total_units',0)}"))
        self.notice=QLabel(_('엑셀에 기록될 코드와 수량입니다. 170은 OT06으로 합산합니다. 원본 조회는 A PC에서 수행합니다.'))
        self.notice.setWordWrap(True);layout.addWidget(self.notice)
        top=QSplitter(Qt.Horizontal);top.setProperty('dmt_presentation','report-code-tables')
        self.code_table=QTableWidget(len(self.codes),3)
        self.code_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.code_table.setHorizontalHeaderLabels([_('성적서 코드'),_('수량'),_('전체 Unit 대비 %')])
        for row,(code,count) in enumerate(self.codes):
            for col,value in enumerate((code,count,f"{count/report['total_units']*100:.6f}" if report.get('total_units') else '—')):
                item=QTableWidgetItem(str(value));self.code_table.setItem(row,col,item)
                if code in ('1111','UNKNOWN','9999','SM03_DUMMY9999'):item.setBackground(QColor('#fff1ce'))
        top.addWidget(self.code_table)
        right=QGroupBox(_('코드별 Strip·Unit 배정'))
        right_layout=QVBoxLayout(right)
        self.unit_table=QTableWidget(0,6);self.unit_table.setHorizontalHeaderLabels(['StripID','X','Y',_('Unit (좌표 환산)'),_('공정'),_('계산 사유')])
        self.unit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        right_layout.addWidget(self.unit_table)
        page=QHBoxLayout();self.previous=QPushButton(_('이전'));self.next=QPushButton(_('다음'));self.page_label=QLabel()
        page.addWidget(self.previous);page.addWidget(self.page_label);page.addWidget(self.next);right_layout.addLayout(page)
        top.addWidget(right);layout.addWidget(top)
        self.proof=QTextEdit();self.proof.setReadOnly(True);self.proof.setProperty('dmt_literal_text',True);layout.addWidget(self.proof)
        source=QSplitter(Qt.Horizontal);source.setProperty('dmt_presentation','report-code-sources')
        self.text_combo=QComboBox();self.xml_combo=QComboBox()
        for combo in (self.text_combo,self.xml_combo):combo.setProperty('dmt_literal_text',True)
        self.txt_view=self.panel(source,_('TXT / BBT 원문'),self.text_combo)
        self.ski_view=self.panel(source,_('SKI 원문'))
        self.xml_view=self.panel(source,_('XML 원문'),self.xml_combo)
        layout.addWidget(source)
        actions=QHBoxLayout();self.search_xml=QPushButton(_('NAS XML 찾기'));self.reload=QPushButton(_('원본 다시 조회'));self.stop=QPushButton(_('조회 중지'));self.status=QLabel();self.status.setWordWrap(True);close=QPushButton(_('닫기'))
        for widget in (self.search_xml,self.reload,self.stop,self.status,close):actions.addWidget(widget)
        layout.addLayout(actions)
        self.code_table.currentCellChanged.connect(self.select_code)
        self.unit_table.currentCellChanged.connect(self.select_unit)
        self.previous.clicked.connect(lambda:self.page(-200));self.next.clicked.connect(lambda:self.page(200))
        self.search_xml.clicked.connect(lambda:self.queue_source(search=True));self.reload.clicked.connect(lambda:self.queue_source())
        self.text_combo.currentIndexChanged.connect(lambda:self.queue_source());self.xml_combo.currentIndexChanged.connect(lambda:self.queue_source())
        self.stop.clicked.connect(self.stop_source);close.clicked.connect(self.reject);self.finished.connect(self.on_close)
        self.progress_timer=QTimer(self);self.progress_timer.timeout.connect(self.show_progress);self.progress_timer.start(400)
        if not self.trace:
            self.notice.setText(_('이 계산에는 좌표별 추적 기록이 없습니다. 공정 TXT 입력으로 수율을 다시 계산하세요.'))
        if self.codes:self.code_table.setCurrentCell(next((i for i,(code,_) in enumerate(self.codes) if code in ('1111','UNKNOWN')),0),0)
        self.stop.setEnabled(False)

    @staticmethod
    def panel(splitter,title,combo=None):
        group=QGroupBox(title);layout=QVBoxLayout(group)
        if combo is not None:layout.addWidget(combo)
        text=QTextEdit();text.setReadOnly(True);text.setProperty('dmt_literal_text',True);layout.addWidget(text);splitter.addWidget(group);return text

    def select_code(self,row,*args):
        if row<0 or row>=len(self.codes):return
        code,count=self.codes[row]
        if code==self.code:return
        self.code=code;self.offset=0
        traced=sum(len(group['coords']) for group in matching_groups(self.trace,code))
        if traced!=count:
            self.notice.setText(_('성적서 수량과 좌표 추적 수량이 다릅니다. 집계 규칙을 확인하세요.')+f' {code}: {count} / {traced}')
        else:self.notice.setText(_('엑셀 수량과 좌표 추적 수량 일치')+f': {code} / {count}')
        self.fill_units()

    def page(self,delta):
        self.offset=max(0,self.offset+delta);self.fill_units()

    def fill_units(self):
        self._selection=None;self.stop_source()
        self.rows,total=trace_rows(self.trace,self.code,self.offset)
        self.unit_table.blockSignals(True);self.unit_table.setRowCount(len(self.rows))
        for index,row in enumerate(self.rows):
            info=self.trace.get('strips',{}).get(row['strip_id'],{});unit=(row['y']-1)*info.get('x_count',0)+row['x']-1
            for column,value in enumerate((row['strip_id'],row['x'],row['y'],unit,row['process'],_(REASONS.get(row['reason'],row['reason'])))):
                self.unit_table.setItem(index,column,QTableWidgetItem(str(value)))
        self.unit_table.blockSignals(False)
        self.page_label.setText(f'{self.offset+1 if total else 0}–{self.offset+len(self.rows)} / {total}')
        self.previous.setEnabled(self.offset>0);self.next.setEnabled(self.offset+len(self.rows)<total)
        if self.rows:self.unit_table.setCurrentCell(0,0);self.select_unit(0)
        else:
            self.proof.setPlainText(_('조회할 배정 좌표가 없습니다.'))
            for view in (self.txt_view,self.ski_view,self.xml_view):view.clear()
            for combo in (self.text_combo,self.xml_combo):combo.blockSignals(True);combo.clear();combo.blockSignals(False)

    def select_unit(self,index,*args):
        if index<0 or index>=len(self.rows):return
        row=self.rows[index];key=(row['strip_id'],row['x'],row['y'],row['code'],row['reason'],row['process'])
        if key==self._selection:return
        self._selection=key
        for combo in (self.text_combo,self.xml_combo):combo.blockSignals(True);combo.clear();combo.blockSignals(False)
        self.proof.setPlainText(frozen_text(self.trace,row));self.txt_view.clear();self.ski_view.clear();self.xml_view.clear()
        self.queue_source()

    def queue_source(self,search=False):
        if self._closed or not self._selection:return
        index=self.unit_table.currentRow()
        if index<0 or index>=len(self.rows):return
        self._generation+=1
        self._pending=(self._generation,self.rows[index],self.xml_combo.currentText(),self.text_combo.currentText(),search)
        if self.worker and self.worker.isRunning():self.worker.stop()
        QTimer.singleShot(100,self.start_pending)

    def start_pending(self):
        if self._closed or not self._pending:return
        if self.worker and self.worker.isRunning():QTimer.singleShot(100,self.start_pending);return
        generation,row,xml,text,search=self._pending;self._pending=None
        self.worker=ReportEvidenceWorker(self.trace,row,self.report.get('model_name',''),self.report.get('lot_id',''),xml,text,search)
        self.worker.finished.connect(lambda result:self.apply_source(generation,result))
        self.worker.failed.connect(lambda message:self.failed_source(generation,message))
        self.worker.cancelled.connect(lambda:self.failed_source(generation,_('원본 조회 중지됨')))
        self.stop.setEnabled(True);self.status.setText(_('원본 조회 중…'))
        start_worker(self,self.worker)

    def apply_source(self,generation,result):
        if generation!=self._generation or self._closed:return
        self.txt_view.setPlainText(result.get('text',''));self.ski_view.setPlainText(result.get('ski',''));self.xml_view.setPlainText(result.get('xml',''))
        for combo,key,selected in ((self.text_combo,'text_paths','selected_text'),(self.xml_combo,'xml_paths','selected_xml')):
            combo.blockSignals(True);combo.clear();combo.addItems(result.get(key,[]));combo.setCurrentText(result.get(selected,''));combo.blockSignals(False)
        self.status.setText(_('원본 조회 완료 — 현재 파일 내용과 계산 시점 근거를 비교하세요.'));self.stop.setEnabled(False)

    def failed_source(self,generation,message):
        if generation==self._generation and not self._closed:self.status.setText(message);self.stop.setEnabled(False)

    def stop_source(self):
        self._generation+=1;self._pending=None
        if self.worker and self.worker.isRunning():self.worker.stop()
        self.stop.setEnabled(False);self.status.setText(_('원본 조회 중지 요청'))

    def show_progress(self):
        if self.worker and self.worker.isRunning() and self.stop.isEnabled():
            self.status.setText(_('원본 조회 중…')+' '+self.worker.dmt_progress.get('detail',''))

    def on_close(self,*args):
        self._closed=True;self.stop_source();self.progress_timer.stop()
