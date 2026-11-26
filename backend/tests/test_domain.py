import unittest
from app.services.domain import shares, decision


class FinanceRules(unittest.TestCase):
    def test_split_preserves_exact_vnd(self):
        self.assertEqual(shares(101, [8, 1, 3]), {1: 34, 3: 34, 8: 33})

    def test_reject_duplicate_participants(self):
        with self.assertRaises(ValueError):
            shares(101, [1, 1])

    def test_only_owner_may_approve(self):
        with self.assertRaises(PermissionError):
            decision(False, 'pending', True)

    def test_settlement_cannot_be_reversed(self):
        with self.assertRaises(ValueError):
            decision(True, 'paid', False)

    def test_approval_retries_are_idempotent(self):
        self.assertEqual(decision(True, 'paid', True), 'paid')
