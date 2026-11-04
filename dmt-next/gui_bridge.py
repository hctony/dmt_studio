"""Headless original UI controller, rendered by Electron (no native analysis windows)."""
from __future__ import annotations
import base64
import json
import os
import sys
import threading
import traceback
import time
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6 import QtCore as C, QtWidgets as W
import shiboken6


class Controller(C.QObject):
    incoming = C.Signal(dict)

    def __init__(self, app, emit):
        super().__init__()
        self.app, self.emit = app, emit
        self.widgets, self.pages, self.waiters = {}, {}, {}
        self.pixmaps = {}
        self.request_sequence = 0
        self.input_path = Path(os.environ.get('DMT_DATA_DIR', '.')) / 'gui-inputs.json'
        self.install_dialogs()
        from ui.main_window import MainWindow
        from core.i18n import Translator
        # The renderer translates the canonical Korean labels without rebuilding state.
        translator=Translator()
        translator.current_lang='ko'
        translator.translations={}
        self.window = MainWindow()
        self.window.resize(1440, 1000)
        self.restore_inputs()
        from workflow_bridge import WorkflowBridge
        self.action_depth = 0
        self.workflow = WorkflowBridge(self, self.input_path.parent)
        from model_profiles import ModelProfiles
        self.model_profiles = ModelProfiles(self)
        self.window.show()
        self.incoming.connect(self.handle, C.Qt.QueuedConnection)
        self.timer = C.QTimer(self)
        self.timer.timeout.connect(self.publish)
        self.timer.start(500)
        self.last_snapshot = ''

    def ask(self, kind, data):
        self.request_sequence += 1
        key = self.request_sequence
        loop = C.QEventLoop()
        record = {'loop': loop, 'value': None}
        self.waiters[key] = record
        self.emit({'event':'dialog', 'dialog':dict(data, kind=kind, id=key)})
        loop.exec()
        self.waiters.pop(key, None)
        return record['value']

    def install_dialogs(self):
        def files(kind, parent=None, caption='', directory='', filter='', *rest, **kwargs):
            value = self.ask(kind, {'title':caption, 'path':directory, 'filter':filter})
            if kind == 'directory': return value or ''
            return (value or ([] if kind == 'files' else ''), '')
        W.QFileDialog.getOpenFileName = staticmethod(lambda *a, **kw: files('file', *a, **kw))
        W.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **kw: files('files', *a, **kw))
        W.QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: files('save', *a, **kw))
        W.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **kw: files('directory', *a, **kw))
        def message(kind, parent, title, text, buttons=None, defaultButton=None, **kw):
            if hasattr(self,'workflow'):
                self.workflow.history.record('message',level=kind,title=title,text=self.workflow.history.clean(text)[:6000])
            buttons = buttons if buttons is not None else (W.QMessageBox.Yes | W.QMessageBox.No if kind == 'question' else W.QMessageBox.Ok)
            options = [{'name':b.name, 'value':b.value} for b in (W.QMessageBox.Ok,W.QMessageBox.Yes,W.QMessageBox.No,W.QMessageBox.Cancel,W.QMessageBox.Close,W.QMessageBox.Retry,W.QMessageBox.Abort) if buttons & b]
            result = self.ask('message', {'title':title, 'text':text, 'level':kind, 'buttons':options})
            return W.QMessageBox.StandardButton(result or W.QMessageBox.Cancel.value)
        for kind in ('information','warning','critical','question'):
            setattr(W.QMessageBox, kind, staticmethod(lambda *a, _kind=kind, **kw: message(_kind,*a,**kw)))
        def item(parent, title, label, items, current=0, editable=True, *rest, **kwargs):
            result = self.ask('item', {'title':title, 'text':label,'items':list(items),'current':current,'editable':editable})
            return (result or '', result is not None)
        W.QInputDialog.getItem = staticmethod(item)

    def input_widgets(self):
        tabs = self.window.centralWidget()
        for i in range(tabs.count()):
            roots = [tabs.widget(i)]
            roots += tabs.widget(i).findChildren(W.QWidget)
            seen = set()
            for j, owner in enumerate(roots):
                for name, value in vars(owner).items():
                    if isinstance(value, (W.QLineEdit,W.QComboBox,W.QCheckBox,W.QRadioButton,W.QDateEdit)) and id(value) not in seen:
                        seen.add(id(value))
                        yield f'{i}/{type(owner).__name__}:{getattr(owner,"mode","")}/{name}', value

    def save_inputs(self):
        data = {}
        for key, w in self.input_widgets():
            if isinstance(w,W.QLineEdit): data[key] = w.text()
            elif isinstance(w,W.QComboBox): data[key] = w.currentText()
            elif isinstance(w,W.QDateEdit): data[key] = w.date().toString('yyyy-MM-dd')
            else: data[key] = w.isChecked()
        self.input_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.input_path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        os.replace(temp,self.input_path)

    def restore_inputs(self):
        if not self.input_path.exists(): return
        try: data = json.loads(self.input_path.read_text(encoding='utf-8'))
        except (ValueError,OSError): return
        for key,w in self.input_widgets():
            if key not in data: continue
            value=data[key]
            if isinstance(w,W.QLineEdit): w.setText(str(value))
            elif isinstance(w,W.QComboBox): w.setCurrentText(str(value))
            elif isinstance(w,W.QDateEdit): w.setDate(C.QDate.fromString(str(value),'yyyy-MM-dd'))
            else: w.setChecked(bool(value))

    def task_progress(self):
        tasks=[];seen=set()
        for owner in [self.window, *self.window.findChildren(W.QWidget)]:
            for worker in vars(owner).values():
                if not isinstance(worker,C.QThread) or not shiboken6.isValid(worker) or not worker.isRunning() or id(worker) in seen: continue
                seen.add(id(worker))
                if not hasattr(worker,'_dmt_started'):
                    worker._dmt_started=time.monotonic()
                task=dict(getattr(worker,'dmt_progress',{'stage':'작업 진행 중','value':0,'max':0,'detail':''}))
                task['elapsed']=int(time.monotonic()-worker._dmt_started)
                tasks.append(task)
        return tasks

    def busy(self):
        for owner in [self.window, *self.window.findChildren(W.QWidget)]:
            for value in vars(owner).values():
                if isinstance(value,C.QThread) and shiboken6.isValid(value) and value.isRunning(): return True
        return bool(self.waiters)

    def register(self,w):
        key=str(id(w)); self.widgets[key]=w; return key

    def layout(self,layout):
        if layout is None: return []
        result=[]
        for i in range(layout.count()):
            item=layout.itemAt(i)
            child=self.widget(item.widget()) if item.widget() else ({'type':'layout','direction':'row' if isinstance(item.layout(),W.QHBoxLayout) else 'column','children':self.layout(item.layout())} if item.layout() else None)
            if child:
                if isinstance(layout,W.QFormLayout):
                    row,role=layout.getItemPosition(i); child['formRow']=row; child['formRole']=role.value
                result.append(child)
        return result

    def widget(self,w):
        if w is None or w.isHidden(): return None
        key=self.register(w); d={'id':key,'enabled':w.isEnabled(),'type':'container','presentation':w.property('dmt_presentation') or ''}
        if isinstance(w,W.QTabWidget):
            return dict(d,type='tabs',index=w.currentIndex(),labels=[w.tabText(i) for i in range(w.count())],content=self.widget(w.currentWidget()))
        if isinstance(w,W.QLineEdit): return dict(d,type='input',value=w.text(),placeholder=w.placeholderText(),readonly=w.isReadOnly())
        if isinstance(w,W.QComboBox): return dict(d,type='select',value=w.currentText(),index=w.currentIndex(),items=[w.itemText(i) for i in range(w.count())],editable=w.isEditable())
        if isinstance(w,W.QDateEdit): return dict(d,type='date',value=w.date().toString('yyyy-MM-dd'))
        if isinstance(w,W.QAbstractButton): return dict(d,type='check' if isinstance(w,W.QCheckBox) else 'radio' if isinstance(w,W.QRadioButton) else 'button',text=w.text(),checked=w.isChecked())
        if isinstance(w,W.QLabel):
            pix=w.property('dmt_full_pixmap') or w.pixmap()
            if pix and not pix.isNull():
                cached=self.pixmaps.get(key)
                if not cached or cached[0]!=pix.cacheKey():
                    data=C.QByteArray(); buffer=C.QBuffer(data); buffer.open(C.QIODevice.WriteOnly); pix.save(buffer,'PNG')
                    cached=(pix.cacheKey(),'data:image/png;base64,'+base64.b64encode(bytes(data)).decode())
                    self.pixmaps[key]=cached
                return dict(d,type='image',src=cached[1])
            return dict(d,type='label',text=w.text())
        if isinstance(w,W.QTextEdit): return dict(d,type='text',text=w.toPlainText(),readonly=w.isReadOnly())
        if isinstance(w,W.QProgressBar): return dict(d,type='progress',value=w.value(),max=w.maximum())
        if isinstance(w,W.QTableWidget):
            start=self.pages.get(key,0); start=min(start,max(0,((w.rowCount()-1)//200)*200)); rows=[]
            for r in range(start,min(start+200,w.rowCount())):
                rows.append([w.item(r,c).text() if w.item(r,c) else '' for c in range(w.columnCount())])
            return dict(d,type='table',start=start,total=w.rowCount(),headers=[w.horizontalHeaderItem(c).text() if w.horizontalHeaderItem(c) else str(c+1) for c in range(w.columnCount())],rows=rows,selected=w.currentRow(),sortable=w.isSortingEnabled(),sortColumn=w.horizontalHeader().sortIndicatorSection(),sortDescending=w.horizontalHeader().sortIndicatorOrder()==C.Qt.DescendingOrder)
        if isinstance(w,W.QListWidget):
            start=self.pages.get(key,0); start=min(start,max(0,((w.count()-1)//200)*200)); rows=[]
            for r in range(start,min(start+200,w.count())):
                item=w.item(r); rows.append({'text':item.text(),'checked':item.checkState()==C.Qt.Checked,'checkable':bool(item.flags()&C.Qt.ItemIsUserCheckable),'selected':item.isSelected()})
            return dict(d,type='list',start=start,total=w.count(),rows=rows)
        if isinstance(w,W.QTreeWidget):
            def node(item,route): return {'route':route,'text':[item.text(i) for i in range(w.columnCount())],'children':[node(item.child(i),route+[i]) for i in range(item.childCount())]}
            return dict(d,type='tree',headers=[w.headerItem().text(i) for i in range(w.columnCount())],rows=[node(w.topLevelItem(i),[i]) for i in range(w.topLevelItemCount())])
        if isinstance(w,W.QSplitter): return dict(d,type='layout',direction='row' if w.orientation()==C.Qt.Horizontal else 'column',children=[self.widget(w.widget(i)) for i in range(w.count())])
        if isinstance(w,W.QDialogButtonBox): return dict(d,type='layout',direction='row',children=[self.widget(b) for b in w.buttons()])
        return dict(d,title=w.title() if isinstance(w,W.QGroupBox) else '',type='group' if isinstance(w,W.QGroupBox) else 'container',form=isinstance(w.layout(),W.QFormLayout),horizontal=isinstance(w.layout(),W.QHBoxLayout),children=self.layout(w.layout()))

    def snapshot(self):
        dialogs=[w for w in self.app.topLevelWidgets() if isinstance(w,W.QDialog) and w.isVisible()]
        return {'root':self.widget(self.window.centralWidget()),'dialogs':[{'title':w.windowTitle(),'id':self.register(w),'content':self.widget(w)} for w in dialogs], 'busy':self.busy(), 'tasks':self.task_progress()}

    def publish(self):
        try:
            self.workflow.poll()
            snapshot=self.snapshot(); encoded=json.dumps(snapshot,ensure_ascii=False)
            if encoded!=self.last_snapshot:
                self.last_snapshot=encoded;self.emit({'event':'snapshot','snapshot':snapshot})
        except Exception: traceback.print_exc(file=sys.stderr)

    @C.Slot(dict)
    def handle(self,request):
        try:
            method=request.get('method'); a=request.get('args',{})
            if method=='dialog.reply':
                record=self.waiters.get(a['id'])
                if record: record['value']=a.get('value');record['loop'].quit()
                result=True
            elif method=='gui.snapshot': result=self.snapshot()
            elif method.startswith('workflow.'):
                result=self.workflow.handle(method.split('.',1)[1],a)
            elif method=='gui.reload-models':
                self.window.centralWidget().widget(6).restore_model_units()
                self.save_inputs();result=True
            elif method=='gui.models': result=self.model_profiles.status()
            elif method=='gui.import-models': result=self.model_profiles.import_data(a['data'])
            elif method=='gui.apply-model': result=self.model_profiles.apply(a['model'], a.get('gathering', False))
            elif method=='gui.action':
                w=self.widgets.get(str(a['id']))
                if w is None or not shiboken6.isValid(w): raise ValueError('Control no longer exists')
                if not w.isEnabled(): raise ValueError('Control is disabled')
                action=a['action'];v=a.get('value')
                if action=='click' and isinstance(w,W.QAbstractButton):
                    started=time.monotonic();tab=self.window.centralWidget().currentIndex()
                    self.action_depth+=1
                    try: w.click()
                    finally:
                        self.action_depth-=1
                        self.workflow.action(tab,w.text(),action,started)
                elif action=='input' and isinstance(w,W.QLineEdit): w.setText(str(v));w.editingFinished.emit()
                elif action=='text' and isinstance(w,W.QTextEdit) and not w.isReadOnly(): w.setPlainText(str(v))
                elif action=='date' and isinstance(w,W.QDateEdit): w.setDate(C.QDate.fromString(v,'yyyy-MM-dd'))
                elif action=='select' and isinstance(w,W.QComboBox): w.setCurrentIndex(int(v))
                elif action=='edit-select' and isinstance(w,W.QComboBox) and w.isEditable(): w.setEditText(str(v))
                elif action=='tab' and isinstance(w,W.QTabWidget): w.setCurrentIndex(int(v))
                elif action=='check' and isinstance(w,W.QAbstractButton): w.click() if w.isChecked()!=bool(v) else None
                elif action=='list' and isinstance(w,W.QListWidget): w.setCurrentRow(int(v))
                elif action=='list-check' and isinstance(w,W.QListWidget): w.item(a['row']).setCheckState(C.Qt.Checked if v else C.Qt.Unchecked)
                elif action=='table' and isinstance(w,W.QTableWidget):
                    r,c=int(a['row']),int(a.get('column',0));w.setCurrentCell(r,c)
                    if a.get('double'): w.cellDoubleClicked.emit(r,c)
                    else: w.cellClicked.emit(r,c)
                elif action=='sort' and isinstance(w,W.QTableWidget) and w.isSortingEnabled():
                    w.sortItems(int(a['column']),C.Qt.DescendingOrder if v else C.Qt.AscendingOrder)
                elif action=='tree' and isinstance(w,W.QTreeWidget):
                    route=a['route']; item=w.topLevelItem(route[0])
                    for index in route[1:]: item=item.child(index)
                    w.setCurrentItem(item);w.itemClicked.emit(item,0)
                elif action=='page': self.pages[str(a['id'])]=max(0,int(v))
                elif action=='reject' and isinstance(w,W.QDialog): w.reject()
                else: raise ValueError('Unsupported control action')
                self.save_inputs();result=True
                if action in ('input','edit-select','select','date','check'):
                    self.workflow.action(self.window.centralWidget().currentIndex(),str(a['id']),action,time.monotonic())
            else: raise ValueError('Unknown GUI method')
            self.emit({'id':request.get('id'),'result':result})
            self.publish()
        except Exception as exc:
            self.workflow.history.record('bridge_error',error=self.workflow.history.clean(exc))
            self.emit({'id':request.get('id'),'error':str(exc)})


def run():
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    output=sys.stdout;sys.stdout=sys.stderr
    lock=threading.Lock()
    def emit(value):
        with lock: output.write(json.dumps(value,ensure_ascii=False)+'\n');output.flush()
    def report_exception(kind,value,tb):
        traceback.print_exception(kind,value,tb,file=sys.stderr)
        emit({'event':'error','error':str(value)})
    sys.excepthook=report_exception
    app=W.QApplication([]);app.setQuitOnLastWindowClosed(False)
    controller=Controller(app,emit)
    def reader():
        for line in sys.stdin:
            try: controller.incoming.emit(json.loads(line))
            except Exception as exc: emit({'error':str(exc)})
        C.QMetaObject.invokeMethod(app,'quit',C.Qt.QueuedConnection)
    threading.Thread(target=reader,daemon=True).start()
    controller.publish();app.exec()
