import unittest
from core.models import GatherStrip
from core.gather import build_gather_duplicate_conflict
from core.duplicate_review import build_review_plan


class DuplicateReviewTests(unittest.TestCase):
    def strip(self, coords, code='OT12', stamp=1):
        return GatherStrip('L','S',[f'L,S,{x},{y},T,{code}' for x,y in coords],['file'],source_mtime=stamp)

    def test_user_example_identical_nine_plus_bbt_three_equals_ski_twelve(self):
        coords={(1,1),(2,1),(14,1),(11,2),(15,3),(22,3),(11,4),(21,4),(22,4)}
        bbt={(18,1),(5,3),(18,4)}
        a=self.strip(coords);b=self.strip(coords,stamp=2)
        plan=build_review_plan([build_gather_duplicate_conflict(a,b,{'S':bbt},{'S':coords|bbt})])
        self.assertEqual(len(plan),1)
        self.assertEqual(plan[0]['recommended'],0)
        self.assertEqual(plan[0]['original_count'],1)
        self.assertEqual(plan[0]['candidates'][0]['source_mtime'],2)

    def test_same_exact_coords_uses_latest_candidate_even_with_different_codes(self):
        a=self.strip({(1,1)});b=self.strip({(1,1)},code='SM09',stamp=2)
        plan=build_review_plan([build_gather_duplicate_conflict(a,b,{}, {'S':{(1,1)}})])
        self.assertEqual(plan[0]['recommended'],1)

    def test_all_versions_of_strip_grouped_and_only_exact_selected(self):
        a=self.strip({(1,1)});b=self.strip({(2,1)});c=self.strip({(3,1)})
        ski={'S':{(3,1)}}
        plan=build_review_plan([build_gather_duplicate_conflict(a,b,{},ski),build_gather_duplicate_conflict(b,c,{},ski)])
        self.assertEqual(len(plan),1)
        selected=plan[0]['candidates'][plan[0]['recommended']]
        self.assertEqual(selected['defect_lines'],c.defect_lines)

    def test_partial_merge_is_not_automatically_approved(self):
        a=self.strip({(1,1)});b=self.strip({(2,1)})
        plan=build_review_plan([build_gather_duplicate_conflict(a,b,{}, {'S':{(1,1),(2,1),(3,1)}})])
        self.assertIsNone(plan[0]['recommended'])

    def test_exact_merge_is_automatic_when_no_original_candidate_matches(self):
        a=self.strip({(1,1)});b=self.strip({(2,1)})
        plan=build_review_plan([build_gather_duplicate_conflict(a,b,{}, {'S':{(1,1),(2,1)}})])
        self.assertEqual(plan[0]['recommended'],2)
