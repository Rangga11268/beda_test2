import json
import unittest
import pandas as pd
from engine import BedaTriageEngine, EnquiryCategory

class TestBedaTriageSystem(unittest.TestCase):
    def setUp(self):
        self.engine = BedaTriageEngine(crm_path="crm_seed.csv")
        with open("enquiries.json", "r") as f:
            self.enquiries = json.load(f)

    def test_all_items_ingested_and_processed(self):
        """Verify all 12 synthetic items process successfully without exceptions."""
        results = [self.engine.process_enquiry(e) for e in self.enquiries]
        self.assertEqual(len(results), 12)
        for r in results:
            self.assertIn("id", r)
            self.assertIn("status", r)
            self.assertIn("extracted", r)
            self.assertIn("crm_match", r)
            self.assertIn("staged_action", r)

    def test_deduplication_and_threading(self):
        """Verify E002 is caught as duplicate of E001 and E010 links to E009."""
        # Process sequentially
        r_e001 = self.engine.process_enquiry(self.enquiries[0]) # E001
        r_e002 = self.engine.process_enquiry(self.enquiries[1]) # E002
        
        self.assertFalse(r_e001["is_duplicate"])
        self.assertTrue(r_e002["is_duplicate"], "E002 should be flagged as duplicate of E001")
        self.assertEqual(r_e002["dedup_info"]["linked_enquiry_id"], "E001")

        # Process up to E010
        for eq in self.enquiries[2:9]: # E003 to E009
            self.engine.process_enquiry(eq)
        
        r_e010 = self.engine.process_enquiry(self.enquiries[9]) # E010
        self.assertTrue(r_e010["dedup_info"]["is_thread_update"], "E010 should be recognized as thread update to E009")
        self.assertEqual(r_e010["dedup_info"]["linked_enquiry_id"], "E009")

    def test_multi_attribute_identity_resolution(self):
        """Verify multi-attribute matching across Email, Phone, and Domain."""
        # E001 matches C001 and identifies C002 duplicate in CRM
        res = self.engine.resolve_identity(
            extracted_email="amelia.grant@humelogistics.example",
            extracted_company="Hume Logistics Pty Ltd",
            extracted_phone="0400 111 020",
            extracted_name="Amelia Grant"
        )
        self.assertTrue(res["match_found"])
        self.assertEqual(res["record"]["ID"], "C001")
        self.assertGreaterEqual(res["match_confidence"], 0.95)
        # Should detect C002 as a duplicate CRM record
        dup_ids = [d["ID"] for d in res.get("crm_duplicates", [])]
        self.assertIn("C002", dup_ids, "Should detect C002 as an internal CRM duplicate of C001")

        # Phone-only match test (e.g. unknown email, but phone matches Rohan Lee)
        phone_res = self.engine.resolve_identity(
            extracted_email="unregistered@domain.test",
            extracted_company="Unknown",
            extracted_phone="0400 222 310",
            extracted_name="Rohan"
        )
        self.assertTrue(phone_res["match_found"])
        self.assertEqual(phone_res["record"]["ID"], "C003")

    def test_strict_hitl_boundary_no_premature_crm_mutation(self):
        """Ensure CRM is NOT mutated before human approval is explicitly executed."""
        initial_crm_count = len(self.engine.crm_df)
        
        # Process an enquiry with a new prospect (E009)
        e009_payload = next(e for e in self.enquiries if e["id"] == "E009")
        res = self.engine.process_enquiry(e009_payload)
        
        # Verify CRM count has NOT changed before approval
        self.assertEqual(len(self.engine.crm_df), initial_crm_count, "CRM count must NOT change before HITL approval gate")
        self.assertFalse(res["approved"])

        # Execute approval
        approval_result = self.engine.execute_approval(
            enquiry_id="E009",
            operator_name="Matt Cooper",
            decision="APPROVE_AND_EXECUTE",
            edited_draft="Approved draft for Newcastle cold storage."
        )
        self.assertTrue(approval_result["success"])
        # Now CRM count should have incremented
        self.assertEqual(len(self.engine.crm_df), initial_crm_count + 1, "CRM must be updated after HITL approval")

    def test_audit_trail_integrity(self):
        """Verify immutable audit log records all pipeline actions."""
        self.engine.process_enquiry(self.enquiries[0])
        self.assertGreater(len(self.engine.audit_log), 0)
        last_event = self.engine.audit_log[-1]
        self.assertIn("timestamp", last_event)
        self.assertIn("event_type", last_event)
        self.assertIn("actor", last_event)
        self.assertIn("rationale", last_event)

if __name__ == "__main__":
    unittest.main()
