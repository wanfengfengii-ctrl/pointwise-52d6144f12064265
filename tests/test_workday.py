from samples.demo import run_workday
from tests.support import WorkdayCase


class WorkdayTests(WorkdayCase):
    def test_full_shift_with_offline_retrieval(self):
        result = run_workday(self.directory)
        self.assertEqual(result['current'], [7, 9, 0, 14])
        self.assertEqual(result['sealed'], [9, 0, 14])
        self.assertEqual(result['reconciled'], [9, 0, 14])
        self.assertEqual(result['revisions'], [1, 2, 3])
        self.assertEqual(result['first_line'], [7, '北站采样原单', [[2, '早班核账单']]])
        self.assertEqual(result['last_line'], [11, '复测更正单', [[2, '早班核账单'], [1, '白班核账单']]])
        self.assertEqual(result['empty_line_count'], 0)
        self.assertEqual(len(set(result['statement_ids'])), 3)
