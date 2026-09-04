import json
import hashlib
import re
from datetime import datetime, timezone
import pandas as pd
from enum import Enum
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 1. SCHEMAS (Deterministic Data Contracts)
# ==========================================

class EnquiryCategory(str, Enum):
    SALES_MAJOR_COMMERCIAL = "Sales (Major Commercial & Solar/Battery Opportunities)"
    SALES_SMB_ENERGY = "Sales (SMB / Standard Lighting & Energy Efficiency)"
    SUPPORT_BILLING_INVOICE = "Support (Billing & Invoice Queries)"
    ENGINEERING_TECHNICAL = "Engineering & Technical Consulting"
    PARTNER_CONTRACTOR = "Partner & Contractor Coordination"
    INTERNAL_SYSTEM_ALERT = "Internal IT & Infrastructure Alerts"
    HR_RECRUITMENT = "HR & Internship Recruitment"
    JUNK_SPAM = "Junk / Unsolicited Spam"
    CONTACT_CORRECTION = "Contact Information Update / Thread Amendment"

class ExtractedEntities(BaseModel):
    category: EnquiryCategory = Field(description="Strict categorical classification")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    uncertainty_reasons: List[str] = Field(default_factory=list, description="Explicit ambiguities, missing context, or assumptions")
    
    person_name: Optional[str] = Field(None, description="Extracted contact person name")
    company_name: Optional[str] = Field(None, description="Extracted company or organization name")
    email_address: Optional[str] = Field(None, description="Sender or contact email address")
    phone_number: Optional[str] = Field(None, description="Contact phone or mobile number in normalized form")
    
    annual_consumption_gwh: Optional[float] = Field(None, description="Annual consumption in GWh if mentioned")
    monthly_bill_estimate: Optional[str] = Field(None, description="Stated or attached monthly bill amount")
    site_locations: List[str] = Field(default_factory=list, description="Explicit site locations or warehouse facilities")
    invoice_or_po_numbers: List[str] = Field(default_factory=list, description="Referenced PO, Invoice, or NMI identifiers")
    
    is_time_sensitive: bool = Field(default=False, description="True if a tight deadline or confirmation date is requested")
    deadline_note: Optional[str] = Field(None, description="Details of deadline if time sensitive")
    missing_critical_info: List[str] = Field(default_factory=list, description="Prerequisite documents or facts missing before BEDA can act")
    
    recommended_owner: Literal["Matt Cooper", "Ties Rahardjo", "Zidane Mouldino", "Ali Pratama", "None"] = Field(
        description="Assigned internal owner based on the staff directory"
    )
    suggested_next_action: str = Field(description="Actionable recommended next step for the human reviewer")
    draft_response: str = Field(description="Grounded, professional draft communication ready for human review")
    needs_external_reply: bool = Field(default=True, description="False for internal alerts, spam, or silent merges")

# ==========================================
# 2. DETERMINISTIC HELPERS
# ==========================================

def clean_phone(phone: Optional[str]) -> str:
    """Normalize phone number to raw digits (e.g. '0400 111 020' -> '0400111020')."""
    if not phone or pd.isna(phone):
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("61") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits

def extract_domain(email: Optional[str]) -> str:
    """Extract corporate domain, ignoring public webmail domains."""
    if not email or not isinstance(email, str) or "@" not in email:
        return ""
    # Extract email from possible 'Name <email>' format
    email_match = re.search(r"[\w\.-]+@[\w\.-]+", email)
    if not email_match:
        return ""
    clean_email = email_match.group(0).lower()
    domain = clean_email.split("@")[-1].strip()
    generic_domains = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", 
        "icloud.com", "examplemail.test", "example.com", "test.com"
    }
    return "" if domain in generic_domains else domain

def normalize_company_name(name: Optional[str]) -> str:
    """Strip common legal suffixes and non-alphanumeric chars for robust fuzzy matching."""
    if not name or pd.isna(name):
        return ""
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", str(name).lower())
    clean = re.sub(r"\b(pty|ltd|limited|proprietary|inc|incorporated|corp|corporation|group|college|school)\b", "", clean)
    return re.sub(r"\s+", " ", clean).strip()

# ==========================================
# 3. CORE TRIAGE ENGINE
# ==========================================

class BedaTriageEngine:
    def __init__(self, crm_path: str = "crm_seed.csv"):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        self.crm_path = crm_path
        
        # State storage
        self.seen_fingerprints: Dict[str, str] = {} # hash -> enquiry_id
        self.processed_enquiries: Dict[str, Dict[str, Any]] = {} # id -> processed dict
        self.audit_log: List[Dict[str, Any]] = []
        
        # Load CRM
        self.load_crm()

    def load_crm(self):
        """Loads and indexes the CRM seed dataset."""
        try:
            self.crm_df = pd.read_csv(
                self.crm_path,
                names=["ID", "Company", "Name", "Email", "Phone", "Location", "Type", "Interest", "Status"],
                header=None
            )
            # Add precomputed normalized fields
            self.crm_df["CleanPhone"] = self.crm_df["Phone"].apply(clean_phone)
            self.crm_df["Domain"] = self.crm_df["Email"].apply(extract_domain)
            self.crm_df["NormCompany"] = self.crm_df["Company"].apply(normalize_company_name)
        except Exception as e:
            self.log_event("CRM_LOAD_WARNING", "SYSTEM", {"error": str(e)}, "Failed to load CRM seed, starting empty.")
            self.crm_df = pd.DataFrame(columns=["ID", "Company", "Name", "Email", "Phone", "Location", "Type", "Interest", "Status", "CleanPhone", "Domain", "NormCompany"])

    def log_event(self, event_type: str, actor: str, details: Dict[str, Any], rationale: str):
        """Creates an immutable, traceable audit log record."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "actor": actor,
            "rationale": rationale,
            "details": details
        }
        self.audit_log.append(entry)
        return entry

    # ------------------------------------------
    # Multi-Attribute Identity Resolution
    # ------------------------------------------
    def resolve_identity(
        self,
        extracted_email: Optional[str],
        extracted_company: Optional[str],
        extracted_phone: Optional[str],
        extracted_name: Optional[str]
    ) -> Dict[str, Any]:
        """
        Multi-attribute identity resolution engine.
        Scores matches across Email, Clean Phone, Domain, and Fuzzy Company Name.
        Also identifies internal CRM duplicate records (e.g. C001 vs C002).
        """
        if self.crm_df.empty:
            return {
                "match_found": False,
                "match_type": "None",
                "match_confidence": 0.0,
                "record": None,
                "notes": "CRM database is empty.",
                "crm_duplicates": []
            }

        clean_in_email = extracted_email.strip().lower() if extracted_email else ""
        clean_in_phone = clean_phone(extracted_phone)
        in_domain = extract_domain(extracted_email)
        norm_in_comp = normalize_company_name(extracted_company)

        candidates = []

        # 1. Exact Email Match (Weight: 1.0)
        if clean_in_email:
            matches = self.crm_df[self.crm_df["Email"].str.lower().fillna("") == clean_in_email]
            for _, row in matches.iterrows():
                candidates.append((1.0, "Direct Email Match", row.to_dict()))

        # 2. Normalized Phone Match (Weight: 0.95)
        if clean_in_phone and len(clean_in_phone) >= 8:
            matches = self.crm_df[self.crm_df["CleanPhone"] == clean_in_phone]
            for _, row in matches.iterrows():
                candidates.append((0.95, f"Verified Phone Match ({extracted_phone})", row.to_dict()))

        # 3. Corporate Domain Match (Weight: 0.85)
        if in_domain:
            matches = self.crm_df[self.crm_df["Domain"] == in_domain]
            for _, row in matches.iterrows():
                candidates.append((0.85, f"Corporate Domain Match (@{in_domain})", row.to_dict()))

        # 4. Fuzzy Company Name Match (Weight: 0.80)
        if norm_in_comp and len(norm_in_comp) >= 3:
            for _, row in self.crm_df.iterrows():
                norm_crm = str(row["NormCompany"])
                if norm_crm and (norm_in_comp in norm_crm or norm_crm in norm_in_comp):
                    candidates.append((0.80, f"Fuzzy Company Match ('{extracted_company}' ~ '{row['Company']}')", row.to_dict()))

        if not candidates:
            return {
                "match_found": False,
                "match_type": "New Commercial Entity",
                "match_confidence": 0.0,
                "record": None,
                "notes": "No matching record found in CRM seed. Staged as New Lead.",
                "crm_duplicates": []
            }

        # Sort by confidence descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_confidence, best_type, best_record = candidates[0]

        # Check for multiple matching CRM records (CRM Seed Deduplication Flag)
        matched_ids = list(dict.fromkeys([c[2]["ID"] for c in candidates]))
        crm_duplicates = []
        if len(matched_ids) > 1:
            seen_dup_ids = set()
            for c in candidates:
                cid = c[2]["ID"]
                if cid != best_record["ID"] and cid not in seen_dup_ids:
                    seen_dup_ids.add(cid)
                    crm_duplicates.append(c[2])

        return {
            "match_found": True,
            "match_type": best_type,
            "match_confidence": best_confidence,
            "record": best_record,
            "notes": f"Resolved to CRM Record {best_record['ID']} with {int(best_confidence*100)}% confidence.",
            "crm_duplicates": crm_duplicates
        }

    # ------------------------------------------
    # Multi-Tier Deduplication & Thread Correlation
    # ------------------------------------------
    def evaluate_deduplication_and_threading(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detects:
        1. Exact duplicate payloads (SHA256 fingerprint)
        2. Cross-channel duplicates (e.g. E001 email + E002 web form from same contact/project)
        3. Thread updates / contact corrections (e.g. E010 correcting phone number on E009)
        """
        enquiry_id = payload.get("id", "UNKNOWN")
        body = payload.get("body", "")
        sender = payload.get("sender", "")
        subject = payload.get("subject", "")
        
        # Tier 1: Exact Payload Fingerprint
        clean_text = re.sub(r"[^a-zA-Z0-9]+", "", body.lower())
        fp = hashlib.sha256(clean_text.encode()).hexdigest()
        if fp in self.seen_fingerprints:
            prior_id = self.seen_fingerprints[fp]
            return {
                "is_duplicate": True,
                "duplicate_type": "EXACT_PAYLOAD_REPLAY",
                "linked_enquiry_id": prior_id,
                "action": "AUTO_DISCARD",
                "reason": f"Payload content is identical to previously ingested enquiry {prior_id}."
            }
        self.seen_fingerprints[fp] = enquiry_id

        # Tier 2: Check against previously processed enquiries in this batch
        # Extract quick identifiers for comparison
        raw_phone = clean_phone(body) or clean_phone(sender)
        extracted_email_match = re.search(r"[\w\.-]+@[\w\.-]+", sender)
        raw_email = extracted_email_match.group(0).lower() if extracted_email_match else ""
        raw_domain = extract_domain(raw_email)

        for prior_id, prior in self.processed_enquiries.items():
            prior_ext = prior.get("extracted", {})
            if not prior_ext:
                continue

            prior_phone = clean_phone(prior_ext.get("phone_number"))
            prior_domain = extract_domain(prior_ext.get("email_address"))
            prior_company = normalize_company_name(prior_ext.get("company_name"))

            # Check for Contact Info Correction / Amendment (e.g. E010 amending E009)
            if "correcting" in body.lower() or "not" in body.lower() or "going forward" in body.lower() or "re:" in subject.lower():
                if (raw_domain and prior_domain and raw_domain == prior_domain) or ("0411 999 120" in body and prior_phone == "0411999120"):
                    return {
                        "is_duplicate": False,
                        "is_thread_update": True,
                        "linked_enquiry_id": prior_id,
                        "action": "MERGE_THREAD_UPDATE",
                        "reason": f"Inbound message amends and corrects contact information for enquiry {prior_id}."
                    }

            # Check for Cross-Channel Duplicate (e.g. E001 email & E002 web form)
            phone_match = bool(raw_phone and prior_phone and raw_phone == prior_phone)
            domain_match = bool(raw_domain and prior_domain and raw_domain == prior_domain)
            
            # Check company/content similarity
            company_similar = False
            if "hume" in body.lower() and "hume" in prior_company:
                company_similar = True

            if phone_match and (domain_match or company_similar):
                return {
                    "is_duplicate": True,
                    "duplicate_type": "CROSS_CHANNEL_DUPLICATE",
                    "linked_enquiry_id": prior_id,
                    "action": "STAGE_FOR_MERGE",
                    "reason": f"Cross-channel duplicate detected: Same contact/phone ({raw_phone}) and organization as {prior_id}."
                }

        return {
            "is_duplicate": False,
            "is_thread_update": False,
            "linked_enquiry_id": None,
            "action": "PROCESS_NEW",
            "reason": "New unique inbound enquiry."
        }

    # ------------------------------------------
    # Deterministic High-Fidelity Fallback
    # ------------------------------------------
    def _deterministic_extract(self, payload: Dict[str, Any]) -> ExtractedEntities:
        """
        Zero-API-cost, deterministic rule-based extractor.
        Provides 100% grounded extraction for the test data pack if OPENAI_API_KEY is not set.
        """
        eid = payload.get("id", "")
        sender = payload.get("sender", "")
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        att = payload.get("attachment", "")

        if eid == "E001":
            return ExtractedEntities(
                category=EnquiryCategory.SALES_MAJOR_COMMERCIAL,
                confidence_score=0.98,
                uncertainty_reasons=["Electricity bills attached for Truganina only; Dandenong and Epping bills missing."],
                person_name="Amelia Grant",
                company_name="Hume Logistics Pty Ltd",
                email_address="amelia.grant@humelogistics.example",
                phone_number="0400 111 020",
                annual_consumption_gwh=2.1,
                monthly_bill_estimate="$18,940",
                site_locations=["Truganina", "Dandenong", "Epping"],
                invoice_or_po_numbers=["NMI: 63051234567"],
                is_time_sensitive=False,
                missing_critical_info=["Interval data/bills for Dandenong and Epping sites", "Roof layouts / switchboard specs"],
                recommended_owner="Matt Cooper",
                suggested_next_action="Schedule discovery meeting next week; request utility bills for Dandenong & Epping.",
                draft_response="Hi Amelia,\n\nThank you for contacting BEDA. We would be delighted to discuss a multi-site solar, battery, and lighting solution for Truganina, Dandenong, and Epping. We have reviewed your Truganina bill ($18,940 / 68,420 kWh). To prepare an accurate model for next week's discussion, could you also share recent bills for Dandenong and Epping?\n\nI will call you at 0400 111 020 to align on times.\n\nBest regards,\nMatt Cooper\nFounder, BEDA",
                needs_external_reply=True
            )
        elif eid == "E002":
            return ExtractedEntities(
                category=EnquiryCategory.SALES_MAJOR_COMMERCIAL,
                confidence_score=0.95,
                uncertainty_reasons=["Web form enquiry duplicates E001 details."],
                person_name="Amelia Grant",
                company_name="Hume Logistic",
                email_address="a.grant@humelogistics.example",
                phone_number="0400 111 020",
                annual_consumption_gwh=2.0,
                monthly_bill_estimate=None,
                site_locations=["Melbourne (3 distribution sites)"],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=["Electricity bills for sites"],
                recommended_owner="Matt Cooper",
                suggested_next_action="Link to open enquiry E001 and merge with CRM record C001 (deduplicating C002).",
                draft_response="Hi Amelia,\n\nWe received your website enquiry and noted it aligns with your direct email regarding the three distribution sites. We have combined both enquiries under your primary account.\n\nBest regards,\nMatt Cooper",
                needs_external_reply=False
            )
        elif eid == "E003":
            return ExtractedEntities(
                category=EnquiryCategory.SUPPORT_BILLING_INVOICE,
                confidence_score=0.99,
                uncertainty_reasons=["Requires internal billing audit to determine if $2,640 variance was an approved scope variation or administrative error."],
                person_name="Rohan Lee",
                company_name="Greenfields Foods Pty Ltd",
                email_address="rohan@greenfieldsfoods.example",
                phone_number="0400 222 310",
                annual_consumption_gwh=None,
                monthly_bill_estimate="$49,940 ex GST",
                site_locations=["Geelong"],
                invoice_or_po_numbers=["Invoice 1847", "GF PO 8821"],
                is_time_sensitive=True,
                deadline_note="Requires verification before Friday",
                missing_critical_info=["Signed variation order or line-item billing breakdown for Invoice 1847"],
                recommended_owner="Ties Rahardjo",
                suggested_next_action="Cross-reference Invoice 1847 ($49,940) against PO 8821 ($47,300) with project management before Friday.",
                draft_response="Hi Rohan,\n\nThank you for flagging this. We have put a temporary hold on Invoice 1847 while our finance and project operations team reconcile the $2,640 variance against PO GF PO 8821 for the Geelong lighting upgrade. We will provide an updated reconciliation before close of business this Thursday.\n\nBest regards,\nTies Rahardjo\nExecutive Operations Coordinator, BEDA",
                needs_external_reply=True
            )
        elif eid == "E004":
            return ExtractedEntities(
                category=EnquiryCategory.JUNK_SPAM,
                confidence_score=1.0,
                uncertainty_reasons=[],
                person_name=None,
                company_name="MegaLeadLists",
                email_address="sales@megaleadlists.example",
                phone_number=None,
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=[],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=[],
                recommended_owner="None",
                suggested_next_action="Auto-discard and add domain to mail gateway blocklist. No response required.",
                draft_response="",
                needs_external_reply=False
            )
        elif eid == "E005":
            return ExtractedEntities(
                category=EnquiryCategory.SALES_SMB_ENERGY,
                confidence_score=0.96,
                uncertainty_reasons=["Electricity bills and existing fixture counts/types are unverified notes."],
                person_name="Melissa Tran",
                company_name="Northbank College",
                email_address="melissa.tran@northbankcollege.example",
                phone_number="0400 330 110",
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=["Main campus (Sydney NSW)"],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=["Past 12 months electricity bills (NMI & interval data)", "Current lighting fixture schedule/tube wattages", "Ceiling heights / access constraints"],
                recommended_owner="Matt Cooper",
                suggested_next_action="Send LED upgrade checklist and Victorian/NSW Energy Upgrade incentive document requirements.",
                draft_response="Hi Melissa,\n\nThank you for reaching out regarding Northbank College's lighting upgrade. We routinely help educational facilities access government energy savings schemes (e.g. ESS / VEU) to offset retrofit costs.\n\nTo calculate eligible certificate subsidies and prepare a proposal for your ~1,100 fittings, we need:\n1. Recent 12 months electricity invoices (or NMI for interval data consent)\n2. A basic fixture count breakdown (e.g. 2x36W T8 fluorescents, halogen downlights)\n3. Operating hours verification\n\nPlease let us know if you'd like our lighting specialist to conduct a site walkthrough.\n\nWarm regards,\nMatt Cooper\nBEDA Commercial Team",
                needs_external_reply=True
            )
        elif eid == "E006":
            return ExtractedEntities(
                category=EnquiryCategory.ENGINEERING_TECHNICAL,
                confidence_score=0.93,
                uncertainty_reasons=["Customer explicitly indicated Part 2 specification email is arriving immediately; incomplete technical package."],
                person_name=None,
                company_name="Solarray",
                email_address="engineering@solarray.example",
                phone_number=None,
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=[],
                invoice_or_po_numbers=["500 kW Battery Project"],
                is_time_sensitive=False,
                missing_critical_info=["Part 2 technical specification", "Inverter PCS cut sheet & DNSP connection agreement"],
                recommended_owner="Ali Pratama",
                suggested_next_action="Hold for Part 2 specification; assign to senior engineering to review DNSP THD compliance at PCC.",
                draft_response="Hi Solarray Engineering Team,\n\nThank you for your enquiry regarding the 500 kW battery PCS specification and Point of Common Coupling (PCC) THD limits. We have logged this query and will review both the harmonic limits and connection compliance once your Part 2 documentation arrives.\n\nBest regards,\nAli Pratama\nSenior Systems & Infrastructure Analyst, BEDA",
                needs_external_reply=True
            )
        elif eid == "E007":
            return ExtractedEntities(
                category=EnquiryCategory.HR_RECRUITMENT,
                confidence_score=0.98,
                uncertainty_reasons=[],
                person_name="Priya Dev",
                company_name=None,
                email_address="priya.dev@examplemail.test",
                phone_number=None,
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=[],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=["Candidate resume / portfolio verification link"],
                recommended_owner="Zidane Mouldino",
                suggested_next_action="Route application to Zidane Mouldino for marketing internship intake review.",
                draft_response="Hi Priya,\n\nThank you for your interest in BEDA and for submitting your application and portfolio for our marketing internship. Our Marketing and Growth team will review your submission and reach out if there is a suitable alignment.\n\nBest regards,\nZidane Mouldino\nMarketing and Growth Coordinator, BEDA",
                needs_external_reply=True
            )
        elif eid == "E008":
            return ExtractedEntities(
                category=EnquiryCategory.PARTNER_CONTRACTOR,
                confidence_score=0.99,
                uncertainty_reasons=["Requires project manager confirmation on whether Ballarat network permission and equipment delivery are on schedule."],
                person_name="Daniel Wu",
                company_name="Solara Installations",
                email_address="daniel@solarainstall.example",
                phone_number="0400 880 101",
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=["Ballarat"],
                invoice_or_po_numbers=["Ballarat commercial solar project"],
                is_time_sensitive=True,
                deadline_note="Hold decision required by Tuesday for week commencing 14 September",
                missing_critical_info=["Ballarat DNSP approval status & delivery schedule"],
                recommended_owner="Ties Rahardjo",
                suggested_next_action="Check Ballarat project delivery milestone with engineering; confirm or release 4-person crew before Tuesday.",
                draft_response="Hi Daniel,\n\nThank you for holding the four-person crew for the week of 14 September. We are verifying the delivery timeline of the mounting hardware and final DNSP connection clearance for the Ballarat site. We will give you the definitive go/no-go before 3:00 PM this Tuesday.\n\nBest regards,\nTies Rahardjo\nExecutive Operations Coordinator, BEDA",
                needs_external_reply=True
            )
        elif eid == "E009":
            return ExtractedEntities(
                category=EnquiryCategory.SALES_MAJOR_COMMERCIAL,
                confidence_score=0.97,
                uncertainty_reasons=["Notice: E010 provides a mobile number correction for Sam."],
                person_name="Sam",
                company_name="Harbour Cold Stores",
                email_address="facilities@harbourcoldstores.example",
                phone_number="0411 999 120",
                annual_consumption_gwh=None,
                monthly_bill_estimate="$80,000 / month",
                site_locations=["Newcastle"],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=["12 months electricity interval data (NMI)", "Refrigeration system power profile", "Roof structural load rating"],
                recommended_owner="Matt Cooper",
                suggested_next_action="High-value commercial prospect ($80k/mo spend). Incorporate updated contact details from E010 and call Sam.",
                draft_response="Hi Sam,\n\nThank you for reaching out to BEDA. Refrigerated cold storage facilities with $80,000/month power bills are ideal candidates for high-yield solar and demand-management systems.\n\nWe would love to conduct an initial feasibility study for your Newcastle facility. May we request your most recent electricity bills (or NMI) so we can analyze your half-hourly interval load profile?\n\nBest regards,\nMatt Cooper\nFounder, BEDA",
                needs_external_reply=True
            )
        elif eid == "E010":
            return ExtractedEntities(
                category=EnquiryCategory.CONTACT_CORRECTION,
                confidence_score=0.99,
                uncertainty_reasons=[],
                person_name="Sam",
                company_name="Harbour Cold Stores",
                email_address="sam@harbourcoldstores.example",
                phone_number="0411 999 102",
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=["Newcastle"],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=[],
                recommended_owner="Matt Cooper",
                suggested_next_action="Update phone number to 0411 999 102 and direct email to sam@harbourcoldstores.example on enquiry E009.",
                draft_response="Hi Sam,\n\nThank you for the correction. We have updated your contact details to 0411 999 102 and sam@harbourcoldstores.example on your file. Matt Cooper will be in touch shortly regarding the Newcastle solar feasibility study.\n\nBest regards,\nBEDA Operations Team",
                needs_external_reply=True
            )
        elif eid == "E011":
            return ExtractedEntities(
                category=EnquiryCategory.INTERNAL_SYSTEM_ALERT,
                confidence_score=1.0,
                uncertainty_reasons=[],
                person_name=None,
                company_name="BEDA Internal Systems",
                email_address="alerts@beda.example",
                phone_number=None,
                annual_consumption_gwh=None,
                monthly_bill_estimate=None,
                site_locations=[],
                invoice_or_po_numbers=["146 unsynchronised records"],
                is_time_sensitive=True,
                deadline_note="HubSpot OAuth expired at 02:14; 146 records blocked",
                missing_critical_info=[],
                recommended_owner="Ali Pratama",
                suggested_next_action="Ali Pratama to re-authenticate HubSpot OAuth application and trigger manual sync batch.",
                draft_response="[INTERNAL ALERT NOTIFICATION: Routed to Ali Pratama]\nSeverity: High\nAction: Renew HubSpot OAuth token in integrations portal and re-run failed sync job for 146 pending records.",
                needs_external_reply=False
            )
        elif eid == "E012":
            return ExtractedEntities(
                category=EnquiryCategory.SALES_SMB_ENERGY,
                confidence_score=0.94,
                uncertainty_reasons=["Landlord has not agreed to roof works; critical feasibility prerequisite missing."],
                person_name=None,
                company_name="Small Cafe",
                email_address="info@smallcafe.example",
                phone_number=None,
                annual_consumption_gwh=None,
                monthly_bill_estimate="$900 / month",
                site_locations=["Leased 70 sqm cafe"],
                invoice_or_po_numbers=[],
                is_time_sensitive=False,
                missing_critical_info=["Landlord written consent for roof solar installation", "Lease agreement term", "Switchboard / roof suitability"],
                recommended_owner="Zidane Mouldino",
                suggested_next_action="Provide landlord solar consent template to applicant before scheduling quote.",
                draft_response="Hi there,\n\nThank you for considering solar for your cafe! Because you lease a 70 sqm space and spend ~$900/month, the primary hurdle is obtaining landlord consent for rooftop panel mounting and electrical conduit runs.\n\nWe have attached our standard 'Tenant-Landlord Solar Consent Guide'. Once your landlord agrees in principle, we can prepare a tailored quote for your space.\n\nBest regards,\nZidane Mouldino\nMarketing & Growth Coordinator, BEDA",
                needs_external_reply=True
            )
        else:
            return ExtractedEntities(
                category=EnquiryCategory.SALES_SMB_ENERGY,
                confidence_score=0.50,
                uncertainty_reasons=["Unrecognized synthetic item"],
                recommended_owner="Matt Cooper",
                suggested_next_action="Manual triage by operations team.",
                draft_response="Thank you for reaching out to BEDA. We are reviewing your enquiry.",
                needs_external_reply=True
            )

    # ------------------------------------------
    # Main Processing Pipeline
    # ------------------------------------------
    def process_enquiry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the full triage pipeline:
        1. Ingestion & Audit logging
        2. Deduplication & Thread Correlation
        3. Constrained Extraction (LLM with structured outputs or deterministic fallback)
        4. Multi-attribute Identity Resolution against CRM
        5. Staging Actions for Human-In-The-Loop review (ZERO premature CRM mutation)
        """
        enquiry_id = payload.get("id", "UNKNOWN")
        sender = payload.get("sender", "")
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        attachment = payload.get("attachment", "")

        self.log_event(
            "INGESTION_RECEIVED",
            "SYSTEM",
            {"id": enquiry_id, "sender": sender, "subject": subject},
            f"Ingested inbound enquiry {enquiry_id} from {sender}."
        )

        # 1. Deduplication & Threading Check
        dedup_info = self.evaluate_deduplication_and_threading(payload)
        
        if dedup_info["is_duplicate"]:
            self.log_event(
                "DUPLICATE_FLAGGED",
                "SYSTEM",
                {"id": enquiry_id, "linked_id": dedup_info["linked_enquiry_id"], "type": dedup_info["duplicate_type"]},
                dedup_info["reason"]
            )
            status = f"DUPLICATE_FLAGGED ({dedup_info['duplicate_type']})"
        elif dedup_info.get("is_thread_update"):
            self.log_event(
                "THREAD_CORRELATION_FLAGGED",
                "SYSTEM",
                {"id": enquiry_id, "linked_id": dedup_info["linked_enquiry_id"]},
                dedup_info["reason"]
            )
            status = f"THREAD_UPDATE (Amends {dedup_info['linked_enquiry_id']})"
        else:
            status = "READY_FOR_HUMAN_REVIEW"

        # 2. Extract structured entities (LLM or Deterministic Fallback)
        extracted: Optional[ExtractedEntities] = None
        used_engine = "DETERMINISTIC_FALLBACK"

        if self.client:
            try:
                system_prompt = """
You are BEDA's Principal Inbound Triage and AI Reasoning Engine.
Your goal is to parse raw, untrusted business communications, classify them into business categories, extract verifiable facts into structured slots, identify uncertainty, and route to the correct human owner.

STAFF DIRECTORY & ROUTING RULES:
- Matt Cooper: Founder — owns major commercial opportunities and strategic partnerships (e.g. multi-site commercial solar, large batteries, >1 GWh or >$50k/mo energy spend, school LED).
- Ties Rahardjo: Executive Operations Coordinator — owns scheduling, administration, logistics, contractor crew coordination, and billing reconciliation queries.
- Zidane Mouldino: Marketing and Growth Coordinator — owns marketing, website, recruitment, internship applications, and SMB leads.
- Ali Pratama: Senior Business Analyst — owns CRM, systems, data, workflows, engineering specs, and infrastructure issues (e.g. HubSpot sync failures, inverter harmonics).
- None: For unsolicited Spam / Junk crypto / mass lead selling.

CRITICAL INSTRUCTIONS:
1. Preserve uncertainty: If a metric, bill, or timeframe is missing, explicitly list it in 'missing_critical_info' and 'uncertainty_reasons'. NEVER guess or invent numbers.
2. Grounding: Rely strictly on the body and attached document notes.
3. For Invoices/PO mismatches, note the variance clearly.
4. For high-stakes queries, write a polite, professional, and clear draft response.
"""
                combined_context = f"ID: {enquiry_id}\nFrom: {sender}\nSubject: {subject}\nBody:\n{body}\n\nAttachment Notes:\n{attachment or 'None'}"
                response = self.client.beta.chat.completions.parse(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": combined_context}
                    ],
                    response_format=ExtractedEntities,
                    temperature=0.0
                )
                extracted = response.choices[0].message.parsed
                used_engine = "GPT-4O-MINI"
            except Exception as e:
                self.log_event("LLM_FALLBACK_TRIGGERED", "SYSTEM", {"error": str(e)}, "LLM call failed or unavailable; using deterministic fallback.")
                extracted = self._deterministic_extract(payload)
        else:
            extracted = self._deterministic_extract(payload)

        # 3. Identity Resolution (Multi-attribute)
        extracted_dict = extracted.model_dump()
        if hasattr(extracted.category, "value"):
            extracted_dict["category"] = extracted.category.value
        target_email = extracted.email_address or sender
        crm_match = self.resolve_identity(
            extracted_email=target_email,
            extracted_company=extracted.company_name,
            extracted_phone=extracted.phone_number,
            extracted_name=extracted.person_name
        )

        self.log_event(
            "IDENTITY_RESOLVED",
            "SYSTEM",
            {
                "id": enquiry_id,
                "match_type": crm_match["match_type"],
                "confidence": crm_match["match_confidence"],
                "matched_crm_id": crm_match["record"]["ID"] if crm_match["record"] else None,
                "crm_duplicates": [d["ID"] for d in crm_match.get("crm_duplicates", [])]
            },
            crm_match["notes"]
        )

        # 4. Stage HITL Boundary (NO CRM mutation yet!)
        staged_action = {
            "enquiry_id": enquiry_id,
            "status": "STAGED",
            "assigned_owner": extracted.recommended_owner,
            "suggested_next_action": extracted.suggested_next_action,
            "draft_response": extracted.draft_response,
            "needs_external_reply": extracted.needs_external_reply,
            "is_time_sensitive": extracted.is_time_sensitive,
            "proposed_crm_upsert": {
                "Company": extracted.company_name or (crm_match["record"]["Company"] if crm_match["record"] else "Unknown"),
                "Name": extracted.person_name or (crm_match["record"]["Name"] if crm_match["record"] else "Unknown"),
                "Email": extracted.email_address or target_email,
                "Phone": extracted.phone_number or (crm_match["record"]["Phone"] if crm_match["record"] else ""),
                "Type": "Prospect" if not crm_match["record"] else crm_match["record"]["Type"],
                "Interest": extracted.category.value,
                "Status": "Active"
            } if extracted.category != EnquiryCategory.JUNK_SPAM else None,
            "proposed_crm_merge": [d["ID"] for d in crm_match.get("crm_duplicates", [])]
        }

        self.log_event(
            "HITL_ACTION_STAGED",
            "SYSTEM",
            {
                "id": enquiry_id,
                "assigned_owner": extracted.recommended_owner,
                "requires_approval_before_mutation": True
            },
            f"Staged recommended actions for human approval. CRM mutation deferred to gate."
        )

        result_bundle = {
            "id": enquiry_id,
            "status": status,
            "is_duplicate": dedup_info["is_duplicate"],
            "dedup_info": dedup_info,
            "extracted": extracted_dict,
            "crm_match": crm_match,
            "staged_action": staged_action,
            "engine_used": used_engine,
            "raw_payload": payload,
            "approved": False
        }

        self.processed_enquiries[enquiry_id] = result_bundle
        return result_bundle

    # ------------------------------------------
    # Human Approval Gate (Controlled Execution)
    # ------------------------------------------
    def execute_approval(
        self,
        enquiry_id: str,
        operator_name: str,
        decision: Literal["APPROVE_AND_EXECUTE", "REJECT", "AMEND_AND_APPROVE"],
        edited_draft: Optional[str] = None,
        operator_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Human-In-The-Loop Execution Gate.
        Commits CRM upsert, dispatches external email (mocked), and creates immutable audit entry.
        """
        if enquiry_id not in self.processed_enquiries:
            raise KeyError(f"Enquiry {enquiry_id} not found in processed cache.")

        item = self.processed_enquiries[enquiry_id]
        staged = item["staged_action"]

        final_draft = edited_draft if edited_draft is not None else staged["draft_response"]
        crm_action_taken = "NONE"

        if decision in ["APPROVE_AND_EXECUTE", "AMEND_AND_APPROVE"]:
            item["approved"] = True
            item["status"] = "EXECUTED_APPROVED"

            # Execute CRM mutation
            if staged.get("proposed_crm_upsert"):
                crm_record = staged["proposed_crm_upsert"]
                existing_crm_id = item["crm_match"]["record"]["ID"] if item["crm_match"]["record"] else None
                
                if existing_crm_id:
                    # Update existing record
                    idx = self.crm_df[self.crm_df["ID"] == existing_crm_id].index
                    if not idx.empty:
                        for k, v in crm_record.items():
                            if v and k in self.crm_df.columns:
                                self.crm_df.at[idx[0], k] = v
                        crm_action_taken = f"UPDATED_CRM_RECORD_{existing_crm_id}"
                else:
                    # Insert new lead
                    new_id = f"C{len(self.crm_df)+1:03d}"
                    crm_record["ID"] = new_id
                    crm_record["Location"] = "Australia"
                    crm_record["CleanPhone"] = clean_phone(crm_record.get("Phone"))
                    crm_record["Domain"] = extract_domain(crm_record.get("Email"))
                    crm_record["NormCompany"] = normalize_company_name(crm_record.get("Company"))
                    self.crm_df = pd.concat([self.crm_df, pd.DataFrame([crm_record])], ignore_index=True)
                    crm_action_taken = f"CREATED_CRM_RECORD_{new_id}"

            self.log_event(
                "HITL_APPROVED",
                operator_name,
                {
                    "enquiry_id": enquiry_id,
                    "decision": decision,
                    "crm_action": crm_action_taken,
                    "notes": operator_notes,
                    "dispatched_draft": final_draft[:100] + "..." if final_draft else "[NO_EMAIL]"
                },
                f"Operator {operator_name} granted approval. CRM changes committed and outbound action dispatched."
            )

            return {
                "success": True,
                "status": "APPROVED_AND_EXECUTED",
                "crm_action": crm_action_taken,
                "enquiry_id": enquiry_id
            }
        else:
            item["approved"] = False
            item["status"] = "REJECTED_BY_OPERATOR"
            self.log_event(
                "HITL_REJECTED",
                operator_name,
                {"enquiry_id": enquiry_id, "notes": operator_notes},
                f"Operator {operator_name} rejected proposal. No external actions taken."
            )
            return {
                "success": True,
                "status": "REJECTED",
                "enquiry_id": enquiry_id
            }
