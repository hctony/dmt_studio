"""Connect local workflow history to original Qt inputs without running analyses."""
import time
from PySide6 import QtWidgets as W
from workflow_assistant import WorkflowHistory


class WorkflowBridge:
    def __init__(self, controller, root):
        self.c = controller
        self.history = WorkflowHistory(root)
        self.was_busy = False

    def fields(self, tab=None):
        return {key: w.text() for key, w in self.c.input_widgets()
                if isinstance(w, W.QLineEdit) and (tab is None or key.startswith(str(tab) + '/'))}

    def context_fields(self, tab):
        fields = self.fields(tab)
        active = self.history.active
        if not active: return {}
        for name, expected in [('model_in', active['model']), ('lot_in', active['lot'])]:
            values = [v for k, v in fields.items() if k.endswith('/' + name) and v]
            if any(v != expected for v in values): return {}
        return fields

    def result_summary(self, tab):
        root = self.c.window.centralWidget().widget(tab)
        tables = [dict(rows=w.rowCount(), columns=w.columnCount()) for w in root.findChildren(W.QTableWidget)]
        logs = [self.history.clean(w.toPlainText()[-4000:]) for w in root.findChildren(W.QTextEdit) if w.isReadOnly()]
        return dict(tables=tables, logs=logs[:4])

    def action(self, tab, control, action, started, error=None):
        self.history.record('action_error' if error else 'action', self.context_fields(tab), tab=tab,
                            control=control, action=action, elapsedMs=round((time.monotonic()-started)*1000),
                            error=self.history.clean(error) if error else None,
                            result=self.result_summary(tab) if action == 'click' else None)

    def poll(self):
        busy = self.c.busy()
        if self.was_busy and not busy and self.history.active:
            # Async worker completion is observed, not assumed to be successful.
            self.history.record('workers_idle', result=[self.result_summary(i) for i in range(10)])
        self.was_busy = busy

    def recommendations(self, model, lot):
        fields = self.fields()
        tabs = self.c.window.centralWidget()
        result = self.history.recommend(model, lot)
        for item in result:
            item['current'] = fields.get(item['key'], '')
            item['tab'] = tabs.tabText(int(item['key'].split('/')[0]))
            item['field'] = item['key'].rsplit('/', 1)[-1]
            item['mode'] = item['key'].split('/')[1].split(':', 1)[-1]
        return result

    def apply(self, model, lot, keys, overwrite=False, automatic=False):
        widgets = dict(self.c.input_widgets())
        applied, skipped = [], []
        for item in self.recommendations(model, lot):
            key = item['key']
            if key not in keys: continue
            w = widgets.get(key)
            if not isinstance(w, W.QLineEdit) or w.isReadOnly() or not w.isEnabled() or (w.text() and not overwrite) or (automatic and item['confidence'] != 'high'):
                skipped.append(key); continue
            # Do not set a path into a screen still targeting another model/LOT.
            tab_fields = self.fields(int(key.split('/')[0]))
            if any(v and v != expected for name, expected in [('model_in', model), ('lot_in', lot)]
                   for k, v in tab_fields.items() if k.endswith('/' + name)):
                skipped.append(key); continue
            w.setText(item['value'])
            # No button click or processing callback: data only, never execute LM.
            applied.append(key)
        self.c.save_inputs()
        self.history.record('recommendations_applied',
                            model=model, lot=lot, applied=applied, skipped=skipped, automatic=automatic)
        return dict(applied=applied, skipped=skipped)

    def handle(self, method, args):
        if method == 'status': return self.history.status()
        if method == 'export': return self.history.export()
        if method == 'recommend': return self.recommendations(str(args['model']).strip(), str(args['lot']).strip())
        if self.c.busy() or self.c.action_depth:
            raise ValueError('작업 완료 후 사용하세요. / 請先完成工作。')
        if method == 'preferences':
            self.history.set_preferences(args.get('autofill', False)); return self.history.status()
        if method == 'start':
            active = self.history.start(args['model'], args['lot'], args.get('version', 'unknown'))
            # The explicit session model/LOT is applied to the current screen only.
            tab = self.c.window.centralWidget().currentIndex()
            for key, w in self.c.input_widgets():
                name = key.rsplit('/', 1)[-1]
                if key.startswith(str(tab) + '/') and name in ('model_in', 'lot_in') and isinstance(w, W.QLineEdit):
                    w.setText(active['model'] if name == 'model_in' else active['lot'])
                    if name == 'model_in': w.editingFinished.emit()
            self.c.save_inputs()
            if self.history.preferences['autofill']:
                items = self.recommendations(active['model'], active['lot'])
                self.apply(active['model'], active['lot'], [r['key'] for r in items], automatic=True)
            return self.history.status()
        if method == 'finish': return self.history.finish(args['outcome'], args.get('note', ''))
        if method == 'apply':
            return self.apply(str(args['model']).strip(), str(args['lot']).strip(), args['keys'], bool(args.get('overwrite')))
        raise ValueError('Unknown workflow method')
