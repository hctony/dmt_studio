import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from core.ivs import records_to_json,load_ivs_dummy_scrap_strips
from core.models import DefectRecord,TrialContext
from core.report_codes import ordered_report_codes
from core.report_trace import trace_rows,matching_groups
from core.report_evidence import frozen_text,live_text,matching_refs,xml_excerpt
from core.yield_calc import calculate_yield_summary_by_process,build_yield_report_aggregation
from workers.report_workers import ReportEvidenceWorker


class ReportTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.lot='RXTEST-';self.strip=self.lot+'01'

    def record(self,code,unit='3',dummy=False,xml=''):
        return DefectRecord(self.strip,'A','MODEL',self.lot,'T','1','Dummy' if dummy else 'Unit','T','-1' if dummy else unit,'0',code,code,'','F',2,2,0,0,xml,[])

    def fixture(self,rows,ski=None,records=None,bbt=None,extra=None):
        txt=self.root/'gather.txt';txt.write_text(f'1,{self.lot},{self.strip}\n'+''.join(f'{self.lot},{self.strip},{x},{y},T,{code}\n' for x,y,code in rows)+f'{self.lot},{self.strip},EOS\n')
        ski_path=None
        if ski is not None:
            ski_path=self.root/'final.ski';ski_path.write_text(''.join(f'{self.strip},{x},{y}\n' for x,y in ski))
        jsons=[]
        if records:
            file=self.root/'ivs.json';file.write_text(json.dumps(records_to_json('MODEL',self.lot,records)));jsons=[file]
        bbt_folder=None
        if bbt:
            bbt_folder=self.root/'bbt';bbt_folder.mkdir();(bbt_folder/(self.strip+'.rpt')).write_text(''.join(f'X:{x:03d} Y:{y:03d} = 1 - OT14\n' for x,y in bbt))
        candidates=load_ivs_dummy_scrap_strips(jsons,self.lot)
        summary=calculate_yield_summary_by_process([txt]+(extra or []),[],[],jsons,None,4,2,bbt_folder,ski_path,None,candidates)
        report=build_yield_report_aggregation(summary,'MODEL')
        return summary,report

    def test_preview_uses_same_positive_code_counts_and_170_mapping_as_excel(self):
        ordered=ordered_report_codes({'final_defects_by_code_including_bbt':{'170':{'count':3},'OT06':{'count':2},'1111':{'count':1},'UNKNOWN':{'count':0}}})
        self.assertEqual(ordered,[('1111',1),('OT06',5)])

    def test_preview_counts_equal_the_actual_generated_excel_columns(self):
        from core.export_excel import export_report_to_2xlsx
        from openpyxl import load_workbook
        summary,report=self.fixture([(1,1,''),(2,1,'1111'),(1,2,'OT06'),(2,2,'170')],[(1,1),(2,1),(1,2),(2,2)])
        target=self.root/'report.xlsx';template=Path(__file__).resolve().parent.parent/'resources/templates/report.xlsx'
        self.assertTrue(export_report_to_2xlsx(report,str(template),str(target))['success'])
        workbook=load_workbook(target);sheet=workbook.active
        actual={sheet.cell(1,column).value:sheet.cell(3,column).value for column in range(10,sheet.max_column+1)}
        self.assertEqual(actual,dict(ordered_report_codes(report)));workbook.close()
        self.assertEqual(actual,{'UNKNOWN':1,'1111':1,'OT06':2})

    def test_unknown_causes_are_separate_and_live_ski_keeps_line_numbers(self):
        summary,report=self.fixture([(1,1,'')],[(1,1),(2,1)])
        self.assertEqual(dict(ordered_report_codes(report)),{'UNKNOWN':2})
        rows,total=trace_rows(summary['report_trace'],'UNKNOWN')
        self.assertEqual(total,2);self.assertEqual({r['reason'] for r in rows},{'missing_code','ski_unassigned'})
        text=live_text(summary['report_trace'],self.root/'final.ski',self.strip,(2,1),'ski')
        self.assertIn('▶ L2:',text);self.assertIn('계산 시점과 파일 크기·수정 시각 일치',text)
        (self.root/'final.ski').write_text(f'{self.strip},1,1\n')
        self.assertIn('계산 후 파일 변경됨',live_text(summary['report_trace'],self.root/'final.ski',self.strip,(2,1),'ski'))

    def test_trace_names_actual_selected_file_even_when_newer_candidate_is_rejected(self):
        extra=self.root/'newer.txt';extra.write_text(f'1,{self.lot},{self.strip}\n{self.lot},{self.strip},2,1,T,SM03\n{self.lot},{self.strip},EOS\n')
        os.utime(extra,(time.time()+60,time.time()+60))
        summary,report=self.fixture([(1,1,'1111')],[(1,1)],extra=[extra])
        self.assertEqual(dict(ordered_report_codes(report)),{'1111':1})
        info=summary['report_trace']['strips'][self.strip]['processes']['VRS']
        self.assertEqual(info['selected_source'],str(self.root/'gather.txt'));self.assertIn(str(extra),info['sources'])

    def test_unknown_uses_sixth_column_even_with_extra_or_trailing_columns(self):
        summary,report=self.fixture([(1,1,',metadata'),(2,1,'UNKNOWN,')],[(1,1),(2,1)])
        rows,total=trace_rows(summary['report_trace'],'UNKNOWN');self.assertEqual(total,2)
        reasons={(r['x'],r['y']):r['reason'] for r in rows}
        self.assertEqual(reasons,{(1,1):'missing_code',(2,1):'gather'})
        self.assertIn('▶ L2:',live_text(summary['report_trace'],self.root/'gather.txt',self.strip,(1,1),'txt'))

    def test_json_1111_recovery_keeps_exact_source_record_and_normalizes_other_codes(self):
        xml=self.root/'actual.xml';xml.write_text(f'<Root barcode="{self.strip}"><Unit><P0 unit_idx="3" ivs_1="1111"/><P1 unit_idx="0" ivs_1="SM03"/></Unit></Root>')
        full=[(1,1),(2,1),(1,2),(2,2)]
        summary,report=self.fixture([(x,y,'170') for x,y in full],full,[self.record('1111',xml=str(xml))])
        self.assertEqual(dict(ordered_report_codes(report)),{'1111':1,'OT06':3})
        rows,total=trace_rows(summary['report_trace'],'1111');self.assertEqual(total,1);row=rows[0]
        self.assertEqual(row['reason'],'ivs_recovery');self.assertEqual(matching_refs(summary['report_trace'],row)[0][4],str(xml))
        self.assertIn('1111',frozen_text(summary['report_trace'],row))
        excerpt=xml_excerpt(xml.read_bytes(),row,summary['report_trace']['strips'][self.strip])
        self.assertIn('ivs_1="1111"',excerpt);self.assertNotIn('ivs_1="SM03"',excerpt)
        result=[];errors=[];worker=ReportEvidenceWorker(summary['report_trace'],row,'MODEL',self.lot)
        worker.finished.connect(result.append);worker.failed.connect(errors.append);worker.run()
        self.assertEqual(errors,[]);self.assertIn('1111',result[0]['xml']);self.assertIn('L',result[0]['ski'])

    def test_dummy_assignment_counts_bbt_first_and_traces_only_remaining_coordinates(self):
        full=[(1,1),(2,1),(1,2),(2,2)]
        summary,report=self.fixture([(2,2,'SM03')],full,[self.record('9999',dummy=True)],[(1,1)])
        self.assertEqual(dict(ordered_report_codes(report)),{'SM03_DUMMY9999':3,'OT14':1})
        groups=matching_groups(summary['report_trace'],'SM03_DUMMY9999')
        self.assertEqual(groups[0]['reason'],'dummy9999');self.assertNotIn([1,1],groups[0]['coords'])
        self.assertEqual(trace_rows(summary['report_trace'],'OT14')[0][0]['reason'],'bbt')

    def test_pagination_never_repeats_or_loses_assignment_rows(self):
        trace={'groups':[{'strip_id':self.strip,'code':'170','process':'XOUT','reason':'xout_fill','coords':[[x,1] for x in range(1,452)]}]}
        rows=[]
        for offset in (0,200,400):
            page,total=trace_rows(trace,'OT06',offset);rows+=page;self.assertEqual(total,451)
        self.assertEqual(len(rows),451);self.assertEqual(len({r['x'] for r in rows}),451)

    def test_bbt_source_uses_strip_header_instead_of_guessing_filename_even_from_cache(self):
        from core.report_evidence import local_candidates
        from core.skipdata import load_skipdata_folder
        folder=self.root/'bbt';folder.mkdir();original=folder/'combined-report.rpt'
        original.write_text(f'[OTHER-01]\nX:001 Y:001 = 1 - OT14\n[{self.strip}]\nX:002 Y:001 = 1 - OT14\n')
        load_skipdata_folder(folder)  # Warm the same cache used by earlier screens.
        summary,report=self.fixture([(2,2,'1111')],[(2,1),(2,2)])
        # Recalculate using the combined RPT file, with a different filename from the StripID.
        summary=calculate_yield_summary_by_process([self.root/'gather.txt'],[],[],[],None,4,2,folder,self.root/'final.ski')
        row=trace_rows(summary['report_trace'],'OT14')[0][0]
        candidates=local_candidates(summary['report_trace'],row)
        self.assertEqual(candidates[0],str(original));self.assertNotIn(str(folder/(self.strip+'.rpt')),candidates)
        text=live_text(summary['report_trace'],candidates[0],self.strip,(2,1),'rpt')
        self.assertIn('▶ L4:',text);self.assertNotIn('L2:',text)

    def test_report_ot14_counts_bbt_and_process_txt_once_each(self):
        summary,report=self.fixture([(2,1,'OT14')],[(1,1),(2,1)],bbt=[(1,1)])
        self.assertEqual(dict(ordered_report_codes(report)),{'OT14':2})
        self.assertEqual(report['existing_bbt_ot14_units'],1)
        self.assertEqual(report['final_defects_by_code_including_bbt']['OT14']['process_breakdown'],{'BBT':1,'VRS':1})
        self.assertEqual(trace_rows(summary['report_trace'],'OT14')[1],2)
        # A process-only OT14 must not also be added as if it came from BBT.
        summary,report=self.fixture([(2,1,'OT14')],[(2,1)])
        self.assertEqual(dict(ordered_report_codes(report)),{'OT14':1})
        self.assertEqual(report['existing_bbt_ot14_units'],0)

    def test_selected_strip_bbt_is_ot14_regardless_of_rpt_code_and_ski_membership(self):
        summary,report=self.fixture([(1,1,'1111'),(2,2,'SM03')],[(1,1),(2,2)],bbt=[(1,1),(2,1)])
        rpt=self.root/'bbt'/(self.strip+'.rpt');rpt.write_text(rpt.read_text().replace('OT14','OT03'))
        summary=calculate_yield_summary_by_process([self.root/'gather.txt'],[],[],[],None,4,3,self.root/'bbt',self.root/'final.ski')
        report=build_yield_report_aggregation(summary,'MODEL')
        self.assertEqual(summary['bbt_units'],2)
        self.assertEqual(dict(ordered_report_codes(report)),{'OT14':2,'SM03':1})
        self.assertEqual(report['existing_bbt_ot14_units'],2)
        self.assertEqual(summary['defective_units'],3)
        self.assertEqual(summary['good_units'],1)
        self.assertEqual(trace_rows(summary['report_trace'],'OT14')[0][0]['reason'],'bbt')

    def test_bbt_outside_ski_is_included_but_other_strip_rpt_is_not(self):
        summary,report=self.fixture([(2,1,'SM03')],[(2,1)],bbt=[(1,1)])
        other=self.root/'bbt'/'OTHER.rpt';other.write_text('X:001 Y:002 = 1 - 9998\n')
        summary=calculate_yield_summary_by_process([self.root/'gather.txt'],[],[],[],None,4,3,self.root/'bbt',self.root/'final.ski')
        report=build_yield_report_aggregation(summary,'MODEL')
        self.assertEqual(dict(ordered_report_codes(report)),{'OT14':1,'SM03':1})
        self.assertEqual(summary['total_strips'],1);self.assertEqual(summary['defective_units'],2)
        self.assertEqual(report['final_vs_ski_delta'],1)

    def test_xml_outside_nas_root_is_rejected_before_connecting(self):
        row={'strip_id':self.strip,'x':1,'y':1,'code':'1111','reason':'gather','process':'IVS'}
        worker=ReportEvidenceWorker({'strips':{self.strip:{'x_count':2}}},row,'MODEL',self.lot)
        with patch('core.nas.connect') as connect:
            with self.assertRaises(PermissionError):worker.read_xml('/etc/private.xml')
        connect.assert_not_called()

    def test_optional_nas_lookup_finds_original_strip_without_writing_source(self):
        row={'strip_id':self.strip,'x':1,'y':1,'code':'1111','reason':'gather','process':'IVS'}
        path='/Pixel/ResultData/Data/A/MODEL/RXTEST-/T'
        trace={'strips':{self.strip:{'x_count':2}},'inputs':{},'file_versions':{}}
        worker=ReportEvidenceWorker(trace,row,'MODEL',self.lot,search_nas=True)
        results=[];errors=[];worker.finished.connect(results.append);worker.failed.connect(errors.append)
        import io
        class Sftp:
            def open(self,path,mode):
                self_mode=mode;self.assert_mode=mode
                if mode!='rb':raise AssertionError('Source must be read-only')
                return io.BytesIO(f'<Root barcode="{row["strip_id"]}"><Unit><P0 unit_idx="0" ivs_1="1111"/></Unit></Root>'.encode())
            def close(self):pass
        with patch('core.nas.connect',return_value=(SimpleNamespace(close=lambda:None),Sftp())),patch('core.nas.list_lot_trials',return_value=[TrialContext('A','MODEL',self.lot,'T',path)]),patch('core.nas.iter_dirs',return_value=['1']),patch('core.nas.find_fc_file',return_value=path+'/1/fc.xml'),patch('core.nas.read_barcode_from_xml',return_value=self.strip),patch('core.ivs.find_ivs_files',return_value=[path+'/1/data_ivs.xml']):
            worker.run()
        self.assertEqual(errors,[]);self.assertIn('1111',results[0]['xml']);self.assertTrue(results[0]['selected_xml'].endswith('data_ivs.xml'))
