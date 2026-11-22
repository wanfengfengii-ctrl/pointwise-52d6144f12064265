from exchange.issue import issue
from exchange.issued_store import read_issued_piece
from intake.batch_store import read_batch, read_latest_observation
from intake.batches import ingest
from intake.origins import ReadingOrigin
from intake.validation import BatchValidationError
from quality.revisions import InvalidQualityOperation, freeze
from quality.snapshots import read_current_revision, read_snapshot

from tests.support import (DAY, ORIGINAL, OTHER_DAY, WorkdayCase, initialize_source,
                           operation, prepare_first, sample)


class SourceTests(WorkdayCase):
    def test_original_order(self):
        source = self.source()
        second_origin = ReadingOrigin('原单二', 11)
        receipt = ingest(source, DAY, '双行批', [sample('Z'), sample('A', 0, origin=second_origin)])
        z, a = receipt.credentials
        revision = freeze(source, DAY, 0, [operation(z, 'approve'), operation(a, 'approve'),
                                           operation(a, 'hold')])
        original = read_batch(source, DAY, '双行批')
        self.assertEqual([(row.credential.number, row.quantity, row.origin) for row in original],
                         [('Z', 7, ORIGINAL), ('A', 0, second_origin)])
        self.assertEqual([row.credential.order for row in original], [(1, 1), (1, 2)])
        self.assertEqual([row.credential for row in read_snapshot(source, DAY, revision).billable], [z])
        later, = ingest(source, DAY, '后一批', [sample('B', 3)]).credentials
        next_revision = freeze(source, DAY, revision, [operation(later, 'approve')])
        self.assertEqual([(row.credential.number, row.credential.order)
                          for row in issue(source, DAY, 0, next_revision).rows],
                         [('Z', (1, 1)), ('B', (2, 1))])

    def test_frozen_history(self):
        source = self.source()
        z, a = ingest(source, DAY, '具名双行', [sample('Z', 12), sample('A', 5)]).credentials
        r1 = freeze(source, DAY, 0, [operation(z, 'approve'), operation(a, 'approve')])
        r2 = freeze(source, DAY, r1, [operation(a, 'hold')])
        r3 = freeze(source, DAY, r2, [operation(a, 'release', 8, ReadingOrigin('更正单', 2))])
        first, held, restored = [read_snapshot(source, DAY, version) for version in (r1, r2, r3)]
        self.assertEqual([row.quantity for row in first.billable], [12, 5])
        self.assertEqual([row.quantity for row in held.billable], [12])
        self.assertEqual([row.quantity for row in restored.billable], [12, 8])
        self.assertEqual(held.operations, (operation(a, 'hold'),))
        self.assertEqual(restored.billable[1].origin, ReadingOrigin('更正单', 2))
        self.assertEqual(restored.billable[1].observation.quantity, 5)
        self.assertEqual(restored.billable[1].observation.origin, ORIGINAL)

    def test_invalid_operation(self):
        source = self.source()
        own, _ = prepare_first(source)
        foreign, _ = prepare_first(source, OTHER_DAY)
        old = read_snapshot(source, DAY, 1)
        with self.assertRaises(InvalidQualityOperation):
            freeze(source, DAY, 1, [operation(own, 'hold'), operation(foreign, 'hold')])
        with self.assertRaises(InvalidQualityOperation):
            freeze(source, DAY, 0, [operation(own, 'hold')])
        with self.assertRaises(InvalidQualityOperation):
            freeze(source, DAY, 1, [operation(own, 'correct', True, ORIGINAL)])
        self.assertEqual(read_current_revision(source, DAY), 1)
        self.assertIsNone(read_snapshot(source, DAY, 2))
        self.assertEqual(read_snapshot(source, DAY, 1), old)
        self.assertEqual(read_current_revision(source, OTHER_DAY), 1)

    def assert_batch_rejected(self, invalid):
        source = self.source()
        with self.assertRaises(BatchValidationError):
            ingest(source, DAY, '拒收双行', [sample('前行'), invalid])
        source.close()
        reopened = self.connect('source')
        self.assertIsNone(read_batch(reopened, DAY, '拒收双行'))
        self.assertIsNone(read_latest_observation(reopened, DAY, '前行'))
        self.assertIsNone(read_latest_observation(reopened, DAY, invalid.number))
        self.assertIsNone(read_current_revision(reopened, DAY))

    def test_invalid_unit(self):
        self.assert_batch_rejected(sample('后行', 42, unit='mL'))

    def test_invalid_origin(self):
        self.assert_batch_rejected(sample('后行', 42, origin=None))

    def test_invalid_integer(self):
        self.assert_batch_rejected(sample('后行', True))

    def test_held_number_stays_reserved_and_withdrawal_releases_it(self):
        source = self.source()
        old, _ = prepare_first(source)
        freeze(source, DAY, 1, [operation(old, 'hold')])
        with self.assertRaises(BatchValidationError):
            ingest(source, DAY, '非法补采', [sample('Z', 5), sample()])
        self.assertIsNone(read_latest_observation(source, DAY, 'Z'))
        freeze(source, DAY, 2, [operation(old, 'withdraw')])
        newest, = ingest(source, DAY, '补采', [sample(quantity=11)]).credentials
        self.assertEqual(newest.order, (2, 1))
        self.assertEqual(newest.number, old.number)
        self.assertEqual(read_batch(source, DAY, '早班实收')[0].credential, old)
        with self.assertRaises(InvalidQualityOperation):
            freeze(source, DAY, 3, [operation(old, 'release')])
        self.assertEqual(read_current_revision(source, DAY), 3)

    def test_reinitialize(self):
        source = self.source()
        first, second = ingest(source, DAY, '持久双行', [sample('Z'), sample('A', 0)]).credentials
        r1 = freeze(source, DAY, 0, [operation(first, 'approve'), operation(second, 'approve')])
        frozen = read_snapshot(source, DAY, r1)
        piece = issue(source, DAY, 0, r1)
        initialize_source(source)
        source.close()
        reopened = self.source()
        original = read_batch(reopened, DAY, '持久双行')
        self.assertEqual([(row.credential, row.quantity, row.origin) for row in original],
                         [(first, 7, ORIGINAL), (second, 0, ORIGINAL)])
        self.assertEqual(read_snapshot(reopened, DAY, 0).dispositions, ())
        self.assertEqual(read_current_revision(reopened, DAY), 1)
        self.assertEqual(read_snapshot(reopened, DAY, r1), frozen)
        self.assertEqual(read_issued_piece(reopened, piece.issue_id), piece)
