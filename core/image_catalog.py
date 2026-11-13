"""All-image metadata counts and small, stable per-code viewing selections."""
from collections import defaultdict
from posixpath import normpath


def image_key(record):
    paths=tuple(sorted({normpath(p.strip().replace('\\','/')) for p in record.images if p.strip()}))
    return ('images',paths) if paths else ('record',record.source_xml,record.strip_id,record.unit_idx,record.face,record.img_merge_idx,record.ivs_1)


def grouped_images(records):
    groups={}
    for record in records:groups.setdefault(image_key(record),[]).append(record)
    return list(groups.values())


class ImageCatalog:
    def __init__(self,records):
        self.by_code=defaultdict(list)
        ordered=sorted(records,key=lambda r:(r.strip_id,int(r.unit_idx) if str(r.unit_idx).isdigit() else -1,r.face,r.img_merge_idx,r.machine,r.trial,r.strip_folder,r.source_xml))
        per_code=defaultdict(list)
        for record in ordered:per_code[record.ivs_1].append(record)
        for code,items in per_code.items():self.by_code[code]=grouped_images(items)
        self.extra={}

    def counts(self):return {code:len(groups) for code,groups in self.by_code.items()}

    def candidate_files(self):
        return len({normpath(p.strip().replace('\\','/')) for groups in self.by_code.values() for group in groups for record in group for p in record.images if p.strip()})

    def select_range(self,code,start,end):
        total=len(self.by_code.get(code,[]))
        if not 1<=start<=end<=total:raise ValueError('범위는 1부터 전체 건수 안에서 선택하세요.')
        return self.by_code[code][start-1:end]

    def add_view_range(self,code,start,end):
        if end-start+1>200:raise ValueError('한 번에 추가로 볼 범위는 최대 200건입니다.')
        self.extra[code]={image_key(g[0]) for g in self.select_range(code,start,end)}

    def visible(self,records):
        allowed={code:{image_key(g[0]) for g in groups[:10]}|self.extra.get(code,set()) for code,groups in self.by_code.items()}
        return [g for g in grouped_images(records) if any(image_key(r) in allowed.get(r.ivs_1,set()) for r in g)]

    def download_records(self,code,start,end):
        # One file set per image group, even when multiple XML records refer to it.
        return [group[0] for group in self.select_range(code,start,end)]
