import os
import sys
import json
import tempfile
import unittest
import time
from unittest.mock import patch
from pathlib import Path

os.environ['QT_QPA_PLATFORM']='offscreen'
_settings=tempfile.TemporaryDirectory()
os.environ['DMT_DATA_DIR']=_settings.name
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PySide6 import QtWidgets as W, QtCore as C
C.QSettings.setDefaultFormat(C.QSettings.IniFormat)
C.QSettings.setPath(C.QSettings.IniFormat,C.QSettings.UserScope,_settings.name)
from gui_bridge import Controller


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=W.QApplication.instance() or W.QApplication([])
    def setUp(self):
        self.events=[];self.c=Controller(self.app,self.events.append)
        self.answers={};self.dialogs=[]
        def ask(kind,data):
            self.dialogs.append((kind,data))
            answer=self.answers.get(kind)
            if callable(answer):return answer(data)
            return answer if answer is not None else W.QMessageBox.Ok.value if kind=='message' else ''
        self.c.ask=ask
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self): self.c.timer.stop();self.c.window.close();self.c.window.deleteLater();self.app.processEvents();self.temp.cleanup()
    def tab(self,i):
        tabs=self.c.window.centralWidget();tabs.setCurrentIndex(i);self.app.processEvents();return tabs.widget(i)
    def action(self,w,kind,value=None,**extra):
        self.c.handle({'id':99,'method':'gui.action','args':dict(id=self.c.register(w),action=kind,value=value,**extra)})
        errors=[e for e in self.events if e.get('id')==99 and 'error' in e]
        self.assertFalse(errors,errors)
    def fixture(self):
        txt=self.root/'gather.txt';txt.write_text('1,RX1234-,RX1234-01\nRX1234-,RX1234-01,1,1,T,SM03\nRX1234-,RX1234-01,EOS\n',encoding='utf-8')
        ids=self.root/'strip.json';ids.write_text(json.dumps({'lot_id':'RX1234-','strip_ids':['RX1234-01']}))
        return txt,ids
    def test_all_eleven_tabs_serialize_original_controls(self):
        def walk(w):
            if not w:return []
            return [w]+sum([walk(c) for c in w.get('children',[])],[])+walk(w.get('content'))
        total=0
        for i in range(12):
            self.tab(i);s=self.c.snapshot();nodes=walk(s['root']['content'])
            self.assertTrue(any(w['type']=='button' for w in nodes),i)
            total+=sum(w['type']=='button' for w in nodes)
            self.assertEqual(s['root']['index'],i)
        self.assertGreater(total,45)
    def test_operator_history_tab_search_selection_and_original_download(self):
        import hashlib
        from core.inspection_history import record_file
        with patch.dict(os.environ,{'DMT_CACHE_DIR':str(self.root/'cache')}):
            txt,ids=self.fixture();body=txt.read_bytes()
            record_file(dict(object=str(txt),path=str(self.root/'M_RX1234-_admin_2026-11-18 132845.txt'),
                sha256=hashlib.sha256(body).hexdigest(),size=len(body),mtime_ns=txt.stat().st_mtime_ns),'RX1234-','VRS')
            tab=self.tab(10);self.action(tab.strip_in,'input','RX1234-01')
            self.action(tab.btn_search,'click')
            self.assertEqual(tab.table.rowCount(),1);self.assertIn('SM03',tab.detail.toPlainText())
            self.assertEqual(tab.table.item(0,3).text(),'admin')
            self.assertTrue(self.c.widget(tab.table)['literalCells'])
            self.assertTrue(self.c.widget(tab.detail)['literal'])
            output=self.root/'download.txt';self.answers['save']=str(output)
            self.action(tab.btn_export,'click');self.assertEqual(output.read_bytes(),body)

    def test_yield_original_calculation_and_excel_export(self):
        txt,ids=self.fixture();tab=self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','skipdata_in','ski_in'):getattr(tab,field).clear()
        tab.txt_in.setText(str(txt));tab.strip_json_in.setText(str(ids));tab.units_per_strip_in.setText('4');tab.xout_limit_in.setText('3')
        self.action(tab.btn_calc,'click')
        self.assertEqual(tab.summary['total_units'],4)
        self.assertEqual(tab.summary['good_units'],3)
        target=self.root/'report.xlsx';self.answers['save']=str(target);self.answers['file']=''
        self.action(tab.btn_export_excel,'click')
        self.assertTrue(target.is_file(),self.dialogs)
        from openpyxl import load_workbook
        self.assertTrue(load_workbook(target).sheetnames)

    def test_9999_has_no_unresolved_popup_and_can_export_excel(self):
        from openpyxl import load_workbook
        txt,ids=self.fixture();txt.write_text(txt.read_text().replace('SM03','9999'))
        tab=self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','txt_in','strip_json_in','skipdata_in','ski_in'):getattr(tab,field).clear()
        tab.vrs_txt_in.setText(str(txt));tab.strip_json_in.setText(str(ids));tab.units_per_strip_in.setText('4');tab.xout_limit_in.setText('3')
        self.action(tab.btn_calc,'click')
        self.assertFalse(any(d.get('title')=='미분류 불량 코드' for k,d in self.dialogs if k=='message'))
        target=self.root/'9999.xlsx';self.answers['save']=str(target);self.action(tab.btn_export_excel,'click')
        self.assertTrue(target.exists(),self.dialogs)
        book=load_workbook(target);sheet=book.active
        self.assertEqual(sheet['H3'].value,1)
        self.assertIn(('9999',1),[(sheet.cell(1,c).value,sheet.cell(3,c).value) for c in range(10,sheet.max_column+1)]);book.close()
    def test_ivs_json_loading_and_list_checkbox(self):
        from core.models import DefectRecord
        from core.ivs import records_to_json
        record=DefectRecord(strip_id='RX1234-01',machine='AFVI1',model='M',lot='RX1234-',trial='T',strip_folder='1',container='Unit',face='T',unit_idx='1',img_merge_idx='0',ivs_1='SM03',code_main='SM03',code_sub='',def_side='F',strip_pt_x=1,strip_pt_y=1,img_pt_x=0,img_pt_y=0,source_xml='',images=[])
        file=self.root/'ivs.json';file.write_text(json.dumps(records_to_json('M','RX1234-',[record])))
        self.answers['file']=str(file);tab=self.tab(0)
        self.action(tab.btn_load,'click');self.assertEqual(len(tab.records),1)
        self.action(tab.download_group_list,'list-check',False,row=0)
        self.assertEqual(tab.download_group_list.item(0).checkState(),C.Qt.Unchecked)
    def test_lm_preview_cancel_then_execute_with_backup(self):
        tab=self.tab(8);smk=self.root/'smk';pmk=self.root/'pmk';smk.mkdir();pmk.mkdir()
        (smk/'S1.smk').write_text('sample');(pmk/'x_Master.pmk').write_text('S1\nS2\n')
        data=self.root/'validation.json';data.write_text(json.dumps({'lot_id':'RX1234-','strip_ids':{'ski_only_strips':['S1']}}))
        tab.json_in.setText(str(data));tab.smk_folder_in.setText(str(smk));tab.pmk_folder_in.setText(str(pmk))
        self.action(tab.btn_preview,'click');self.assertEqual(tab.last_preview['smk_move_count'],1)
        self.answers['message']=W.QMessageBox.No.value;self.action(tab.btn_execute,'click');self.assertTrue((smk/'S1.smk').exists())
        self.answers['message']=W.QMessageBox.Yes.value;self.action(tab.btn_execute,'click')
        self.assertTrue((smk/'BK'/'S1.smk').exists());self.assertEqual((pmk/'x_Master.pmk').read_text(),'S2\n');self.assertTrue(list((pmk/'BK').glob('*.bak')))
    def test_duplicate_dialog_can_still_render_in_electron(self):
        from ui.tabs.gathering_tab import GatherDuplicateDialog
        duplicate=GatherDuplicateDialog({'strip_id':'S1'},1,1,self.c.window);duplicate.show();self.app.processEvents()
        self.action(duplicate.radio_merge,'check',True);duplicate.accept();self.assertEqual(duplicate.choice,'merge')
    def test_table_pagination_and_input_persistence(self):
        table=W.QTableWidget(450,1,self.c.window);table.show()
        for i in range(450):table.setItem(i,0,W.QTableWidgetItem(str(i)))
        self.action(table,'page',400);s=self.c.widget(table);self.assertEqual(s['rows'][0],['400']);self.assertEqual(s['total'],450)
        table.setSortingEnabled(True)
        self.action(table,'sort',True,column=0);self.action(table,'page',0)
        self.assertEqual(self.c.widget(table)['rows'][0],['99'])
        self.assertTrue(self.c.widget(table)['sortable'])
        tab=self.tab(1);self.action(tab.model_in,'input','TEST-MODEL')
        self.assertIn('TEST-MODEL',self.c.input_path.read_text(encoding='utf-8'))
    def test_original_test_registry(self):
        from ui.tabs.test_tab import TESTS,_FUNC_MAP
        for _,label,name in TESTS:
            with self.subTest(label=label):_FUNC_MAP[name]()
    def test_sixty_ski_matching_duplicates_use_one_review_dialog(self):
        from core.models import GatherStrip
        from core.gather import build_gather_duplicate_conflict
        from core.duplicate_review import build_review_plan
        from ui.tabs.gathering_tab import GatherReviewDialog
        conflicts=[]
        for i in range(60):
            key=f'S{i}'
            strip=GatherStrip('L',key,[f'L,{key},1,1,T,OT12'])
            conflicts.append(build_gather_duplicate_conflict(strip,strip,{}, {key:{(1,1)}}))
        dialog=GatherReviewDialog(build_review_plan(conflicts),self.c.window)
        dialog.show();self.app.processEvents()
        self.assertEqual(dialog.strip_list.count(),60)
        self.assertTrue(all(c==0 for c in dialog.choices))
        self.action(dialog.strip_list,'list',37)
        self.assertIn('S37',dialog.details.toPlainText())
        self.assertEqual(len(self.c.snapshot()['dialogs']),1)
        dialog.accept()
        self.assertEqual(dialog.result(),W.QDialog.Accepted)
        from ui.tabs.gathering_tab import GatheringModePage
        page=next(w for w in self.tab(3).findChildren(GatheringModePage) if w.mode=='VRS')
        strips=[GatherStrip('L',f'S{i}',[f'L,S{i},1,1,T,OT12']) for i in range(60)]
        with patch.object(GatherReviewDialog,'exec',side_effect=AssertionError('Exact matches must not open review')):
            final,output,summary=page.apply_duplicate_choices(strips,'',{'duplicate_conflicts':conflicts})
        self.assertEqual(summary['auto_duplicate_selection_count'],60)
        self.assertEqual(len(final),60)

    def test_bulk_duplicate_selection_only_applies_checked_rows(self):
        from core.models import GatherStrip
        from core.gather import build_gather_duplicate_conflict
        from core.duplicate_review import build_review_plan
        from ui.tabs.gathering_tab import GatherReviewDialog
        conflicts=[]
        for key in ('A','B'):
            strip=GatherStrip('L',key,[f'L,{key},1,1,T,OT12'])
            other=GatherStrip('L',key,[f'L,{key},2,1,T,OT12'])
            conflicts.append(build_gather_duplicate_conflict(strip,other,{}, {key:{(3,1)}}))
        dialog=GatherReviewDialog(build_review_plan(conflicts),self.c.window)
        dialog.set_checked(False)
        dialog.strip_list.item(0).setCheckState(C.Qt.Checked)
        dialog.apply_checked(True)
        self.assertEqual(dialog.choices,['auto',None])
        dialog.apply_checked(False)
        self.assertEqual(dialog.choices,['auto',None])
        dialog.set_checked(True)
        dialog.apply_checked(True)
        self.assertEqual(dialog.choices,['auto','auto'])
        dialog.reject()
    def test_image_list_dedup_preserves_summary_and_different_directories(self):
        from core.models import DefectRecord
        from core.ivs import build_summary
        from dataclasses import replace
        tab=self.tab(0)
        record=DefectRecord('S','A','M','L','T','1','Unit','T','1','0','SM03','SM03','','F',1,1,0,0,'xml',['/one/p1.bmp'])
        tab.records=[record,replace(record,source_xml='other.xml'),replace(record,unit_idx='2',images=['/two/p1.bmp'])]
        before=build_summary(tab.records)
        tab.refresh_view()
        self.assertEqual(tab.defect_list.count(),2)
        self.assertEqual([len(group) for group in tab.image_record_groups],[2,1])
        self.assertEqual(build_summary(tab.records),before)
        with patch('ui.tabs.ivs_tab.ImageLoaderThread') as loader:
            loader.return_value.isRunning.return_value=False
            for direction,expected in [(1,0),(1,1),(1,1),(-1,0),(-1,0)]:
                self.c.handle({'id':71,'method':'gui.image-step','args':{'direction':direction}})
                self.assertEqual(tab.defect_list.currentRow(),expected)
            self.assertEqual(json.loads(tab.info.toPlainText())['image_record_count'],2)

    def test_image_samples_more_dialog_and_download_range_keep_full_summary(self):
        from core.models import DefectRecord
        from core.ivs import build_summary
        from dataclasses import replace
        tab=self.tab(0)
        base=DefectRecord('S','A','M','L','T','1','Unit','FC','0','0','SM03','SM03','','F',1,1,0,0,'xml',[])
        tab.records=[replace(base,unit_idx=str(i)) for i in range(35)]
        before=build_summary(tab.records);tab.refresh_view()
        self.assertEqual(tab.defect_list.count(),10);self.assertEqual(tab.summary_table.item(0,1).text(),'35')
        self.action(tab.btn_more_images,'click');dlg=tab.image_range_dialog
        self.assertEqual(dlg.total.text(),'35');self.assertEqual(dlg.start.text(),'11')
        self.action(dlg.start,'input','11');self.action(dlg.end,'input','20')
        button=next(b for b in dlg.findChildren(W.QPushButton) if b.text()=='추가로 보기');self.action(button,'click')
        self.assertEqual(tab.defect_list.count(),20);self.assertEqual(build_summary(tab.records),before)
        captured=[]
        class Download(C.QObject):
            progress=C.Signal(str);finished=C.Signal(int);failed=C.Signal(str)
            def __init__(self,records,folder,code):super().__init__();captured.append((records,folder,code))
            def start(self):pass
            def isRunning(self):return False
        self.answers['directory']=str(self.root)
        with patch('ui.tabs.ivs_tab.DownloadThread',Download):
            self.action(tab.btn_download,'click');dlg=tab.download_range_dialog
            self.action(dlg.start,'input','26');self.action(dlg.end,'input','30')
            button=next(b for b in dlg.findChildren(W.QPushButton) if b.text()=='다운로드');self.action(button,'click')
        self.assertEqual([r.unit_idx for r in captured[0][0]],['25','26','27','28','29'])
        self.assertEqual(captured[0][2],'SM03');self.assertEqual(build_summary(tab.records),before)
    def test_gathering_lot_input_auto_and_lookup_all_resolve_legacy_paths(self):
        from ui.common_widgets import finish_line_edit_lot_lookup
        tab=self.tab(3)
        with patch('core.utils.auto_find_bbt_skipdata_path',return_value='D:/test/BBT') as bbt, patch('core.utils.auto_find_ski_file_path',return_value='D:/test/final.ski') as ski:
            self.action(tab.lot_in,'input','LOT-MANUAL')
            bbt.assert_called_with('LOT-MANUAL');ski.assert_called_with('LOT-MANUAL')
            with patch('ui.tabs.gathering_tab.infer_lot_id_from_values',return_value='LOT-AUTO'):
                tab.auto_lot()
            bbt.assert_called_with('LOT-AUTO');ski.assert_called_with('LOT-AUTO')
            with patch('ui.common_widgets.QInputDialog.getItem',return_value=('LOT-SEARCH',True)):
                finish_line_edit_lot_lookup(tab,'MODEL',tab.lot_in,'gathering/lot',[],None,['LOT-SEARCH'])
            bbt.assert_called_with('LOT-SEARCH');ski.assert_called_with('LOT-SEARCH')
            self.assertEqual(tab.skipdata_in.text(),'D:/test/BBT')
            self.assertEqual(tab.ski_in.text(),'D:/test/final.ski')
    def test_defect_summary_opens_full_resolution_image_and_clears_old_images(self):
        from core.models import DefectRecord
        from PySide6.QtGui import QPixmap, QImage
        import base64
        tab=self.tab(0)
        record=DefectRecord('S','A','M','L','T','1','Unit','T','1','0','SM03','SM03','','F',1,1,0,0,'xml',['a.png'])
        tab.records=[record];tab.refresh_view()
        pix=QPixmap(1200,600);pix.fill(C.Qt.red);data=C.QByteArray();buffer=C.QBuffer(data);buffer.open(C.QIODevice.WriteOnly);pix.save(buffer,'PNG')
        class Loader(C.QObject):
            finished=C.Signal(list,list);failed=C.Signal(str)
            def __init__(self,paths):super().__init__();self.paths=paths
            def isRunning(self):return False
            def start(self):self.finished.emit(self.paths,[bytes(data)])
        with patch('ui.tabs.ivs_tab.ImageLoaderThread',Loader):
            self.action(tab.summary_table,'table',row=0,column=1)
            image=self.c.widget(tab.image_labels[0])
            self.assertEqual(image['type'],'image')
            decoded=QImage.fromData(base64.b64decode(image['src'].split(',',1)[1]))
            self.assertEqual(decoded.width(),1200)
            record.images=[];tab.on_record_selected()
            self.assertEqual(self.c.widget(tab.image_labels[0])['type'],'label')
            self.assertIn('경로 없음',tab.image_labels[0].text())

    def test_old_image_response_cannot_replace_new_selection(self):
        from core.models import DefectRecord
        from dataclasses import replace
        tab=self.tab(0);record=DefectRecord('S','A','M','L','T','1','Unit','T','1','0','SM03','SM03','','F',1,1,0,0,'xml',['a.png'])
        tab.records=[record,replace(record,strip_id='OTHER',images=[])];tab.refresh_view()
        class Loader(C.QObject):
            finished=C.Signal(list,list);failed=C.Signal(str)
            def __init__(self,paths):super().__init__()
            def isRunning(self):return True
            def start(self):pass
        with patch('ui.tabs.ivs_tab.ImageLoaderThread',Loader):
            tab.defect_list.setCurrentRow(0);old=tab.loader_thread
            tab.defect_list.setCurrentRow(1);old.failed.emit('old failure')
            old.finished.emit(['old'],[b'invalid'])
            self.assertIn('경로 없음',tab.image_labels[0].text())
    def test_running_progress_is_exposed_and_removed_when_done(self):
        worker=C.QThread();self.c.window.progress_test_worker=worker
        worker.dmt_progress={'stage':'현재 Trial 진행률','value':2,'max':4,'detail':'Trial 1/2 · XML 3'}
        with patch.object(worker,'isRunning',return_value=True):
            task=self.c.snapshot()['tasks'][0]
            self.assertEqual((task['value'],task['max']),(2,4))
            self.assertIn('elapsed',task)
        self.assertEqual(self.c.snapshot()['tasks'],[])
        del self.c.window.progress_test_worker

    def test_ivs_progress_counts_completed_folders_not_started_files(self):
        from workers.ivs_workers import IvsScanThread
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        worker=IvsScanThread('M','L');observed=[];errors=[]
        worker.failed.connect(errors.append)
        context=SimpleNamespace(path='/trial',machine='A',trial='T')
        def parse(*args):observed.append(dict(worker.dmt_progress));return []
        with patch('workers.ivs_workers.connect',return_value=(MagicMock(),MagicMock())), patch('workers.ivs_workers.list_lot_trials',return_value=[context]), patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1,2}), patch('workers.ivs_workers.iter_dirs',return_value=['1','2']), patch('workers.ivs_workers.find_ivs_files',return_value=['file.xml']), patch('workers.ivs_workers.parse_ivs_xml',side_effect=parse):
            worker.run()
        self.assertEqual(errors,[])
        self.assertEqual([p['value'] for p in observed],[0,1])
        self.assertEqual(worker.dmt_progress['value'],2)
        self.assertEqual(worker.dmt_progress['max'],2)

    def test_old_ivs_json_latest_zero_clears_ng_and_saves_history(self):
        from core.models import DefectRecord
        from core.ivs import records_to_json
        record=DefectRecord('RX-01','A','M','RX-','99','1','Unit','T','1','0','SM03','SM03','','F',1,1,0,0,'old.xml',[])
        payload=records_to_json('M','RX-',[record],{'strip_ids':['RX-01'],'strip_count':1,'units_per_strip':8,'complete':True,'total_units':8,'scope':'lot'})
        payload['strip_history']={'schema':'dmt-its-history-v1','complete':True,'events':[
            {'strip_id':'RX-01','machine':'A','trial':'99','strip_folder':'1','source_xml':'old.xml','source_mtime':100},
            {'strip_id':'RX-01','machine':'C','trial':'1','strip_folder':'2','source_xml':'zero.xml','source_mtime':200}]}
        source=self.root/'old.json';source.write_text(json.dumps(payload));self.answers['file']=str(source)
        tab=self.tab(0);self.action(tab.btn_load,'click');self.assertEqual(tab.records,[])
        self.assertIn('Barcode별 최신 작업 반영',tab.summary_label.text())
        target=self.root/'new.json';self.answers['save']=str(target);self.action(tab.btn_save,'click')
        self.assertTrue(target.exists(),self.dialogs)
        saved=json.loads(target.read_text(encoding='utf-8'));self.assertEqual(saved['records'],[]);self.assertEqual(len(saved['history_records']),1)
        self.assertEqual(saved['latest_selection']['selected']['RX-01']['machine'],'C')
    def test_model_import_merges_backs_up_and_restores_after_restart(self):
        import config
        path=self.root/'yield_model_settings.json'
        path.write_text(json.dumps({'KEEP':{'xout_limit':'2'},'MODEL':{'units_per_strip':'88','ski_path':'old.ski'}}))
        self.c.input_path=self.root/'gui-inputs.json'
        tab=self.tab(6);tab.model_in.setText('MODEL');tab.xout_limit_in.setText('13')
        tab.vrs_txt_in.setText('previous-lot.txt');self.c.save_inputs()
        old_inputs=self.c.input_path.read_bytes()
        with patch.object(config,'YIELD_MODEL_SETTINGS_PATH',path):
            result=self.c.model_profiles.import_data({'MODEL':{'xout_limit':18,'x_count':11,'y_count':8,'vrs_txt_paths':r'Z:\Taiwan\LOT01.txt','ivs_json_paths':''}})
            stored=json.loads(path.read_text())
            self.assertIn('KEEP',stored);self.assertEqual(stored['MODEL']['ski_path'],'old.ski')
            self.assertEqual(tab.xout_limit_in.text(),'18');self.assertEqual(tab.vrs_txt_in.text(),r'Z:\Taiwan\LOT01.txt')
            self.assertEqual((Path(result['backup'])/'gui-inputs.json').read_bytes(),old_inputs)
            self.assertEqual(json.loads((Path(result['backup'])/'yield_model_settings.json').read_text())['MODEL']['ski_path'],'old.ski')
            tab.xout_limit_in.setText('0');tab.vrs_txt_in.clear();self.c.restore_inputs()
            self.assertEqual(tab.xout_limit_in.text(),'18');self.assertEqual(tab.vrs_txt_in.text(),r'Z:\Taiwan\LOT01.txt')
            gather=self.tab(3);gather.allowance_in.setText('5');gather.x_count_in.setText('22');gather.y_count_in.setText('4')
            self.c.model_profiles.apply('MODEL');self.assertEqual(gather.allowance_in.text(),'5')
            self.c.model_profiles.apply('MODEL',True);self.assertEqual(gather.allowance_in.text(),'18')
            self.assertEqual((gather.x_count_in.text(),gather.y_count_in.text()),('11','8'))

    def test_invalid_or_busy_model_import_leaves_files_unchanged(self):
        import config
        path=self.root/'yield_model_settings.json';path.write_text('{"KEEP":{"xout_limit":"2"}}')
        before=path.read_bytes()
        with patch.object(config,'YIELD_MODEL_SETTINGS_PATH',path):
            for payload in ({},[],{'BAD':{'xout_limit':-1}},{'BAD':{'xout_limit':True}},{'BAD':{'vrs_txt_paths':[]}}, {'GOOD':{'xout_limit':'3'},'BAD':{'units_per_strip':'0'}}):
                with self.assertRaises(ValueError):self.c.model_profiles.import_data(payload)
                self.assertEqual(path.read_bytes(),before)
            self.c.action_depth=1
            with self.assertRaises(ValueError):self.c.model_profiles.import_data({'GOOD':{'xout_limit':'3'}})
            self.c.action_depth=0
            self.assertEqual(path.read_bytes(),before)

    def test_model_switch_respects_explicit_blank_instead_of_stale_cache(self):
        tab=self.tab(6);tab.model_in.setText('BLANK');tab.vrs_txt_in.setText('previous-lot.txt')
        with patch('ui.tabs.yield_tab.load_yield_model_settings',return_value={'BLANK':{'vrs_txt_paths':'','xout_limit':'0'}}), patch('ui.tabs.yield_tab.setting_text',return_value='stale-cache'):
            tab.restore_model_units()
        self.assertEqual(tab.vrs_txt_in.text(),'');self.assertEqual(tab.xout_limit_in.text(),'0')
    def test_workflow_recommendation_apply_preserves_existing_and_does_not_execute(self):
        from workflow_assistant import WorkflowHistory
        self.c.workflow.history=WorkflowHistory(self.root)
        tab=self.tab(6);tab.model_in.setText('M1')
        key=next(k for k,w in self.c.input_widgets() if w is tab.vrs_txt_in)
        history=self.c.workflow.history
        for lot in ['AA01-','AA02-','AA03-']:
            history.start('M1',lot,'test');history.record('action',{key:f'D:\\data\\{lot}.txt'});history.finish('success')
        tab.vrs_txt_in.setText('manual.txt')
        result=self.c.workflow.handle('apply',dict(model='M1',lot='AA04-',keys=[key]))
        self.assertEqual(result['applied'],[]);self.assertEqual(tab.vrs_txt_in.text(),'manual.txt')
        with patch.object(tab,'calculate') as calculate:
            result=self.c.workflow.handle('apply',dict(model='M1',lot='AA04-',keys=[key],overwrite=True))
            self.assertEqual(tab.vrs_txt_in.text(),'D:\\data\\AA04-.txt');calculate.assert_not_called()
        tab.model_in.setText('OTHER');tab.vrs_txt_in.clear()
        self.assertEqual(self.c.workflow.handle('apply',dict(model='M1',lot='AA04-',keys=[key]))['applied'],[])
    def test_workflow_records_action_and_blocks_finish_during_work(self):
        from workflow_assistant import WorkflowHistory
        self.c.workflow.history=WorkflowHistory(self.root)
        tab=self.tab(8)
        self.c.workflow.handle('start',dict(model='M1',lot='AA01-',version='test'))
        self.action(tab.btn_preview,'click')
        self.assertTrue(any(e['event']=='action' for e in self.c.workflow.history.active['events']))
        self.c.action_depth=1
        with self.assertRaises(ValueError):self.c.workflow.handle('finish',dict(outcome='success'))
        self.c.action_depth=0
        self.c.workflow.handle('finish',dict(outcome='failed',note='validation expected'))
        self.assertEqual(self.c.workflow.history.recommend('M1','AA04-'),[])
    def test_nested_dialog_protocol(self):
        # Restore the real event-loop handshake, not the fixture's canned response.
        self.c.ask=Controller.ask.__get__(self.c)
        C.QTimer.singleShot(20,lambda:self.c.handle({'id':100,'method':'dialog.reply','args':{'id':1,'value':W.QMessageBox.Yes.value}}))
        value=self.c.ask('message',{'title':'Confirm','text':'Test','buttons':[]})
        self.assertEqual(value,W.QMessageBox.Yes.value)

    def test_gather_uses_latest_complete_txt_per_process_without_date_dialog(self):
        from workers.gather_workers import GatheringWorker
        from core import rework_scope
        sources = {}
        for mode, replacement in [('IVS', ''), ('VRS', 'RX1234-,RX1234-01,2,1,T,OT12\n')]:
            folder = self.root / mode
            folder.mkdir()
            old = folder / 'M_RX1234-_op_2026-01-01 120000.txt'
            new = folder / 'M_RX1234-_op_2026-01-03 120000.txt'
            old.write_text('1,RX1234-,RX1234-01\nRX1234-,RX1234-01,1,1,T,SM03\nRX1234-,RX1234-01,EOS\n'
                           '1,RX1234-,RX1234-02\nRX1234-,RX1234-02,1,2,T,SM03\nRX1234-,RX1234-02,EOS\n')
            new.write_text('1,RX1234-,RX1234-01\nRX1234-,RX1234-01,1,1,T,DR01\nRX1234-,RX1234-01,EOS\n'
                           '1,RX1234-,RX1234-01\n' + replacement + 'RX1234-,RX1234-01,EOS\n')
            os.utime(old, (time.time(), time.time()))
            os.utime(new, (time.time()-60, time.time()-60))
            sources[mode]=folder

        def gather(mode):
            worker = GatheringWorker(mode,'1','unused','M','RX1234-',2,2,10,'170',set(),set())
            worker.build_paths = lambda: [sources[mode]]
            result, errors = [], []
            worker.finished.connect(lambda strips, output, summary: result.append((strips, summary)))
            worker.failed.connect(errors.append)
            worker.run()
            self.assertFalse(errors,errors)
            strips,summary = result[0]
            return {strip.strip_id: strip.defect_lines for strip in strips},summary

        with patch.dict(os.environ, {'DMT_CACHE_DIR': str(self.root/'cache')}), patch('core.collection.enabled', return_value=False):
            ivs, summary = gather('IVS')
            vrs, _ = gather('VRS')
            self.assertEqual(ivs['RX1234-01'], [])
            self.assertEqual(vrs['RX1234-01'], ['RX1234-,RX1234-01,2,1,T,OT12'])
            self.assertIn('RX1234-02', ivs)
            self.assertEqual(summary['auto_duplicate_selection_count'],2)
            rework_scope.save('M','RX1234-',True,'2026-01-02',False)
            ivs, _ = gather('IVS')
            self.assertEqual(ivs, {'RX1234-01': []})

    def test_three_process_batch_export(self):
        from workers.gather_workers import GatheringWorker
        txt,_=self.fixture();inputs=self.root/'inputs';inputs.mkdir();(inputs/'data.txt').write_bytes(txt.read_bytes())
        output=self.root/'output';output.mkdir();tab=self.tab(3)
        tab.lot_in.setText('RX1234-');tab.exclude_json_in.clear();tab.skipdata_in.clear();tab.ski_in.clear();tab.x_count_in.setText('2');tab.y_count_in.setText('2');tab.allowance_in.setText('3')
        self.answers['directory']=str(output)
        with patch('core.yield_handoff.DATA_DIR', self.root), patch.object(GatheringWorker,'build_paths',return_value=[inputs]):
            self.action(tab.btn_batch_export,'click')
            deadline=time.monotonic()+8
            while not tab.btn_batch_export.isEnabled() and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
            self.assertTrue(tab.btn_batch_export.isEnabled())
            yield_tab = self.tab(6)
            for mode in ('VRS','FVI','IVS'):
                self.assertEqual(Path(getattr(yield_tab,mode.lower()+'_txt_in').text()).read_text(),(output/f'Gather_{mode}_RX1234-.txt').read_text())
            self.assertEqual(yield_tab.units_per_strip_in.text(),'4')
            self.assertEqual(yield_tab.xout_limit_in.text(),'3')
            self.assertEqual(yield_tab.strip_json_in.text(),'')
        for mode in ('VRS','FVI','IVS'):self.assertTrue((output/f'Gather_{mode}_RX1234-.txt').is_file())

    def test_batch_saves_in_matched_model_lot_folder_and_keeps_previous_export(self):
        from workers.gather_workers import GatheringWorker
        inputs = self.root / 'input'
        inputs.mkdir()
        (inputs/'source.txt').write_text('1,RXTEST-,RXTEST-01\nRXTEST-,RXTEST-01,EOS\n')
        backup = self.root / 'Backup'
        correct = backup / 'MODEL-Q&0075'
        other = backup / 'OTHER&0001'
        correct.mkdir(parents=True)
        other.mkdir()
        for folder in (correct, other):
            (folder/'RXTEST-01.rpt').write_text('')
        existing = correct/'Gather_VRS_RXTEST-.txt'
        existing.write_text('previous result')
        tab = self.tab(3)
        tab.model_in.setText('MODEL-Q')
        tab.lot_in.setText('RXTEST-')
        tab.skipdata_in.setText(str(other))
        tab.exclude_json_in.clear()
        tab.ski_in.clear()
        for flag in (tab.chk_vrs, tab.chk_fvi, tab.chk_ivs):
            flag.setChecked(True)
        with patch('core.utils.BBT_LOOKUP_ROOT', str(backup)), patch('core.yield_handoff.DATA_DIR', self.root), \
             patch.object(GatheringWorker, 'build_paths', return_value=[inputs]):
            self.action(tab.btn_batch_export, 'click')
            deadline = time.monotonic()+8
            while not tab.btn_batch_export.isEnabled() and time.monotonic()<deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertTrue(tab.btn_batch_export.isEnabled())
            tab.batch_dialog.close()
            self.assertEqual(tab.batch_out_dir, correct.resolve())
            self.assertEqual(Path(tab.batch_config['skipdata_path']), correct.resolve())
            self.assertFalse(any(kind=='directory' for kind, _data in self.dialogs))
            for mode in ('VRS','FVI','IVS'):
                target = correct/f'Gather_{mode}_RXTEST-{tab.batch_export_suffix}.txt'
                self.assertIn('RXTEST-01', target.read_text())
                self.assertTrue(target.with_suffix('.txt.its.json').is_file())
            self.assertEqual(existing.read_text(),'previous result')
            yield_tab = self.tab(6)
            self.assertEqual(yield_tab.xout_limit_in.text(), '')
            self.action(yield_tab.btn_calc, 'click')
            self.assertTrue(any(data.get('title')=='허용수 입력 필요' for kind,data in self.dialogs if kind=='message'))

    def test_batch_error_displays_incomplete_host_and_keeps_only_draft(self):
        from types import SimpleNamespace
        tab = self.tab(3)
        tab.batch_modes = ['IVS']
        tab.batch_current_idx = 0
        tab.batch_run_context = {'model': 'MODEL', 'lot': 'RX-'}
        tab.batch_out_dir = self.root
        log = []
        close = W.QPushButton()
        tab.batch_dialog = SimpleNamespace(append_log=log.append, btn_close=close)
        collection = {'mode': 'IVS', 'complete': False, 'receipt': {'id': 'fixture'}, 'hosts': [
            {'path': 'offline-share', 'state': 'incomplete', 'errors': ['access denied']},
            {'path': 'online-share', 'state': 'complete', 'matched_strips': 0},
        ]}
        with patch('core.yield_handoff.remember') as remember:
            tab._on_batch_finished([], '', {'collection': collection})
        self.assertTrue(remember.called)
        self.assertIn('offline-share', log[0])
        self.assertIn('access denied', self.dialogs[-1][1]['text'])
        self.assertNotIn('online-share', log[0])
        self.assertFalse(list(self.root.glob('Gather_*')))

    def test_gathering_geometry_follows_model_and_survives_yield_save(self):
        settings = self.root / 'yield_model_settings.json'
        settings.write_text(json.dumps({
            'MODEL-A': {'x_count': '12', 'y_count': '5', 'units_per_strip': '60', 'xout_limit': '9'},
            'LEGACY': {'units_per_strip': '88', 'xout_limit': 0},
        }), encoding='utf-8')
        gather = self.tab(3)
        with patch('ui.settings_helper.YIELD_MODEL_SETTINGS_PATH', settings):
            self.action(gather.model_in, 'input', 'MODEL-A')
            self.assertEqual([w.text() for w in (gather.x_count_in,gather.y_count_in,gather.allowance_in)], ['12','5',''])
            gather.lot_in.setText('RXTEST-')
            gather.exclude_json_in.clear()
            self.assertIsNone(gather.read_config()['allowance'])
            gather.allowance_in.setText('9')
            config = gather.read_config()
            self.assertEqual((config['x_count'],config['y_count'],config['allowance'],config['fill_code']), (12,5,9,'170'))
            yield_tab = self.c.window.centralWidget().widget(6)
            yield_tab.model_in.setText('MODEL-A')
            yield_tab.restore_model_units()
            yield_tab.save_model_yield_settings()
            self.assertEqual(json.loads(settings.read_text())['MODEL-A']['x_count'], '12')
            self.action(gather.model_in, 'input', 'LEGACY')
            self.assertEqual([w.text() for w in (gather.x_count_in,gather.y_count_in,gather.allowance_in)], ['','',''])

    def test_nas_handler_sets_coordinates_and_leaves_allowance_blank(self):
        from core.nas_geometry import lookup
        repository = self.root / 'PxRepository'
        handler = repository / '7TEST-P93' / '2.0' / 'Line' / 'Top' / 'Top_handler.txt'
        handler.parent.mkdir(parents=True)
        handler.write_text('UnitRowNum=22\nUnitColNum=4\n', encoding='utf-16')
        expected = {'x_count': 22, 'y_count': 4, 'units_per_strip': 88}
        self.assertEqual(lookup('7TEST-P93', repository), (expected, str(handler)))
        tab = self.tab(3)
        tab.model_in.setText('7TEST-P93')
        settings = self.root / 'yield_model_settings.json'
        from functools import partial
        with patch('core.nas_geometry.lookup', side_effect=partial(lookup, repository=repository)), \
             patch('ui.settings_helper.YIELD_MODEL_SETTINGS_PATH', settings):
            self.action(tab.btn_nas_geometry,'click')
            deadline = time.monotonic() + 3
            while tab.geometry_worker.isRunning() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.app.processEvents()
            self.assertEqual([tab.x_count_in.text(),tab.y_count_in.text(),tab.allowance_in.text()], ['22','4',''])
            self.assertEqual(json.loads(settings.read_text())['7TEST-P93']['units_per_strip'], '88')

    def test_server_work_roots_use_local_inputs_and_legacy_ivs_range(self):
        from ui.tabs.gathering_tab import GatheringModePage
        tab = self.tab(3)
        for page in tab.findChildren(GatheringModePage):
            if page.mode == 'IVS':
                page.ip_in.setText('108')
                page.path_in.setText(r'bdm_kinsus\BDM_BACKUP')
        data = self.root / 'server'
        self.c.input_path = data / 'gui-inputs.json'
        output = self.root / 'work'
        output.mkdir()
        tab.skipdata_in.setText(str(output))
        self.c.publish_work_roots()
        manifest = data / 'network-work-roots.json'
        roots = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertIn(str(output), roots)
        for station in range(101,111):
            self.assertIn(r'\\110.15.136.' + str(station) + '\\bdm_kinsus\\', roots)
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1','DMT_DATA_DIR':str(self.root/'remote'),
                                    'DMT_ALLOWED_ROOTS':'[]','DMT_WORK_ROOTS_FILE':str(manifest)}):
            from remote_paths import allowed_path
            self.assertTrue(allowed_path(output/'result.txt'))
            self.assertFalse(allowed_path(output/'.env'))
            tab.skipdata_in.setText(str(self.root/'unapproved'))
            self.c.publish_work_roots()
            self.assertFalse(allowed_path(self.root/'unapproved'/'result.txt'))

    def test_ski_diagnosis_and_validation_json_export(self):
        txt,ids=self.fixture();folder=self.root/'bbt';folder.mkdir();ski=self.root/'final.ski';ski.write_text('RX1234-01,1,1\n')
        tab=self.tab(7);tab.strip_json_in.setText(str(ids));tab.txt_in.setText(str(txt));tab.skipdata_in.setText(str(folder));tab.ski_in.setText(str(ski))
        self.action(tab.btn_ski_validate,'click');self.assertEqual(tab.ski_validation_summary['total_strips'],1)
        output=self.root/'validation.json';self.answers['save']=str(output);self.action(tab.btn_save_ski_lists,'click');self.assertIn('strip_ids',json.loads(output.read_text(encoding='utf8')))

    def test_nas_xml_scans_and_image_download_using_local_transport(self):
        from workers.ivs_workers import IvsScanThread,DownloadThread
        from workers.strip_workers import StripIdThread,FullXmlBarcodeScanThread
        from core.ivs import load_records_from_payload
        from PySide6.QtGui import QImage,QColor
        from types import SimpleNamespace
        trial='/Pixel/ResultData/Data/AFVI1/M/RX1234-/trial1'
        local=self.root/trial.lstrip('/');strip=local/'0001';strip.mkdir(parents=True)
        (local/'Bundle1.ini').write_text('Index=1\nViaMCName=AFVI1\n')
        xml='<Root barcode="RX1234-01"><Unit><P0 ivs_1="SM03" unit_idx="1" def_side="F" strip_pt_x="1" strip_pt_y="1"/></Unit></Root>'
        (strip/'fc.xml').write_text('<Root barcode="RX1234-01"/>');(strip/'data_ivs.xml').write_text(xml)
        image_dir=strip/'Unit'/'U1'/'FC';image_dir.mkdir(parents=True)
        image=QImage(64,64,QImage.Format_RGB32);image.fill(QColor('red'))
        for suffix in ('_Sub2','','_Sub1'):image.save(str(image_dir/f'p0{suffix}.bmp'))
        root=self.root
        class Sftp:
            def resolve(self,p):return root/str(p).lstrip('/')
            def open(self,p,mode='rb'):return self.resolve(p).open(mode)
            def stat(self,p):return self.resolve(p).stat()
            def listdir(self,p):return [x.name for x in self.resolve(p).iterdir()]
            def listdir_attr(self,p):return [SimpleNamespace(filename=x.name,st_mode=x.stat().st_mode) for x in self.resolve(p).iterdir()]
            def close(self):pass
        transport=lambda:(SimpleNamespace(close=lambda:None),Sftp())
        payloads=[]
        with patch('workers.ivs_workers.connect',side_effect=transport),patch('workers.strip_workers.connect',side_effect=transport):
            for cls in (IvsScanThread,StripIdThread,FullXmlBarcodeScanThread):
                results=[];errors=[];worker=cls('M','RX1234-',trial);worker.finished.connect(results.append);worker.failed.connect(errors.append);worker.run();self.assertFalse(errors,errors);self.assertTrue(results);payloads.append(results[0])
            self.assertEqual(payloads[1]['strip_ids'],['RX1234-01']);self.assertEqual(payloads[2]['duplicate_strip_count'],1)
            records=load_records_from_payload(payloads[0]);self.assertEqual(len(records),1)
            dest=self.root/'images';result=[];worker=DownloadThread(records,str(dest),'SM03');worker.finished.connect(result.append);worker.run();self.assertEqual(result,[3]);self.assertEqual(len(list(dest.rglob('*.png'))),3)

    def test_code_preview_compares_original_xml_ski_and_selected_txt_in_gui(self):
        from core.models import DefectRecord
        from core.ivs import records_to_json
        txt,ids=self.fixture();txt.write_text(txt.read_text().replace('SM03','1111'))
        ski=self.root/'final.ski';ski.write_text('RX1234-01,1,1\nRX1234-01,2,1\n')
        xml=self.root/'original.xml';xml.write_text('<Root barcode="RX1234-01"><Unit><P0 unit_idx="0" ivs_1="1111"/></Unit></Root>')
        record=DefectRecord('RX1234-01','A','M','RX1234-','T','1','Unit','T','0','0','1111','1111','','F',1,1,0,0,str(xml),[])
        ivs=self.root/'ivs.json';ivs.write_text(json.dumps(records_to_json('M','RX1234-',[record])))
        tab=self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','txt_in','strip_json_in','skipdata_in','ski_in'):getattr(tab,field).clear()
        tab.vrs_txt_in.setText(str(txt));tab.strip_json_in.setText(str(ids));tab.ivs_json_in.setText(str(ivs));tab.ski_in.setText(str(ski));tab.units_per_strip_in.setText('4');tab.xout_limit_in.setText('3')
        self.action(tab.btn_calc,'click');self.action(tab.btn_code_preview,'click');dialog=tab.report_preview_dialog
        try:
            self.assertEqual(dict(dialog.codes),{'UNKNOWN':1,'OT06':1})
            index=next(i for i,item in enumerate(dialog.codes) if item[0]=='OT06')
            self.action(dialog.code_table,'table',row=index,column=0)
            end=time.monotonic()+3
            while 'ivs_1="1111"' not in dialog.xml_view.toPlainText() and time.monotonic()<end:self.app.processEvents();time.sleep(.01)
            self.assertIn('ivs_1="1111"',dialog.xml_view.toPlainText());self.assertIn('▶ L1:',dialog.ski_view.toPlainText());self.assertIn('1111',dialog.txt_view.toPlainText())
            self.assertIn(str(txt),dialog.proof.toPlainText());self.assertTrue(self.c.snapshot()['dialogs'])
            current=dialog.xml_view.toPlainText();dialog.apply_source(dialog._generation-1,{'xml':'stale'});self.assertEqual(dialog.xml_view.toPlainText(),current)
        finally:
            dialog.reject()
            for worker in list(getattr(dialog,'_live_workers',())):worker.stop();worker.wait(2000)
            self.app.processEvents()
        tab.units_per_strip_in.setText('8');self.action(tab.btn_code_preview,'click')
        self.assertTrue(any('입력이 변경' in data.get('text','') for kind,data in self.dialogs if kind=='message'))

    def test_remote_preview_is_bounded_but_original_image_and_full_logs_stay_in_backend(self):
        from PySide6.QtGui import QPixmap,QImage
        import base64
        tab=self.tab(0);pix=QPixmap(3200,1600);pix.fill(C.Qt.red);label=tab.image_labels[0];label.setProperty('dmt_full_pixmap',pix)
        data=self.c.widget(label)['src'];preview=QImage.fromData(base64.b64decode(data.split(',',1)[1]));self.assertEqual(preview.width(),1600);self.assertEqual(label.property('dmt_full_pixmap').width(),3200)
        text='a'*80000;tab.info.setPlainText(text);view=self.c.widget(tab.info);self.assertEqual(len(view['text']),50000);self.assertTrue(view['truncated']);self.assertEqual(tab.info.toPlainText(),text)

    def test_partial_collection_displays_host_status_and_blocks_official_txt(self):
        from ui.tabs.gathering_tab import GatheringModePage
        page=self.tab(3).findChildren(GatheringModePage)[0]
        host={'path':'offline-pc','state':'incomplete','found':0,'processed':0,'errors':['NETWORK_OFFLINE'],'matched_strips':0}
        summary={'total_strips':0,'excluded_strip_count':0,'discarded_strip_count':0,'xout_distribution':{},'collection':{'complete':False,'mode':'VRS','hosts':[host]}}
        page.on_finished([], '', summary)
        self.assertFalse(page.btn_export.isEnabled());self.assertEqual(page.status_table.columnCount(),8)
        self.assertEqual(page.status_table.item(0,0).text(),'offline-pc')
        self.assertIn('NETWORK_OFFLINE',page.result.toPlainText())
        self.answers['save']=str(self.root/'forbidden.txt');page.export_txt()
        self.assertFalse((self.root/'forbidden.txt').exists())

    def test_ot14_preview_shows_coordinates_and_exact_rpt_lines(self):
        from core.yield_calc import calculate_yield_summary_by_process
        txt, _ids = self.fixture()
        rpt = self.root / 'rpt'
        rpt.mkdir()
        (rpt / 'RX1234-01.rpt').write_text('X:001 Y:001 = 1 - BBT\n')
        tab = self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','txt_in','strip_json_in','skipdata_in','ski_in'):
            getattr(tab,field).clear()
        tab.summary = calculate_yield_summary_by_process([txt],[],[],[],None,4,3,rpt)
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1','DMT_ALLOWED_ROOTS':json.dumps([str(self.root)])}):
            self.action(tab.btn_ot14_preview,'click')
            dialog = tab.ot14_preview
            try:
                self.assertEqual(dialog.table.rowCount(),1)
                self.assertEqual(dialog.table.item(0,3).text(),'1')
                self.action(dialog.kind,'select',2)
                self.assertEqual(dialog.table.item(0,6).text(),'X:001 Y:001 = 1 - BBT')
                self.action(dialog.strip_filter,'input','OTHER')
                self.assertEqual(dialog.table.rowCount(),0)
            finally:
                dialog.reject()

    def test_report_mismatch_guard_ignores_radio_filter_and_review_allows_export(self):
        from core.report_review import mismatch_rows
        txt,ids=self.fixture();txt.write_text(txt.read_text().replace('SM03','UNKNOWN'))
        ski=self.root/'final.ski';ski.write_text('RX1234-01,1,1\n')
        tab=self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','txt_in','strip_json_in','skipdata_in','ski_in'):getattr(tab,field).clear()
        tab.vrs_txt_in.setText(str(txt));tab.strip_json_in.setText(str(ids));tab.ski_in.setText(str(ski));tab.units_per_strip_in.setText('4');tab.xout_limit_in.setText('3')
        target=self.root/'report.xlsx';self.answers['save']=str(target)
        with patch('core.report_review.DATA_DIR',self.root):
            self.action(tab.btn_calc,'click')
            self.assertTrue(any('UNKNOWN은' in d.get('text','') for k,d in self.dialogs if k=='message'))
            self.action(tab.btn_export_excel,'click');self.assertFalse(target.exists())
            self.action(tab.btn_code_preview,'click');dialog=tab.report_preview_dialog
            try:
                self.action(dialog.review_code,'input','SM03');self.action(dialog.review_note,'input','Checked original XML')
                dialog.unit_table.selectRow(0);self.action(dialog.classify_selected,'click')
                self.assertEqual(tab.summary['report_code_distribution'],{'SM03':1})
            finally:
                dialog.reject()
                for worker in list(getattr(dialog,'_live_workers',())):worker.stop();worker.wait(2000)
            # The export uses precisely the same row predicate as the filter.
            tab.summary['per_strip_rows'][0]['final_xout']=2
            self.assertEqual(len(mismatch_rows(tab.summary)),1)
            self.assertFalse(tab.radio_table_mismatch.isChecked())
            self.action(tab.btn_export_excel,'click');self.assertFalse(target.exists())
            self.action(tab.radio_table_mismatch,'check',True)
            self.assertEqual(tab.strip_table.rowCount(),2) # mismatch + sum
            tab.summary['per_strip_rows'][0]['final_xout']=1;tab.refresh_result()
            self.assertEqual(tab.strip_table.rowCount(),1) # sum only, zero mismatches
            self.action(tab.btn_export_excel,'click');self.assertTrue(target.exists(),self.dialogs)

    def test_partial_collection_or_unknown_prevents_official_excel(self):
        tab=self.tab(6);self.answers['save']=str(self.root/'forbidden.xlsx')
        for complete,unknown in ((False,0),(True,1)):
            tab.summary={'collection_validation':{'enabled':True,'complete':complete},'report_code_distribution':{'UNKNOWN':unknown}}
            tab._report_input_snapshot=None
            self.action(tab.btn_export_excel,'click')
            self.assertFalse((self.root/'forbidden.xlsx').exists())
            self.assertIn('UNKNOWN',self.dialogs[-1][1]['text'])

    def test_report_unit_table_supports_browser_ctrl_and_shift_selection(self):
        table=W.QTableWidget(4,2,self.c.window);table.setProperty('dmt_multi_select',True)
        table.setSelectionMode(W.QAbstractItemView.ExtendedSelection);table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        for row in range(4):table.setItem(row,0,W.QTableWidgetItem(str(row)))
        table.show();self.app.processEvents()
        table.selectRow(0)
        self.action(table,'table',row=1,column=0,ctrl=True)
        self.assertEqual(sorted(self.c.widget(table)['selectedRows']),[0,1])
        self.action(table,'table',row=3,column=0,shift=True)
        self.assertEqual(sorted(self.c.widget(table)['selectedRows']),[1,2,3])

    def test_remote_revalidates_inherited_paths_and_locks_busy_inputs(self):
        tab=self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','txt_in','strip_json_in','skipdata_in','ski_in'):
            getattr(tab,field).clear()
        tab.vrs_txt_in.setText(str(self.root.parent/'outside-approved-folder.txt'))
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1','DMT_ALLOWED_ROOTS':json.dumps([str(self.root)])}):
            self.c.handle({'id':801,'method':'gui.action','args':{'id':self.c.register(tab.btn_calc),'action':'click'}})
            errors=[event for event in self.events if event.get('id')==801 and event.get('error')]
            self.assertTrue(errors)
            self.assertIn('허용',errors[0]['error'])
            with patch.object(self.c,'busy',return_value=True):
                self.c.handle({'id':802,'method':'gui.action','args':{'id':self.c.register(tab.units_per_strip_in),'action':'input','value':'999'}})
            self.assertTrue(any(event.get('id')==802 and 'error' in event for event in self.events))
            self.assertNotEqual(tab.units_per_strip_in.text(),'999')

    def test_cleanup_roots_use_current_process_settings_and_reject_remote(self):
        from ui.tabs.gathering_tab import GatheringModePage
        tab=self.tab(3)
        pages=tab.findChildren(GatheringModePage)
        for page in pages:page.ip_in.setText('101-102');page.path_in.setText('TEST_BACKUP')
        self.c.handle({'id':901,'method':'gui.cleanup-roots'})
        result=next(e['result'] for e in self.events if e.get('id')==901)
        self.assertEqual(len(result),6);self.assertEqual({r['mode'] for r in result},{'IVS','FVI','VRS'})
        self.assertTrue(all('TEST_BACKUP' in r['path'] for r in result))
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1'}):self.c.handle({'id':902,'method':'gui.cleanup-roots'})
        self.assertTrue(any(e.get('id')==902 and 'error' in e for e in self.events))

if __name__=='__main__':unittest.main()
