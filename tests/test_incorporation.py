import json
import unittest
from dataclasses import replace

from archive.reconcile import reconcile_statement
from archive.retrieve import retrieve
from archive.seal import seal
from exchange.envelope import SupplyRow, decode_piece, encode_piece, inventory_digest
from exchange.issue import IssueRejected, issue, issue_v2
from exchange.issued_store import read_issued_piece
from intake.batches import ingest
from intake.observations import SampleCredential, SampleInput
from intake.origins import ReadingOrigin
from ledger.accept import AcceptanceError, accept
from ledger.adjustments import (AdjustmentRejected, adjust, configure_branch,
                                export_voucher, read_adjustments)
from ledger.book_store import initialize_book_store, read_book_state
from ledger.daily import read
from ledger.vouchers import AdjustmentVoucher
from quality.disposition import QualityOperation
from quality.revisions import InvalidQualityOperation, freeze, incorporate
from quality.snapshots import read_current_revision, read_snapshot
from samples.incorporation_demo import run_incorporation
from samples.incorporation_fixture import (CORRECTION_ORIGIN, INCORPORATION_ORIGIN,
                                           prepare, run_control)

from tests.support import (DAY, OTHER_DAY, PLUS_TWO, WorkdayCase, allow_commit,
                           arm_commit_failure, initialize_statement_store, operation,
                           prepare_first, take)


class IncorporationTests(WorkdayCase):
    def prepare_work_order(self, tag):
        source = self.connect(f'source-{tag}')
        branch_a = self.connect(f'branch-a-{tag}')
        branch_b = self.connect(f'branch-b-{tag}')
        day, credential, first, old_id, a_one, b_one = prepare(source, branch_a, branch_b)
        self.assertEqual(configure_branch(branch_a, '甲仓'), '甲仓')
        self.assertEqual(configure_branch(branch_b, '乙仓'), '乙仓')
        return source, branch_a, branch_b, day, credential, first, old_id, a_one, b_one

    def incorporate_chain(self, source, day, credential, first, voucher):
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        final = freeze(source, day, middle, [QualityOperation(
            credential, 'correct', '末班复测', 11, CORRECTION_ORIGIN,
        )])
        return middle, final

    def test_two_branch_chain(self):
        for style in ('step', 'skip'):
            with self.subTest(style=style):
                (source, branch_a, branch_b, day, credential, first, old_id,
                 a_one, b_one) = self.prepare_work_order(style)
                voucher = export_voucher(branch_a, a_one.adjustment_id)
                self.assertEqual(voucher, AdjustmentVoucher('甲仓', a_one.adjustment_id,
                                                            credential, 2, a_one.origin))
                middle, final = self.incorporate_chain(source, day, credential, first, voucher)
                self.assertEqual((middle, final), (first + 1, first + 2))
                snapshot = read_snapshot(source, day, middle)
                self.assertEqual([(row.quantity, row.origin) for row in snapshot.billable],
                                 [(9, INCORPORATION_ORIGIN)])
                self.assertEqual([item.action for item in snapshot.operations], ['incorporate'])
                # 旧式发行拒绝含并入事实的目标
                for base, target in ((first, middle), (middle, final), (first, final)):
                    with self.assertRaises(IssueRejected):
                        issue(source, day, base, target)
                step = [issue_v2(source, day, first, middle),
                        issue_v2(source, day, middle, final)]
                skip = issue_v2(source, day, first, final)
                self.assertEqual([piece.issue_id for piece in (*step, skip)], [2, 3, 4])
                for piece in (*step, skip):
                    payload = encode_piece(piece)
                    self.assertEqual(decode_piece(payload), piece)
                    self.assertEqual(encode_piece(read_issued_piece(source, piece.issue_id)),
                                     payload)
                source.close()
                deliveries = step if style == 'step' else (skip,)
                for piece in deliveries:
                    payload = encode_piece(piece)
                    self.assertEqual(accept(branch_a, day, payload), piece.target_revision)
                    self.assertEqual(accept(branch_b, day, payload), piece.target_revision)
                book_a, book_b = read(branch_a, day), read(branch_b, day)
                self.assertEqual((book_a.revision, book_b.revision), (final, final))
                self.assertEqual((book_a.total, book_b.total), (14, 13))
                line_a, = book_a.lines
                line_b, = book_b.lines
                self.assertEqual((line_a.row.quantity, line_a.row.origin),
                                 (11, CORRECTION_ORIGIN))
                self.assertEqual((line_b.row.quantity, line_b.row.origin),
                                 (11, CORRECTION_ORIGIN))
                # 明列仍加计的本地调整：甲仅 +3，乙仍加计自己的 +2
                self.assertEqual([item.delta for item in line_a.adjustments], [3])
                self.assertEqual([item.delta for item in line_b.adjustments], [2])
                # 两仓均可看到甲的并入凭单
                self.assertEqual(line_a.incorporated, (voucher,))
                self.assertEqual(line_b.incorporated, (voucher,))
                # 完整的本地原调整历史仍含已并入记录
                self.assertEqual([item.delta for item in read_adjustments(branch_a, credential)],
                                 [2, 3])
                self.assertEqual([item.delta for item in read_adjustments(branch_b, credential)],
                                 [2])
                # 甲原封存仍可领得 7 L 和 +2 L，合计 9 L
                old = retrieve(self.connect(f'branch-a-{style}'), old_id)
                old_line, = old.lines
                self.assertEqual((old_line.quantity, old.total), (7, 9))
                self.assertEqual([item.delta for item in old_line.adjustments], [2])
                self.assertEqual(old_line.incorporated, ())
                self.assertTrue(reconcile_statement(old).matches)
                # 新封存固定源读、出处、仍加计调整与并入凭单
                new_id = seal(branch_a, day)
                sealed = retrieve(self.connect(f'branch-a-{style}'), new_id)
                sealed_line, = sealed.lines
                self.assertEqual((sealed_line.quantity, sealed_line.origin, sealed.total),
                                 (11, CORRECTION_ORIGIN, 14))
                self.assertEqual([item.delta for item in sealed_line.adjustments], [3])
                self.assertEqual(sealed_line.incorporated, (voucher,))
                self.assertTrue(reconcile_statement(sealed).matches)

    def test_configure_branch(self):
        work = self.branch()
        for bad in ('', '   ', None, 7, '甲\x00仓'):
            with self.assertRaises(AdjustmentRejected):
                configure_branch(work, bad)
        self.assertEqual(configure_branch(work, '甲仓'), '甲仓')
        self.assertEqual(configure_branch(work, '甲仓'), '甲仓')
        with self.assertRaises(AdjustmentRejected):
            configure_branch(work, '乙仓')
        work.close()
        reopened = self.connect('branch')
        self.assertEqual(configure_branch(reopened, '甲仓'), '甲仓')
        with self.assertRaises(AdjustmentRejected):
            configure_branch(reopened, '丙仓')

    def test_export_voucher(self):
        source = self.source()
        credential, piece = prepare_first(source)
        work = self.branch()
        take(work, piece)
        saved = adjust(work, credential, 2, PLUS_TWO)
        with self.assertRaises(AdjustmentRejected):
            export_voucher(work, saved.adjustment_id)
        # 已有 V1 调整记录的分仓仍可配置
        self.assertEqual(configure_branch(work, '甲仓'), '甲仓')
        for unknown in (saved.adjustment_id + 10, 0, True, '1'):
            with self.assertRaises(AdjustmentRejected):
                export_voucher(work, unknown)
        before = read(work, DAY).total
        voucher = export_voucher(work, saved.adjustment_id)
        self.assertEqual(voucher, AdjustmentVoucher('甲仓', saved.adjustment_id,
                                                    credential, 2, PLUS_TWO))
        self.assertEqual(export_voucher(work, saved.adjustment_id), voucher)
        self.assertEqual(read(work, DAY).total, before)
        source.close()
        self.assertEqual(export_voucher(work, saved.adjustment_id), voucher)

    def test_incorporate_rejections(self):
        (source, branch_a, branch_b, day, credential, first, old_id,
         a_one, b_one) = self.prepare_work_order('reject')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        other, = ingest(source, day, '加测批', [SampleInput('Q', 5, 'L',
                                                          ReadingOrigin('加测单', 1))]).credentials
        foreign = SampleCredential(OTHER_DAY, 1, 1, 'K')
        attempts = [
            (first, other, voucher, INCORPORATION_ORIGIN),
            (first, foreign, replace(voucher, credential=foreign), INCORPORATION_ORIGIN),
            (first + 1, credential, voucher, INCORPORATION_ORIGIN),
            (0, credential, voucher, INCORPORATION_ORIGIN),
            (first, credential, replace(voucher, delta=True), INCORPORATION_ORIGIN),
            (first, credential, replace(voucher, branch_id=''), INCORPORATION_ORIGIN),
            (first, credential, 'voucher', INCORPORATION_ORIGIN),
            (first, credential, voucher, None),
        ]
        for base, cred, delivered, origin in attempts:
            with self.subTest(base=base, cred=cred, delivered=delivered, origin=origin):
                with self.assertRaises(InvalidQualityOperation):
                    incorporate(source, day, base, cred, delivered, origin)
        self.assertEqual(read_current_revision(source, day), first)
        self.assertIsNone(read_snapshot(source, day, first + 1))
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        self.assertEqual(middle, first + 1)
        with self.assertRaises(InvalidQualityOperation):
            incorporate(source, day, middle, credential, voucher, INCORPORATION_ORIGIN)
        self.assertEqual(read_current_revision(source, day), middle)

    def test_accept_v2_requires_configuration(self):
        (source, branch_a, branch_b, day, credential, first, old_id,
         a_one, b_one) = self.prepare_work_order('cfg')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        piece = issue_v2(source, day, first, middle)
        payload = encode_piece(piece)
        work = self.connect('plain')
        initialize_book_store(work)
        initialize_statement_store(work)
        accept(work, day, encode_piece(issue(source, day, 0, first)))
        with self.assertRaises(AcceptanceError):
            accept(work, day, payload)
        self.assertEqual(read(work, day).revision, first)
        # 另一仓的凭单在本仓可以没有对应登记
        configure_branch(work, '丙仓')
        self.assertEqual(accept(work, day, payload), middle)
        line, = read(work, day).lines
        self.assertEqual((line.row.quantity, line.total), (9, 9))
        self.assertEqual(line.incorporated, (voucher,))

    def test_own_voucher_must_match_local_record(self):
        for tag, forge in (
            ('delta', lambda voucher: replace(voucher, delta=5)),
            ('unknown', lambda voucher: replace(voucher,
                                                adjustment_id=voucher.adjustment_id + 100)),
        ):
            with self.subTest(tag=tag):
                (source, branch_a, branch_b, day, credential, first, old_id,
                 a_one, b_one) = self.prepare_work_order(tag)
                voucher = export_voucher(branch_a, a_one.adjustment_id)
                forged = forge(voucher)
                middle = incorporate(source, day, first, credential, forged,
                                     INCORPORATION_ORIGIN)
                payload = encode_piece(issue_v2(source, day, first, middle))
                with self.assertRaises(AcceptanceError):
                    accept(branch_a, day, payload)
                book = read(branch_a, day)
                self.assertEqual((book.revision, book.total), (first, 12))
                self.assertEqual(book.lines[0].incorporated, ())
                self.assertEqual(accept(branch_b, day, payload), middle)
                line, = read(branch_b, day).lines
                self.assertEqual(line.incorporated, (forged,))

    def test_cross_source_base_distinguishes_incorporation(self):
        correction = self.source('correction')
        incorporation = self.source('incorporation')
        cred_c, _ = prepare_first(correction)
        cred_i, _ = prepare_first(incorporation)
        self.assertEqual(cred_c, cred_i)
        freeze(correction, DAY, 1, [operation(cred_c, 'correct', 9, INCORPORATION_ORIGIN)])
        freeze(correction, DAY, 2, [operation(cred_c, 'correct', 11, CORRECTION_ORIGIN)])
        work = self.branch('work')
        configure_branch(work, '甲仓')
        take(work, issue(correction, DAY, 0, 1))
        saved = adjust(work, cred_c, 2, PLUS_TWO)
        voucher = export_voucher(work, saved.adjustment_id)
        middle = incorporate(incorporation, DAY, 1, cred_i, voucher, INCORPORATION_ORIGIN)
        freeze(incorporation, DAY, middle,
               [operation(cred_i, 'correct', 11, CORRECTION_ORIGIN)])
        piece_c = issue(correction, DAY, 1, 2)
        piece_i = issue_v2(incorporation, DAY, 1, 2)
        # 两库的 R2 旧库存摘要相同，并入口径不同
        self.assertEqual(piece_c.evidence.target_digest,
                         inventory_digest({cred_c}, (SupplyRow(cred_c, 9, INCORPORATION_ORIGIN),)))
        self.assertNotEqual(piece_i.evidence.target_digest, piece_c.evidence.target_digest)
        take(work, piece_c)
        self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (2, 11))
        # 采用普通更正 R2 的分仓必须拒绝并入源的 R2→R3 来件
        with self.assertRaises(AcceptanceError):
            take(work, issue_v2(incorporation, DAY, 2, 3))
        self.assertEqual((read(work, DAY).revision, read(work, DAY).total), (2, 11))
        # 反向：已含并入依据的在用账不再接纳 V1 来件
        work_i = self.branch('work_i')
        configure_branch(work_i, '甲仓')
        take(work_i, issue(incorporation, DAY, 0, 1))
        adjust(work_i, cred_i, 2, PLUS_TWO)
        take(work_i, piece_i)
        # 本仓 +2 即已并入的那笔，不再加计，正式源读 9 L 即合计
        self.assertEqual(read(work_i, DAY).total, 9)
        with self.assertRaises(AcceptanceError):
            take(work_i, issue(correction, DAY, 2, 3))
        self.assertEqual((read(work_i, DAY).revision, read(work_i, DAY).total), (2, 9))

    def test_v2_piece_validation(self):
        (source, branch_a, branch_b, day, credential, first, old_id,
         a_one, b_one) = self.prepare_work_order('tamper')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        piece = issue_v2(source, day, first, middle)
        payload = encode_piece(piece)
        tampered = replace(piece, evidence=replace(
            piece.evidence, incorporated=(replace(voucher, delta=5),)))
        duplicated = replace(piece, evidence=replace(
            piece.evidence, incorporated=(voucher, voucher)))
        foreign = replace(piece, evidence=replace(piece.evidence, incorporated=(
            replace(voucher, credential=SampleCredential(OTHER_DAY, 1, 1, 'K')),)))
        for bad in (encode_piece(tampered), encode_piece(duplicated), encode_piece(foreign),
                    payload[:-1], b'{"version":3}'):
            with self.assertRaises(AcceptanceError):
                accept(branch_a, day, bad)
        with self.assertRaises(AcceptanceError):
            accept(branch_a, OTHER_DAY, payload)
        book = read(branch_a, day)
        self.assertEqual((book.revision, book.total), (first, 12))
        self.assertEqual(book.lines[0].incorporated, ())
        self.assertEqual(accept(branch_a, day, payload), middle)
        line, = read(branch_a, day).lines
        self.assertEqual((line.row.quantity, line.row.origin), (9, INCORPORATION_ORIGIN))
        self.assertEqual([item.delta for item in line.adjustments], [3])
        self.assertEqual(read(branch_a, day).total, 12)

    def test_commit_failure_preserves_book(self):
        (source, branch_a, branch_b, day, credential, first, old_id,
         a_one, b_one) = self.prepare_work_order('commit')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        piece = issue_v2(source, day, first, middle)
        arm_commit_failure(branch_a, 'accept')
        with self.assertRaisesRegex(AcceptanceError, 'FOREIGN KEY constraint failed'):
            accept(branch_a, day, encode_piece(piece))
        self.assertFalse(branch_a.in_transaction)
        book = read(branch_a, day)
        self.assertEqual((book.revision, book.total), (first, 12))
        self.assertEqual(book.lines[0].incorporated, ())
        allow_commit(branch_a)
        self.assertEqual(accept(branch_a, day, encode_piece(piece)), middle)
        line, = read(branch_a, day).lines
        self.assertEqual((line.row.quantity, line.row.origin), (9, INCORPORATION_ORIGIN))
        self.assertEqual(line.incorporated, (voucher,))
        self.assertEqual([item.delta for item in line.adjustments], [3])
        self.assertEqual(read(branch_a, day).total, 12)

    def test_hold_and_release_keep_incorporation(self):
        (source, branch_a, branch_b, day, credential, first, old_id,
         a_one, b_one) = self.prepare_work_order('hold')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        held = freeze(source, day, middle, [QualityOperation(
            credential, 'hold', '白班暂扣单 QC-02')])
        released = freeze(source, day, held, [QualityOperation(
            credential, 'release', '晚班解除单 QC-03')])
        accept(branch_b, day, encode_piece(issue_v2(source, day, first, middle)))
        self.assertEqual(read(branch_b, day).total, 11)
        accept(branch_b, day, encode_piece(issue_v2(source, day, middle, held)))
        held_book = read(branch_b, day)
        self.assertEqual((held_book.lines, held_book.total), ((), 0))
        accept(branch_b, day, encode_piece(issue_v2(source, day, held, released)))
        line, = read(branch_b, day).lines
        self.assertEqual((line.row.quantity, line.row.origin), (9, INCORPORATION_ORIGIN))
        self.assertEqual(line.incorporated, (voucher,))
        self.assertEqual([item.delta for item in line.adjustments], [2])
        self.assertEqual(read(branch_b, day).total, 11)

    def test_withdrawal_keeps_voucher_with_original(self):
        (source, branch_a, branch_b, day, credential, first, old_id,
         a_one, b_one) = self.prepare_work_order('withdraw')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        withdrawn = freeze(source, day, middle, [QualityOperation(
            credential, 'withdraw', '撤样单 QC-04')])
        resample, = ingest(source, day, '补采批', [SampleInput(
            'K', 5, 'L', ReadingOrigin('补采单', 1))]).credentials
        final = freeze(source, day, withdrawn, [QualityOperation(
            resample, 'approve', '补采放行 QC-05')])
        accept(branch_b, day, encode_piece(issue_v2(source, day, first, final)))
        book = read(branch_b, day)
        line, = book.lines
        # 撤样后的同号补采有独立原样身份，不携带原样的并入凭单
        self.assertEqual(line.row.credential, resample)
        self.assertEqual((line.row.quantity, line.incorporated, line.total), (5, (), 5))
        # 已冻结的凭单事实仍归原样，留在账册并入依据中
        self.assertEqual(read_book_state(branch_b, day).incorporated, (voucher,))

    def test_legacy_statement_without_incorporation(self):
        source = self.source()
        credential, piece = prepare_first(source)
        work = self.branch()
        take(work, piece)
        adjust(work, credential, 2, PLUS_TWO)
        # 模拟旧版五字段封存记录（无并入凭单列），旧封存继续适用
        legacy = json.dumps([[['清湾北站', '2025-04-16', 1, 1, 'K'], '0x7', ['站务原单', 1],
                              [[1, ['清湾北站', '2025-04-16', 1, 1, 'K'], '0x2',
                                ['早班核账单', 2]]], '0x9']])
        work.execute('''
            INSERT INTO closed_statements(station, day, revision, lines, total)
            VALUES (?, ?, ?, ?, ?)
        ''', (*DAY.key, 1, legacy, '0x9'))
        work.commit()
        statement = retrieve(work, 1)
        line, = statement.lines
        self.assertEqual((line.quantity, line.total, line.incorporated), (7, 9, ()))
        self.assertEqual([item.delta for item in line.adjustments], [2])
        self.assertTrue(reconcile_statement(statement).matches)

    def test_ordinary_correction_control(self):        self.assertEqual(run_control(), {
            'equal_local_adjustments': True, 'revision': 3,
            'source_readings': [11, 11], 'totals': [16, 13], 'old_statement': 9,
        })

    def test_offline_demo(self):
        result = run_incorporation(self.directory)
        self.assertEqual(result['totals'], [14, 13])
        self.assertEqual(result['source_readings'], [11, 11])
        self.assertEqual(result['local_adjustments'], [[3], [2]])
        self.assertEqual(result['incorporated'], [[2], [2]])
        self.assertEqual(result['incorporated_from'], [['甲仓'], ['甲仓']])
        self.assertEqual(result['old_statement'],
                         {'id': 1, 'quantity': 7, 'adjustments': [2], 'total': 9})
        self.assertEqual(result['reconciled'], [9, 14, 13])


if __name__ == '__main__':
    unittest.main()
