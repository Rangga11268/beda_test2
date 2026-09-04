# BEDA Intelligent Inbound Triage & HITL Gateway (Test 2)

A production-grade, deterministic-first system for ingesting untrusted business communications, classifying intent, extracting structured facts with uncertainty preservation, running multi-attribute identity resolution, and staging actions behind a strict Human-in-the-Loop (HITL) approval boundary.

---

## ⚡ Quick Start

### 1. Prerequisites
- Python 3.10+
- (Optional) OpenAI API Key — *Note: The system includes a zero-cost deterministic fallback engine that runs 100% offline out-of-the-box even without an API key!*

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. (Optional) Configure OpenAI Key
If you wish to test with live GPT-4o-mini:
Create a `.env` file or export your key:
```bash
# Windows PowerShell
$env:OPENAI_API_KEY="sk-your-openai-api-key"
```

### 4. Run the System

#### Option A: Interactive Web UI (Streamlit)
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501`.

#### Option B: Headless Terminal CLI
```bash
# Process all 12 synthetic items and display summary table
python cli.py run

# Deeply inspect an enquiry (extraction, CRM match, uncertainty, draft)
python cli.py inspect E001

# Execute human approval for an item
python cli.py approve E001 --operator "Matt Cooper"

# Inspect immutable audit trail
python cli.py audit
```

#### Option C: Run Automated Test Suite
```bash
python test_triage.py
```
*(Runs 5 comprehensive unit tests validating deduplication, identity resolution, HITL boundaries, and audit logging in < 0.2s)*.

---

## 🏗️ Architecture & Direct Response to Test 1 Deductions

In Test 1, the assessor commended the clear architecture and least-privilege design, but noted three specific areas for deduction:
1. *CRM upsert occurring before the stated review gate.*
2. *Identity resolution relying too heavily on normalized email.*
3. *Enrichment and deduplication assumptions needing more care in production.*

This Test 2 build was architected from the ground up to solve these three root challenges:

```
[Untrusted Inbound Payload (Email / Web Form / Attachments)]
                         │
                         ▼
           [Tier 1 & 2 Deduplication Gate]
         ├── Exact SHA256 Payload Hash (Replays)
         └── Cross-Channel Duplication (e.g. E001 email vs E002 web form)
                         │
                         ▼
        [Constrained Entity & Intent Extraction]
   (Pydantic-enforced gpt-4o-mini OR High-Fidelity Fallback)
         ├── Business Category Classification
         ├── Numerical Metric Extraction (GWh, kW, Bills)
         └── Uncertainty & Missing Critical Info Preservation
                         │
                         ▼
       [Multi-Attribute Identity Resolution Engine]
         ├── 1. Exact Email Match (Weight: 1.0)
         ├── 2. Normalized E.164 Phone Match (Weight: 0.95)
         ├── 3. Corporate Domain Match (Weight: 0.85)
         ├── 4. Fuzzy Company Name Match (Weight: 0.80)
         └── 5. Seed CRM Duplicate Detection (C001 vs C002)
                         │
                         ▼
        [STRICT HUMAN-IN-THE-LOOP (HITL) GATE]
   *** ZERO CRM MUTATION / ZERO EXTERNAL ACTION HERE ***
         ├── Staged Proposed CRM Upsert / Merge
         ├── Grounded Draft Response
         └── Operational Routing (Matt / Ties / Zidane / Ali)
                         │
        [Human Reviewer Approves & Executes]
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
 [Committed CRM Upsert]         [Outbound Dispatch]
         │                               │
         └───────────────┬───────────────┘
                         ▼
            [Immutable Audit Trail]
```

### 1. Review Gate & CRM Upsert Decoupling
* **Test 1 Deduction:** CRM upsert occurred before human review.
* **Test 2 Fix:** The engine strictly decouples **Staging** from **Execution**. When `process_enquiry()` runs, it prepares a `staged_action` dictionary with proposed CRM deltas and drafted responses. **The CRM database is never mutated until `execute_approval()` is invoked.**

### 2. Multi-Attribute Identity Resolution
* **Test 1 Deduction:** Identity resolution relied too heavily on normalized email.
* **Test 2 Fix:** Implemented a weighted multi-attribute matching pipeline in `engine.py`:
  - **Direct Email Match (1.0 confidence)**: Exact case-insensitive email match.
  - **Normalized Clean Phone Match (0.95 confidence)**: Strips non-digits and normalizes Australian mobile/landline numbers (`0400 111 020` == `0400111020`), resolving contacts even if they submit from a different email address.
  - **Corporate Domain Match (0.85 confidence)**: Extracts company email domain (filtering out consumer webmail like Gmail, Yahoo, Hotmail).
  - **Fuzzy Company Name Match (0.80 confidence)**: Strips common legal entities (`Pty Ltd`, `Ltd`, `Inc`) and compares alphanumeric sequences.
  - **CRM Duplicate Recognition**: Automatically flags existing duplicates in the CRM seed (e.g. `C001` vs `C002` both representing Hume Logistics) and suggests a merge upon approval.

### 3. Production-Grade Deduplication & Thread Correlation
* **Test 1 Deduction:** Naive deduplication assumptions fail on cross-channel inquiries or minor variations.
* **Test 2 Fix:**
  - **Tier 1 (Exact Content Replay):** SHA256 fingerprint on cleaned body content detects identical resends instantly with zero LLM API cost.
  - **Tier 2 (Cross-Channel Duplicate Detection):** Correlates sender phone number, contact name, and project scope across channels. Correctly identifies `E002` (web form) as a duplicate of `E001` (email) from Amelia Grant / Hume Logistics.
  - **Tier 3 (Thread & Correction Correlation):** Detects contact information corrections. Correctly correlates `E010` (Sam correcting mobile number to `0411 999 102`) with `E009` (`harbourcoldstores.example`) and updates the staged lead rather than creating an orphaned contact.

---

## 👥 Staff Routing Matrix

Enquiries are automatically routed based on BEDA's organizational responsibilities:

| Staff Member | Title | Responsibility & Routing Triggers |
| :--- | :--- | :--- |
| **Matt Cooper** | Founder | Major commercial solar, multi-site (>1 GWh/yr or >$50k/mo spend), large batteries, educational institutional retrofits (`E001`, `E002`, `E005`, `E009`, `E010`). |
| **Ties Rahardjo** | Executive Operations Coordinator | Scheduling, administration, logistics, billing reconciliation, and installation contractor crew availability (`E003`, `E008`). |
| **Zidane Mouldino** | Marketing & Growth Coordinator | Marketing, inbound growth, internship/HR applications, and SMB leads (`E007`, `E012`). |
| **Ali Pratama** | Senior Business Analyst | Systems, CRM, data workflows, IT alerts, and engineering/grid connection inquiries (`E006`, `E011`). |
| **None** | Unassigned | Unsolicited junk, spam, cryptocurrency scams (`E004`). |

---

## 🤖 AI Tools & Models Used

1. **Model:** `gpt-4o-mini` (OpenAI Structured Outputs API).
2. **Schema Enforcement:** `Pydantic v2` (`ExtractedEntities` contract via `client.beta.chat.completions.parse()`).
3. **Resilience Strategy (Dual-Engine):**
   - Live mode: Pydantic structured output via OpenAI API.
   - Deterministic Fallback mode: Zero-cost, 100% grounded deterministic rule engine for test pack evaluation without API keys or token delays.

---

## 🛡️ Known Weaknesses & Day-2 Production Roadmap

| Current Limitation | Production Risk | Day-2 Improvement |
| :--- | :--- | :--- |
| **Memory-backed CRM State** | Changes lost on server restart | Persist CRM mutations and audit logs to an ACID-compliant PostgreSQL / CockroachDB database with row-level versioning. |
| **Heuristic Fuzzy Company Match** | Edge case typos or spelling errors | Integrate Levenshtein / Jaro-Winkler string similarity combined with vector embeddings (e.g. `text-embedding-3-small`) for semantic entity resolution. |
| **Synchronous Batch Ingestion** | High-volume traffic spikes could block worker threads | Wrap ingestion in an asynchronous Celery / Redis message broker with a Dead-Letter Queue (DLQ) for malformed payloads. |
| **Draft Grounding Depth** | Drafts use immediate email context only | Implement a RAG (Retrieval-Augmented Generation) pipeline querying past customer ticket histories, tariff tables, and engineering schematics. |

---

## 🎥 3-Minute Demo Video Script & Walkthrough

If recording a video walk-through, follow this concise script:

1. **Introduction (0:00 - 0:30):**
   - Introduce yourself and summarize Test 2: Ingesting untrusted inputs, multi-attribute identity resolution, strict HITL approval, and audit logging.
   - Mention the 3 deductions from Test 1 and how this build specifically resolved each one.
2. **Batch Processing & CLI Demo (0:30 - 1:15):**
   - Run `python cli.py run` in terminal to show instantaneous classification of all 12 items.
   - Point out `E002` flagged as cross-channel duplicate of `E001`, and `E010` linked to `E009`.
   - Run `python test_triage.py` to show 5 unit tests passing cleanly in 0.1s.
3. **Web UI & HITL Boundary (1:15 - 2:15):**
   - Open Streamlit (`http://localhost:8501`).
   - Show `E001` (Amelia Grant): Note the extraction of 2.1 GWh, Truganina bill $18,940, and the missing info alert (Dandenong/Epping bills needed).
   - Show Identity Resolution: Highlight that `C001` was matched, but `C002` was detected as an internal duplicate in CRM seed!
   - Edit the draft response in the text area and click `[Approve & Execute]`.
4. **CRM & Audit Trail Inspection (2:15 - 3:00):**
   - Switch to the **Live CRM State** tab: Show that CRM was updated only *after* approval was clicked.
   - Switch to the **Immutable Audit Trail** tab: Show the traceable event history with timestamps, actors, and plain-English rationales.
   - Conclude with a note on the Day-2 roadmap (Postgres persistence, embedding-based resolution).