from dataclasses import replace

from archive.reconcile import reconcile_statement
from archive.retrieve import StatementNotFound, retrieve
from archive.seal import SealRejected, seal
from intake.origins import ReadingOrigin
from ledger.adjustments import AdjustmentRejected, adjust, read_adjustments
from ledger.daily import read

from tests.support import (DAY, ORIGINAL, PLUS_ONE, PLUS_TWO, RESTORED, WorkdayCase,
                           arm_commit_failure, build_shifts, first_book, initialize_branch,
                           prepare_first, sample, take)


class CloseTests(WorkdayCase):
    def test_held_empty_offline(self):
        source = self.source()
        pieces = build_shifts(source, False)
        work = self.branch()
        early = first_book(work, pieces.initial, pieces.original)
        take(work, pieces.middle)
        source.close()
        work.close()
        offline = self.connect('branch')
        later = adjust(offline, pieces.original, 1, PLUS_ONE)
        self.assertEqual(read_adjustments(offline, pieces.original), (early, later))
        self.assertEqual((read(offline, DAY).revision, read(offline, DAY).total), (3, 0))
        empty_id = seal(offline, DAY)
        take(offline, pieces.final)
        self.assertEqual(read(offline, DAY).total, 14)
        offline.close()
        closed = retrieve(self.connect('branch'), empty_id)
        self.assertEqual((closed.revision, closed.lines, closed.total), (3, (), 0))
        self.assertTrue(reconcile_statement(closed).matches)

    def test_no_book(self):
        work = self.branch()
        self.assertIsNone(read(work, DAY))
        with self.assertRaises(SealRejected):
            seal(work, DAY)
        with self.assertRaises(StatementNotFound):
            retrieve(work, 1)

    def test_seal_write_failure(self):
        original, initial = prepare_first(self.source())
        work = self.branch()
        first_book(work, initial, original)
        old_id = seal(work, DAY)
        adjust(work, original, 3, PLUS_ONE)
        arm_commit_failure(work, 'seal')
        with self.assertRaisesRegex(SealRejected, 'FOREIGN KEY constraint failed'):
            seal(work, DAY)
        self.assertFalse(work.in_transaction)
        work.close()
        reopened = self.connect('branch')
        with self.assertRaises(StatementNotFound):
            retrieve(reopened, old_id + 1)
        self.assertEqual(retrieve(reopened, old_id).total, 9)
        self.assertEqual(read(reopened, DAY).total, 12)

    def test_adjustment_origins(self):
        original, initial = prepare_first(self.source())
        work = self.branch()
        take(work, initial)
        four = adjust(work, original, 4, PLUS_TWO)
        minus_two = adjust(work, original, -2, PLUS_ONE)
        number = seal(work, DAY)
        work.close()
        statement = retrieve(self.connect('branch'), number)
        line, = statement.lines
        self.assertEqual((line.credential, line.quantity, line.origin), (original, 7, ORIGINAL))
        self.assertEqual([(item.adjustment_id, item.credential, item.delta, item.origin)
                          for item in line.adjustments],
                         [(four.adjustment_id, original, 4, PLUS_TWO),
                          (minus_two.adjustment_id, original, -2, PLUS_ONE)])
        check = reconcile_statement(statement)
        self.assertEqual((statement.total, check.line_totals, check.total), (9, (9,), 9))
        self.assertTrue(check.matches)
        shortened = replace(line, adjustments=line.adjustments[:-1])
        self.assertFalse(reconcile_statement(replace(statement, lines=(shortened,))).matches)

    def test_by_original_id(self):
        source = self.source()
        pieces = build_shifts(source, False)
        work = self.branch()
        early = first_book(work, pieces.initial, pieces.original)
        first_id = seal(work, DAY)
        take(work, pieces.middle)
        adjust(work, pieces.original, 1, PLUS_ONE)
        take(work, pieces.final)
        latest_id = seal(work, DAY)
        source.close()
        work.close()
        offline = self.connect('branch')
        first, latest = (retrieve(offline, number) for number in (first_id, latest_id))
        self.assertEqual((first.statement_id, first.revision, first.total), (first_id, 1, 9))
        self.assertEqual((latest.statement_id, latest.revision, latest.total), (latest_id, 4, 14))
        self.assertEqual((first.lines[0].quantity, first.lines[0].origin), (7, ORIGINAL))
        self.assertEqual((latest.lines[0].quantity, latest.lines[0].origin), (11, RESTORED))
        self.assertEqual([(item.adjustment_id, item.delta, item.origin) for item in first.lines[0].adjustments],
                         [(early.adjustment_id, 2, PLUS_TWO)])
        self.assertEqual([(item.delta, item.origin) for item in latest.lines[0].adjustments],
                         [(2, PLUS_TWO), (1, PLUS_ONE)])
        for statement, total in ((first, 9), (latest, 14)):
            check = reconcile_statement(statement)
            self.assertEqual(check.total, total)
            self.assertTrue(check.matches)

    def test_unknown_statement(self):
        original, initial = prepare_first(self.source())
        work = self.branch()
        first_book(work, initial, original)
        statement_id = seal(work, DAY)
        for unknown in (statement_id + 1, 0, True):
            with self.assertRaises(StatementNotFound):
                retrieve(work, unknown)

    def test_reinitialize(self):
        source = self.source()
        pieces = build_shifts(source, False)
        work = self.branch()
        first_book(work, pieces.initial, pieces.original)
        number = seal(work, DAY)
        sealed = retrieve(work, number)
        take(work, pieces.skip)
        late = adjust(work, pieces.original, 1, PLUS_ONE)
        before = read(work, DAY)
        initialize_branch(work)
        source.close()
        work.close()
        reopened = self.branch()
        self.assertEqual(read(reopened, DAY), before)
        self.assertEqual((before.revision, before.total), (4, 14))
        self.assertEqual(retrieve(reopened, number), sealed)
        self.assertEqual(sealed.total, 9)
        self.assertEqual(read_adjustments(reopened, pieces.original)[-1], late)

    def test_exact_integers_survive_supply_and_archive(self):
        large = (1 << 20000) + 7
        original, initial = prepare_first(self.source(), reading=sample(quantity=large))
        work = self.branch()
        take(work, initial)
        with self.assertRaises(AdjustmentRejected):
            adjust(work, original, True, PLUS_ONE)
        change = adjust(work, original, 9 - large, PLUS_TWO)
        number = seal(work, DAY)
        work.close()
        reopened = self.connect('branch')
        result = retrieve(reopened, number)
        self.assertTrue(result.lines[0].quantity == large)
        self.assertEqual(result.lines[0].adjustments[0].delta + large, 9)
        self.assertEqual(result.lines[0].adjustments[0].origin, change.origin)
        self.assertEqual((read(reopened, DAY).total, result.total, reconcile_statement(result).total),
                         (9, 9, 9))
