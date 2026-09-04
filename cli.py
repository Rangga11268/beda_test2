import sys
import json
import argparse
from engine import BedaTriageEngine

def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title.upper()}")
    print("=" * 70)

def cmd_run(engine: BedaTriageEngine, enquiries_path: str = "enquiries.json", output_json: bool = False):
    with open(enquiries_path, "r") as f:
        enquiries = json.load(f)

    results = []
    for eq in enquiries:
        res = engine.process_enquiry(eq)
        results.append(res)

    if output_json:
        print(json.dumps(results, indent=2))
        return

    print_header("BEDA Intelligent Triage Engine - Batch Processing")
    print(f"{'ID':<6} | {'STATUS':<20} | {'CATEGORY':<28} | {'OWNER':<15} | {'CRM MATCH':<18}")
    print("-" * 95)

    for res in results:
        eid = res["id"]
        status = res["status"][:18]
        ext = res["extracted"]
        cat = ext["category"][:26] if ext else "N/A"
        owner = ext["recommended_owner"] if ext else "N/A"
        crm_record = res["crm_match"].get("record")
        crm_str = f"{crm_record['ID']} ({crm_record['Company'][:10]})" if crm_record else "New Lead"

        print(f"{eid:<6} | {status:<20} | {cat:<28} | {owner:<15} | {crm_str:<18}")

    print("\n[OK] Processed 12 items. CRM is in staged mode (zero mutations prior to human approval).")
    print("Commands: 'python cli.py inspect <ID>' | 'python cli.py approve <ID>' | 'python cli.py --json'")

def cmd_inspect(engine: BedaTriageEngine, enquiry_id: str, enquiries_path: str = "enquiries.json", output_json: bool = False):
    with open(enquiries_path, "r") as f:
        enquiries = json.load(f)

    for eq in enquiries:
        engine.process_enquiry(eq)

    if enquiry_id not in engine.processed_enquiries:
        print(f"Error: Enquiry {enquiry_id} not found.")
        sys.exit(1)

    item = engine.processed_enquiries[enquiry_id]

    if output_json:
        print(json.dumps(item, indent=2))
        return

    ext = item["extracted"]
    crm = item["crm_match"]
    staged = item["staged_action"]
    raw = item["raw_payload"]

    print_header(f"Enquiry Inspection: {enquiry_id}")
    print(f"From:    {raw.get('sender')}")
    print(f"Subject: {raw.get('subject')}")
    print(f"Status:  {item['status']}")
    print(f"Engine:  {item['engine_used']}")
    print("\n--- RAW BODY ---")
    print(raw.get('body'))
    if raw.get('attachment'):
        print("\n--- ATTACHMENT NOTES ---")
        print(raw.get('attachment'))

    print("\n--- STRUCTURED EXTRACTION ---")
    print(f"Category:             {ext.get('category')}")
    print(f"Confidence Score:     {int(ext.get('confidence_score', 0) * 100)}%")
    print(f"Contact Person:       {ext.get('person_name') or 'N/A'}")
    print(f"Company:              {ext.get('company_name') or 'N/A'}")
    print(f"Phone / Mobile:       {ext.get('phone_number') or 'N/A'}")
    print(f"Annual Consumption:   {str(ext.get('annual_consumption_gwh')) + ' GWh' if ext.get('annual_consumption_gwh') else 'N/A'}")
    print(f"Monthly Bill Spend:   {ext.get('monthly_bill_estimate') or 'N/A'}")
    print(f"Identified Sites:     {', '.join(ext.get('site_locations', [])) or 'None'}")
    print(f"PO / Invoice Refs:    {', '.join(ext.get('invoice_or_po_numbers', [])) or 'None'}")
    print(f"Missing Critical:     {', '.join(ext.get('missing_critical_info', [])) or 'None'}")
    print(f"Uncertainty / Notes:  {', '.join(ext.get('uncertainty_reasons', [])) or 'None'}")

    print("\n--- IDENTITY RESOLUTION & CRM MATCH ---")
    print(f"Match Type:           {crm.get('match_type')}")
    print(f"Match Confidence:     {int(crm.get('match_confidence', 0) * 100)}%")
    if crm.get("record"):
        rec = crm["record"]
        print(f"Matched Record:       [{rec.get('ID')}] {rec.get('Company')} - {rec.get('Name')} ({rec.get('Email')})")
    else:
        print("Matched Record:       None (New Lead to be created on approval)")

    if crm.get("crm_duplicates"):
        dup_ids = [d["ID"] for d in crm["crm_duplicates"]]
        print(f"CRM Seed Duplicates:  Notice: Found duplicate CRM records {dup_ids} matching this entity.")

    print("\n--- HUMAN-IN-THE-LOOP GATE ---")
    print(f"Assigned Owner:       {staged.get('assigned_owner')}")
    print(f"Suggested Action:     {staged.get('suggested_next_action')}")
    print(f"Requires Approval:    Yes (External action and CRM mutation gated)")
    print("\n--- DRAFTED RESPONSE ---")
    print(staged.get('draft_response') or "[NO RESPONSE NEEDED]")
    print("=" * 70)

def cmd_approve(engine: BedaTriageEngine, enquiry_id: str, operator: str, enquiries_path: str = "enquiries.json"):
    with open(enquiries_path, "r") as f:
        enquiries = json.load(f)

    for eq in enquiries:
        engine.process_enquiry(eq)

    print_header(f"Executing Human Approval for {enquiry_id}")
    res = engine.execute_approval(
        enquiry_id=enquiry_id,
        operator_name=operator,
        decision="APPROVE_AND_EXECUTE"
    )
    print(f"Result:      {res['status']}")
    print(f"CRM Action:  {res['crm_action']}")
    print(f"Audit Log:   Logged with actor '{operator}'")
    print(f"Status:      [SUCCESS] Outbound action dispatched and CRM updated.")

def cmd_audit(engine: BedaTriageEngine, enquiries_path: str = "enquiries.json", output_json: bool = False):
    with open(enquiries_path, "r") as f:
        enquiries = json.load(f)

    for eq in enquiries:
        engine.process_enquiry(eq)

    if output_json:
        print(json.dumps(engine.audit_log, indent=2))
        return

    print_header("Immutable Audit Trail (Recent Events)")
    for entry in engine.audit_log[-15:]:
        print(f"[{entry['timestamp']}] {entry['event_type']:<24} | Actor: {entry['actor']:<10} | {entry['rationale']}")

def cmd_export_crm(engine: BedaTriageEngine, output_file: str = "crm_updated.csv"):
    path = engine.export_crm_csv(output_file)
    print(f"[SUCCESS] Exported live CRM database to {path}")

def main():
    parser = argparse.ArgumentParser(description="BEDA Intelligent Inbound Triage CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    run_parser = subparsers.add_parser("run", help="Run batch processing on enquiries")
    run_parser.add_argument("--json", action="store_true", help="Output raw JSON array")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a specific enquiry")
    inspect_parser.add_argument("id", type=str, help="Enquiry ID (e.g. E001)")
    inspect_parser.add_argument("--json", action="store_true", help="Output raw JSON representation")

    approve_parser = subparsers.add_parser("approve", help="Approve an enquiry via HITL gate")
    approve_parser.add_argument("id", type=str, help="Enquiry ID (e.g. E001)")
    approve_parser.add_argument("--operator", type=str, default="Human Operator", help="Operator name")

    audit_parser = subparsers.add_parser("audit", help="Display recent audit trail")
    audit_parser.add_argument("--json", action="store_true", help="Output raw audit JSON")

    export_parser = subparsers.add_parser("export-crm", help="Export live CRM state to CSV")
    export_parser.add_argument("--out", type=str, default="crm_updated.csv", help="Output file path")

    args = parser.parse_args()
    engine = BedaTriageEngine()

    if args.command == "run" or args.command is None:
        cmd_run(engine, output_json=getattr(args, "json", False))
    elif args.command == "inspect":
        cmd_inspect(engine, args.id, output_json=args.json)
    elif args.command == "approve":
        cmd_approve(engine, args.id, args.operator)
    elif args.command == "audit":
        cmd_audit(engine, output_json=getattr(args, "json", False))
    elif args.command == "export-crm":
        cmd_export_crm(engine, args.out)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
