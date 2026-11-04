"""Shared dataclass models used across all core modules."""
from __future__ import annotations
from core.i18n import _

from dataclasses import dataclass, field


@dataclass
class BundleInfo:
    name: str
    indexes: set[int]
    viamc_name: str


@dataclass
class TrialContext:
    machine: str
    model: str
    lot: str
    trial: str
    path: str


@dataclass
class DefectRecord:
    strip_id: str
    machine: str
    model: str
    lot: str
    trial: str
    strip_folder: str
    container: str
    face: str
    unit_idx: str
    img_merge_idx: str
    ivs_1: str
    code_main: str
    code_sub: str
    def_side: str
    strip_pt_x: float
    strip_pt_y: float
    img_pt_x: float
    img_pt_y: float
    source_xml: str
    images: list[str] = field(default_factory=list)

    @property
    def is_rework(self) -> bool:
        return self.ivs_1 == "9998"

    @property
    def is_dummy(self) -> bool:
        return self.container == "Dummy" or self.unit_idx == "-1"

    @property
    def is_dummy_9999(self) -> bool:
        return self.is_dummy and self.ivs_1 == "9999"

    @property
    def is_rate_defect(self) -> bool:
        return bool(self.ivs_1) and self.ivs_1 not in {"0", "9998"} and not self.is_dummy

    def unit_key(self) -> str:
        if self.is_dummy:
            return f"{self.strip_id}:Dummy:{self.face}:p{self.img_merge_idx}:{self.ivs_1}:{self.strip_pt_x}:{self.strip_pt_y}"
        return f"{self.strip_id}:U{self.unit_idx}"


@dataclass
class GatherStrip:
    lot: str
    strip_id: str
    defect_lines: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    xout_applied: bool = False
    source_mtime: float = 0.0
    source_order: int = 0
    replaced_count: int = 0


@dataclass
class YieldStripBlock:
    lot: str
    strip_id: str
    defect_lines: list[str] = field(default_factory=list)
    block_count: int = 0
    duplicate_in_source_count: int = 0
    source_files: set[str] = field(default_factory=set)
