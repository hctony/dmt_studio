from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog,QWidget,QVBoxLayout,QFormLayout,QComboBox,QLineEdit,QLabel,QPushButton,QHBoxLayout,QMessageBox
from core.i18n import _


class ImageRangeDialog(QDialog):
    submitted=Signal(str,int,int)

    def __init__(self,counts,download=False,current='',parent=None):
        super().__init__(parent);self.counts=counts;self.download=download
        self.setProperty('dmt_presentation','image-range-dialog')
        self.setWindowTitle(_('이미지 다운로드 범위') if download else _('이미지 추가 보기 범위'))
        layout=QVBoxLayout(self);fields=QWidget();form=QFormLayout(fields);self.code=QComboBox();self.code.addItems(sorted(counts))
        self.start=QLineEdit();self.end=QLineEdit();self.total=QLabel()
        form.addRow(_('불량코드'),self.code);form.addRow(_('전체 이미지 세트'),self.total)
        form.addRow(_('시작 번호 (1 기준)'),self.start);form.addRow(_('끝 번호 (포함)'),self.end);layout.addWidget(fields)
        note=QLabel(_('이미지 1세트는 같은 검사 항목의 조명 이미지입니다. 실제 파일은 조회·다운로드할 때 읽습니다.'))
        note.setWordWrap(True);layout.addWidget(note)
        if not download:layout.addWidget(QLabel(_('기본 10건에 선택한 범위를 추가합니다. 한 번에 최대 200건까지 볼 수 있습니다.')))
        row=QHBoxLayout();apply=QPushButton(_('다운로드') if download else _('추가로 보기'));cancel=QPushButton(_('취소'))
        apply.clicked.connect(self.choose);cancel.clicked.connect(self.reject);row.addWidget(apply);row.addWidget(cancel);layout.addLayout(row)
        self.code.currentTextChanged.connect(self.change_code)
        if current in counts:self.code.setCurrentText(current)
        self.change_code(self.code.currentText())

    def change_code(self,code):
        total=self.counts.get(code,0);self.total.setText(str(total))
        start=1 if self.download or total<=10 else 11
        self.start.setText(str(start));self.end.setText(str(total if self.download else min(total,start+9)))

    def choose(self):
        code=self.code.currentText();total=self.counts.get(code,0)
        try:
            start,end=int(self.start.text()),int(self.end.text())
        except ValueError:
            QMessageBox.warning(self,_('범위 확인'),_('범위는 1부터 전체 건수 안에서 선택하세요.'));return
        try:
            if not 1<=start<=end<=total:raise ValueError(_('범위는 1부터 전체 건수 안에서 선택하세요.'))
            if not self.download and end-start+1>200:raise ValueError(_('한 번에 추가로 볼 범위는 최대 200건입니다.'))
        except ValueError as error:
            QMessageBox.warning(self,_('범위 확인'),str(error));return
        self.accept();self.submitted.emit(code,start,end)
