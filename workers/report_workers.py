"""On-demand source comparison, using A PC permissions and no source writes."""
import os
import posixpath
import threading
from pathlib import Path
from PySide6.QtCore import QThread,Signal
from config import NAS_ROOT
from core.cancellation import check_cancelled,OperationCancelled
from core.i18n import _
from core.report_evidence import matching_refs,local_candidates,live_text,xml_excerpt,local_allowed


class ReportEvidenceWorker(QThread):
    finished=Signal(dict)
    failed=Signal(str)
    cancelled=Signal()

    def __init__(self,trace,row,model,lot,xml_path='',text_path='',search_nas=False):
        super().__init__()
        self.trace,self.row,self.model,self.lot=trace,row,model,lot
        self.xml_path,self.text_path,self.search_nas=xml_path,text_path,search_nas
        self._cancel=threading.Event();self._ssh=None;self._sftp=None
        self.dmt_progress={'stage':'원본 비교 자료 조회','value':0,'max':0,'detail':row['strip_id']}

    def stop(self):
        self._cancel.set()
        ssh=self._ssh
        if ssh:
            def close():
                try:ssh.close()
                except Exception:pass
            threading.Thread(target=close,daemon=True).start()

    def check(self):
        check_cancelled(self._cancel.is_set)

    def connection(self):
        self.check()
        if self._sftp is None:
            from core.nas import connect
            self._ssh,self._sftp=connect()
        self.check();return self._sftp

    def remote_path(self,path):
        from core.nas import normalize_remote_path
        normalized=normalize_remote_path(path)
        root=posixpath.normpath(NAS_ROOT)
        if posixpath.normpath(normalized)!=normalized or not normalized.startswith(root.rstrip('/')+'/'):
            raise PermissionError(_('설정된 NAS 루트 밖입니다. / 此路徑位於設定的 NAS 根目錄之外。'))
        return normalized

    def find_xml(self):
        from core.nas import list_lot_trials,iter_dirs,folder_index,find_fc_file,read_barcode_from_xml
        from core.ivs import find_ivs_files
        sftp=self.connection();found=[]
        for context in list_lot_trials(self.model,self.lot,sftp,self._cancel.is_set):
            for folder in iter_dirs(sftp,context.path):
                self.check()
                if folder_index(folder) is None:continue
                location=context.path+'/'+folder
                self.dmt_progress['detail']=location
                fc=find_fc_file(location,folder,sftp)
                if not fc:continue
                barcode=read_barcode_from_xml(fc,sftp)
                if barcode and barcode.upper()==self.row['strip_id'].upper():
                    found.extend(find_ivs_files(location,sftp));found.append(fc)
        return list(dict.fromkeys(found))

    def read_xml(self,path):
        self.check();self.dmt_progress['detail']=path
        if path.startswith('/') and not path.startswith('//'):
            normalized=self.remote_path(path)
            file=self.connection().open(normalized,'rb')
        else:
            local_allowed(path);file=Path(path).open('rb')
        with file:
            chunks=[];size=0
            while True:
                self.check();chunk=file.read(65536)
                if not chunk:break
                size+=len(chunk)
                if size>16*1024*1024:raise ValueError(_('XML 표시 상한 16 MB를 초과했습니다.'))
                chunks.append(chunk)
        return xml_excerpt(b''.join(chunks),self.row,self.trace['strips'].get(self.row['strip_id'],{}))

    def run(self):
        try:
            self.check();row=self.row;coord=(row['x'],row['y']);result={}
            candidates=local_candidates(self.trace,row)
            selected_text=self.text_path or (candidates[0] if candidates else '')
            result['text_paths']=candidates;result['selected_text']=selected_text
            if selected_text:
                try:
                    result['text']=live_text(self.trace,selected_text,row['strip_id'],coord,'rpt' if selected_text.lower().endswith('.rpt') else 'txt',self.check)
                except OperationCancelled:raise
                except Exception as error:result['text']=selected_text+'\n'+str(error)
            else:result['text']=_('TXT 원본 경로가 없습니다.')
            ski=self.trace.get('ski_path','')
            if ski:
                try:result['ski']=live_text(self.trace,ski,row['strip_id'],coord,'ski',self.check)
                except OperationCancelled:raise
                except Exception as error:result['ski']=ski+'\n'+str(error)
            else:result['ski']=_('계산에 SKI가 지정되지 않았습니다.')
            paths=[ref[4] for ref in matching_refs(self.trace,row) if ref[4]]
            if not paths:
                paths=[ref[4] for ref in self.trace.get('strips',{}).get(row['strip_id'],{}).get('ivs_records',[]) if ref[4]]
            if self.search_nas:
                paths=self.find_xml()
            if self.xml_path and not self.search_nas:
                paths=[self.xml_path]+paths
            paths=list(dict.fromkeys(paths));result['xml_paths']=paths
            selected_xml=(self.xml_path if not self.search_nas else '') or (paths[0] if paths else '')
            result['selected_xml']=selected_xml
            if selected_xml:
                try:
                    result['xml']=selected_xml+'\n'+_('현재 원본 XML의 Unit 항목 발췌')+'\n'+self.read_xml(selected_xml)
                except OperationCancelled:raise
                except Exception as error:result['xml']=selected_xml+'\n'+str(error)
            else:result['xml']=_('XML 경로를 찾지 못했습니다. NAS XML 찾기로 조회할 수 있습니다.')
            self.check();self.finished.emit(result)
        except OperationCancelled:self.cancelled.emit()
        except Exception as error:
            if self._cancel.is_set():self.cancelled.emit()
            else:self.failed.emit(str(error))
        finally:
            for connection in (self._sftp,self._ssh):
                if connection:
                    try:connection.close()
                    except Exception:pass
