import unittest

from replay import CASES, INTENT, SCHEMA, TABLE, run_case, verdict


class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {case.name: run_case(case) for case in CASES}

    def check_order(self, run, *required):
        names = iter(event["event"] for event in run["events"])
        for event in required:
            self.assertIn(event, names)

    def test_before_create_worlds_leave_no_cloud_resource(self):
        pending = self.runs["pending-write-ack-lost-stall"]
        forwarded = self.runs["create-never-forwarded-stall"]
        self.assertEqual(pending["cut"], forwarded["cut"])
        self.assertEqual(pending["cut"], {"phase": "pending", "external_id": None, "inventory": []})
        self.check_order(pending, "pending_committed", "ack_withheld", "oracle_at_cut", "worker_killed")
        self.check_order(forwarded, "create_requested", "ack_withheld", "worker_killed", "create_discarded")
        for run in (pending, forwarded):
            self.assertEqual(run["verdict"], "blocked-before-external-create")
            self.assertNotIn("create_committed", [event["event"] for event in run["events"]])

    def test_lost_create_ack_has_same_controller_view_but_different_cloud_state(self):
        absent = self.runs["create-never-forwarded-stall"]
        committed = self.runs["create-committed-stall"]
        for field in ("phase", "external_id"):
            self.assertEqual(absent["cut"][field], committed["cut"][field])
        self.assertEqual(len(committed["cut"]["inventory"]), 1)
        self.assertEqual(committed["verdict"], "blocked-with-external-resource")
        self.check_order(committed, "create_committed", "ack_withheld", "oracle_at_cut", "worker_killed")

    def test_named_resource_converges_after_every_fault(self):
        for name in (
            "normal", "pending-write-ack-lost-recover", "create-never-forwarded-recover",
            "create-committed-adopt", "create-committed-hidden-read"
        ):
            with self.subTest(case=name):
                run = self.runs[name]
                self.assertEqual(run["final"]["inventory"], [{"id": TABLE, "name": TABLE, "schema": SCHEMA, "intent": INTENT}])
                self.assertEqual(run["verdict"], "converged")
                self.assertEqual(sum(e["event"] == "create_committed" for e in run["events"]), 1)

    def test_stale_read_checks_identity_after_conflict(self):
        run = self.runs["create-committed-hidden-read"]
        self.check_order(
            run, "worker_killed", "describe_hidden_committed_resource", "create_conflict",
            "describe", "ready_committed"
        )
        self.assertEqual(run["worker_result"], "observed-after-conflict")

    def test_opaque_id_guard_prevents_second_commit_and_oracle_catches_blind_retry(self):
        guarded = self.runs["opaque-id-safeguard"]
        blind = self.runs["opaque-id-blind-retry"]
        self.assertEqual(guarded["cut"], blind["cut"])
        self.assertEqual(guarded["verdict"], "blocked-with-external-resource")
        self.assertEqual(len(guarded["final"]["inventory"]), 1)
        self.assertEqual(blind["final"]["phase"], "ready")
        self.assertEqual(blind["verdict"], "extra-resource")
        self.assertEqual(len(blind["final"]["inventory"]), 2)

    def test_same_name_and_schema_do_not_prove_ownership(self):
        run = self.runs["same-name-foreign-resource"]
        self.assertEqual(run["verdict"], "blocked-with-foreign-resource")
        self.assertEqual(run["worker_result"], "blocked-unverified")
        self.check_order(run, "worker_killed", "foreign_resource_committed", "describe")
        self.assertIsNone(run["final"]["external_id"])

    def test_oracle_rejects_ready_for_missing_or_wrong_schema(self):
        state = {"phase": "ready", "external_id": TABLE, "inventory": []}
        self.assertEqual(verdict(state), "false-ready")
        state["inventory"].append({"id": TABLE, "name": TABLE, "schema": "wrong", "intent": INTENT})
        self.assertEqual(verdict(state), "false-ready")


if __name__ == "__main__":
    unittest.main()
