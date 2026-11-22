import json
from dataclasses import replace

from archive.retrieve import retrieve
from archive.seal import seal
from exchange.envelope import decode_piece, encode_piece
from exchange.issue import IssueRejected, issue
from exchange.issued_store import read_issued_piece
from intake.origins import ReadingOrigin
from ledger.accept import AcceptanceError, accept
from ledger.adjustments import AdjustmentRejected, adjust, read_adjustments
from ledger.daily import read
from quality.revisions import freeze

from tests.support import (DAY, OTHER_DAY, PLUS_ONE, PLUS_TWO, RESTORED, WorkdayCase,
                           allow_commit, arm_commit_failure, build_shifts, first_book,
                           operation, prepare_first, sample, take)


def projection(piece):
    return [[row.credential.number, row.quantity] for row in piece.rows]


class HandoffTests(WorkdayCase):
    def test_recovery(self):
        source = self.source()
        original, initial = prepare_first(source)
        work = self.branch()
        take(work, initial)
        self.assertEqual(read(work, DAY).total, 7)
        adjust(work, original, 2, PLUS_TWO)
        self.assertEqual(read(work, DAY).total, 9)
        freeze(source, DAY, 1, [operation(original, 'hold')])
        freeze(source, DAY, 2, [operation(original, 'release', 11, RESTORED)])
        take(work, issue(source, DAY, 1, 2))
        self.assertEqual((read(work, DAY).lines, read(work, DAY).total), ((), 0))
        take(work, issue(source, DAY, 2, 3))
        line, = read(work, DAY).lines
        self.assertEqual((line.row.quantity, line.row.origin, line.total), (11, RESTORED, 13))
        self.assertEqual([record.delta for record in line.adjustments], [2])

    def test_signed_sum(self):
        pieces = build_shifts(self.source(), False)
        work = self.branch()
        first_book(work, pieces.initial, pieces.original)
        minus = adjust(work, pieces.original, -3, ReadingOrigin('扣减核账单', 5))
        self.assertEqual(read(work, DAY).total, 6)
        take(work, pieces.middle)
        self.assertEqual(read(work, DAY).total, 0)
        take(work, pieces.final)
        line, = read(work, DAY).lines
        self.assertEqual((line.total, read(work, DAY).total), (10, 10))
        self.assertEqual([(item.delta, item.origin) for item in line.adjustments],
                         [(2, PLUS_TWO), (-3, minus.origin)])

    def test_issue_fixed(self):
        source = self.source()
        original, _ = prepare_first(source, reading=sample(quantity=65537))
        freeze(source, DAY, 1, [operation(original, 'hold')])
        held = issue(source, DAY, 1, 2)
        payload = encode_piece(held)
        self.assertEqual((held.base_revision, held.target_revision, held.rows), (1, 2, ()))
        self.assertNotIn(b'0x10001', payload)
        freeze(source, DAY, 2, [operation(original, 'release', 11, RESTORED)])
        later = issue(source, DAY, 2, 3)
        source.close()
        reopened = self.connect('source')
        self.assertEqual(encode_piece(read_issued_piece(reopened, held.issue_id)), payload)
        self.assertEqual(held, decode_piece(payload))
        self.assertEqual(projection(read_issued_piece(reopened, later.issue_id)), [['K', 11]])

    def test_invalid_issue_creates_no_piece(self):
        source = self.source()
        original, initial = prepare_first(source)
        for station_day, base, target in [(OTHER_DAY, 0, 1), (DAY, 1, 1), (DAY, 1, 2)]:
            with self.subTest(station_day=station_day, base=base, target=target):
                with self.assertRaises(IssueRejected):
                    issue(source, station_day, base, target)
        self.assertIsNone(read_issued_piece(source, initial.issue_id + 1))
        freeze(source, DAY, 1, [operation(original, 'hold')])
        self.assertEqual(issue(source, DAY, 1, 2).issue_id, initial.issue_id + 1)

    def test_skip_revision(self):
        source = self.source()
        pieces = build_shifts(source, True)
        source.close()
        for name, deliveries in [('step', (pieces.middle, pieces.final)), ('skip', (pieces.skip,))]:
            with self.subTest(name=name):
                work = self.branch(name)
                saved = first_book(work, pieces.initial, pieces.original)
                for delivery in deliveries:
                    take(work, delivery)
                work.close()
                offline = self.connect(name)
                book = read(offline, DAY)
                self.assertEqual((book.revision, book.total), (4, 11))
                self.assertEqual(book.lines[0].row.credential, pieces.current)
                self.assertEqual(book.lines[0].adjustments, ())
                with self.assertRaises(AdjustmentRejected):
                    adjust(offline, pieces.original, 1, PLUS_ONE)
                self.assertEqual(read_adjustments(offline, pieces.original), (saved,))
                adjust(offline, pieces.current, -1, PLUS_ONE)
                self.assertEqual(read(offline, DAY).total, 10)

    def test_equal_projection(self):
        actual = {}
        downstream = {}
        for label, replacement in [('held', False), ('replacement', True)]:
            source = self.source(label)
            pieces = build_shifts(source, replacement)
            source.close()
            step = self.branch(f'{label}-step')
            first_book(step, pieces.initial, pieces.original)
            totals = [read(step, DAY).total]
            for piece in (pieces.middle, pieces.final):
                take(step, piece)
                totals.append(read(step, DAY).total)
            skip = self.branch(f'{label}-skip')
            first_book(skip, pieces.initial, pieces.original)
            take(skip, pieces.skip)
            offline_name = f'{label}-offline'
            offline = self.branch(offline_name)
            first_book(offline, pieces.initial, pieces.original)
            old_statement = seal(offline, DAY)
            take(offline, pieces.middle)
            offline.close()
            offline = self.connect(offline_name)
            try:
                adjust(offline, pieces.original, 1, PLUS_ONE)
            except AdjustmentRejected:
                accepted = False
            else:
                accepted = True
            empty_statement = seal(offline, DAY)
            take(offline, pieces.final)
            actual[label] = {
                'projection': [projection(piece) for piece in (pieces.initial, pieces.middle, pieces.final)],
                'step': totals, 'skip': read(skip, DAY).total, 'offline_adjustment': accepted,
                'offline_final': read(offline, DAY).total,
            }
            saved = retrieve(offline, old_statement)
            empty = retrieve(offline, empty_statement)
            downstream[label] = {'old_total': saved.total, 'empty_total': empty.total,
                                 'empty_lines': len(empty.lines)}
        visible = [[['K', 7]], [], [['K', 11]]]
        expected = {
            'held': {'projection': visible, 'step': [9, 0, 13], 'skip': 13,
                     'offline_adjustment': True, 'offline_final': 14},
            'replacement': {'projection': visible, 'step': [9, 0, 11], 'skip': 11,
                            'offline_adjustment': False, 'offline_final': 11},
        }
        expected_downstream = {name: {'old_total': 9, 'empty_total': 0, 'empty_lines': 0}
                               for name in ('held', 'replacement')}
        self.assertEqual(actual, expected, 'SAMPLE_LIFETIME_MISACCOUNT')
        self.assertEqual(downstream, expected_downstream, 'SAMPLE_LIFETIME_MISACCOUNT')
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v3.equal_projection',
            'input': {'number': 'K', 'initial': 7, 'later': 11, 'early_delta': 2,
                      'offline_delta': 1, 'source_revisions_received': [1, 3, 4]},
            'expected': expected, 'actual': actual, 'downstream_expected': expected_downstream,
            'downstream_actual': downstream,
            'code_paths': ['exchange/evidence.py', 'ledger/accept.py', 'ledger/eligibility.py',
                           'ledger/daily.py', 'archive/seal.py', 'archive/retrieve.py'],
            'oracle': 'Hand sums: 7+2=9, released original 11+2=13, offline 11+2+1=14, new sample 11.'
        }, sort_keys=True))

    def test_wrong_base(self):
        pieces = build_shifts(self.source(), True)
        work = self.branch()
        first_book(work, pieces.initial, pieces.original)
        with self.assertRaisesRegex(AcceptanceError, 'base revision mismatch'):
            take(work, pieces.final)
        self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (1, 9))
        adjust(work, pieces.original, 1, PLUS_ONE)
        self.assertEqual(read(work, DAY).total, 10)

    def test_foreign_station(self):
        source = self.source()
        original, initial = prepare_first(source)
        other, _ = prepare_first(source, OTHER_DAY, sample('Q', 42))
        freeze(source, OTHER_DAY, 1, [operation(other, 'correct', 43, RESTORED)])
        foreign = issue(source, OTHER_DAY, 1, 2)
        work = self.branch()
        first_book(work, initial, original)
        with self.assertRaisesRegex(AcceptanceError, 'another station day'):
            accept(work, DAY, encode_piece(foreign))
        self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (1, 9))
        adjust(work, original, 1, PLUS_ONE)
        self.assertEqual(read(work, DAY).total, 10)
        self.assertIsNone(read(work, OTHER_DAY))

    def test_invalid_piece(self):
        pieces = build_shifts(self.source(), True)
        work = self.branch()
        invalid_first = replace(pieces.initial, evidence=replace(pieces.initial.evidence, added=()))
        with self.assertRaises(AcceptanceError):
            take(work, invalid_first)
        self.assertIsNone(read(work, DAY))
        first_book(work, pieces.initial, pieces.original)
        invalid_target = replace(pieces.middle, evidence=replace(pieces.middle.evidence, retired=()))
        invalid_reading = replace(pieces.skip, rows=(replace(pieces.skip.rows[0], quantity=12),))
        for invalid in (encode_piece(invalid_target), encode_piece(invalid_reading),
                        b'{"version":1,"version":1}'):
            with self.assertRaises(AcceptanceError):
                accept(work, DAY, invalid)
            self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (1, 9))
        adjust(work, pieces.original, 1, PLUS_ONE)
        self.assertEqual(read(work, DAY).total, 10)

    def test_retry_after_reopen(self):
        source = self.source()
        pieces = build_shifts(source, True)
        source.close()
        work = self.branch()
        first_book(work, pieces.initial, pieces.original)
        before = seal(work, DAY)
        arm_commit_failure(work, 'accept')
        with self.assertRaisesRegex(AcceptanceError, 'FOREIGN KEY constraint failed'):
            take(work, pieces.middle)
        self.assertFalse(work.in_transaction)
        self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (1, 9))
        with self.assertRaisesRegex(AcceptanceError, 'base revision mismatch'):
            take(work, pieces.final)
        self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (1, 9))
        work.close()
        offline = self.connect('branch')
        self.assertEqual((read(offline, DAY).revision, read(offline, DAY).total), (1, 9))
        adjust(offline, pieces.original, 1, PLUS_ONE)
        allow_commit(offline)
        self.assertEqual(take(offline, pieces.middle), 3)
        self.assertEqual((read(offline, DAY).revision, read(offline, DAY).total), (3, 0))
        with self.assertRaises(AdjustmentRejected):
            adjust(offline, pieces.original, 1, ReadingOrigin('迟到核账单', 5))
        take(offline, pieces.final)
        offline.close()
        final = self.connect('branch')
        self.assertEqual(read(final, DAY).total, 11)
        self.assertEqual(retrieve(final, before).total, 9)

    def test_first_accept_commit_failure_leaves_no_book(self):
        original, piece = prepare_first(self.source())
        work = self.branch()
        arm_commit_failure(work, 'first_accept')
        with self.assertRaisesRegex(AcceptanceError, 'FOREIGN KEY constraint failed'):
            take(work, piece)
        self.assertIsNone(read(work, DAY))
        with self.assertRaises(AdjustmentRejected):
            adjust(work, original, 1, PLUS_ONE)
        allow_commit(work)
        self.assertEqual(take(work, piece), 1)
        self.assertEqual(read(work, DAY).total, 7)
