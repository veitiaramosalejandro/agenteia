import unittest
from uuid import uuid4

from app.agent.authorization import (
    VisibilityLevel,
    can_agent_access_message,
    normalize_visibility,
    resolve_resource_table,
)


class MessageAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.sender = str(uuid4())
        self.agent = str(uuid4())
        self.other = str(uuid4())
        self.table = [
            {"idResource": self.sender, "sequence": 0, "talkWithAgent": False},
            {"idResource": self.agent, "sequence": 2, "talkWithAgent": True},
            {"idResource": self.other, "sequence": 1, "talkWithAgent": False},
        ]

    def test_resolves_sender_and_agent_recipient_by_confirmed_contract(self):
        participants = resolve_resource_table(self.table)
        self.assertTrue(participants.valid)
        self.assertEqual(self.sender, participants.sender_resource_id)
        self.assertEqual((self.agent,), participants.agent_recipient_ids)

    def test_type_is_not_required_to_identify_roles(self):
        participants = resolve_resource_table(self.table)
        self.assertTrue(participants.valid)
        self.assertEqual((self.agent,), participants.agent_recipient_ids)

    def test_multiple_sequence_zero_senders_fail_closed(self):
        table = self.table + [
            {"idResource": str(uuid4()), "sequence": 0, "talkWithAgent": False}
        ]
        participants = resolve_resource_table(table)
        self.assertFalse(participants.valid)
        self.assertEqual("multiple_senders", participants.reason)

    def test_visibility_enum_contract(self):
        self.assertEqual(VisibilityLevel.PUBLIC, normalize_visibility(0))
        self.assertEqual(VisibilityLevel.NORMAL, normalize_visibility(1))
        self.assertEqual(VisibilityLevel.CONFIDENTIAL, normalize_visibility(2))
        self.assertEqual(VisibilityLevel.PRIVATE, normalize_visibility(3))

    def test_private_only_allows_explicit_participants(self):
        participants = resolve_resource_table(self.table)
        allowed, reason = can_agent_access_message(
            visibility_level=3,
            agent_resource_id=self.agent,
            participants=participants,
            belongs_to_channel=True,
            resource_access_type=3,
        )
        denied, _ = can_agent_access_message(
            visibility_level=3,
            agent_resource_id=self.other,
            participants=participants,
            belongs_to_channel=True,
            resource_access_type=3,
        )
        self.assertTrue(allowed)
        self.assertEqual("private_explicit_participant", reason)
        self.assertFalse(denied)

    def test_confidential_requires_channel_and_access_level(self):
        participants = resolve_resource_table(self.table)
        allowed, _ = can_agent_access_message(
            visibility_level=2,
            agent_resource_id=self.agent,
            participants=participants,
            belongs_to_channel=True,
            resource_access_type=2,
        )
        denied, _ = can_agent_access_message(
            visibility_level=2,
            agent_resource_id=self.agent,
            participants=participants,
            belongs_to_channel=True,
            resource_access_type=1,
        )
        self.assertTrue(allowed)
        self.assertFalse(denied)


if __name__ == "__main__":
    unittest.main()
