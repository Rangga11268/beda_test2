# BEDA Test 2 — Demo Video Spoken Script (English)

**Target Duration:** ~2.5 to 3 minutes  
**Format:** Screen recording with voiceover (Loom, OBS, or Windows Snipping Tool)

---

## 🎬 Section 1: Introduction & Test 1 Follow-up (0:00 – 0:30)

**[Screen: Showing README.md or IDE]**

> "Hi Matt and the BEDA team, I’m Darell. This is my submission walkthrough for Test 2.
>
> In Test 1, my submission cleared the technical reasoning gate with a score of 90, but with three important deductions:
>
> 1. CRM upsert occurring before the stated review gate.
> 2. Identity resolution relying too heavily on normalized email.
> 3. Deduplication assumptions needing more care for production.
>
> In this build, I addressed all three points directly at the root-cause level, following a strict **deterministic-first, human-in-the-loop** architecture. Let’s take a look at the running system."

---

## 💻 Section 2: Automated Tests & Headless CLI (0:30 – 1:15)

**[Screen: Switch to Terminal in `d:\laragon\www\beda_test2` directory]**

> "First, I built a self-checking test suite and a headless CLI so you can evaluate the system without even touching a browser. Let’s run the automated unit tests:"

**[Action: Type and run `python test_triage.py`]**

> "All 5 tests pass in about a tenth of a second. This verifies our multi-attribute resolution, cross-channel deduplication, and proves that zero CRM mutations occur prior to human approval.
>
> Now, let's run the batch processing CLI across all 12 synthetic items:"

**[Action: Type and run `python cli.py run`]**

> "Notice a few key items here:
>
> - `E001` and `E002`: The system catches `E002` as a **cross-channel duplicate** of `E001`—Amelia Grant submitted via both email and website form for the same 3-site solar project.
> - `E010`: The engine recognizes this not as a new lead, but as a **thread amendment** correcting Sam’s phone number for `E009` at Harbour Cold Stores.
> - And for developer pipelines, you can simply append `--json` to get structured machine-readable JSON."

---

## 🌐 Section 3: Web UI & Human-In-The-Loop Review Gate (1:15 – 2:15)

**[Screen: Switch to browser at `http://localhost:8501`]**

> "Here is our interactive Streamlit dashboard.
>
> Let's expand `E001` from Amelia Grant:
>
> - **Extraction:** It extracted the 2.1 GWh consumption, the Truganina bill amount of $18,940, and the NMI. Crucially, it preserved uncertainty: it explicitly flagged that utility bills for Dandenong and Epping are missing.
> - **Identity Resolution:** Notice it resolved to `C001`, but also detected that `C002` in your CRM seed is already an internal duplicate record, staging a merge recommendation.
> - **HITL Review Gate:** Here is the drafted response and recommended action routed to Matt Cooper. Notice that the CRM state is currently **staged**. No external emails are sent, and no database records are touched yet.
>
> As a human operator, I can review the draft, tweak the wording, and click **Approve & Execute**."

**[Action: Click `✅ Approve & Execute` button on `E001`]**

> "Once approved, the status transitions to Executed, the mock outbound email is dispatched, and the CRM mutation is committed."

---

## 🗄️ Section 4: Live CRM State & Audit Trail (2:15 – 2:50)

**[Screen: Click on "Live CRM State" Tab]**

> "If we inspect the **Live CRM State** tab, we can verify that records are updated dynamically only after approval. You can also export the live dataset as CSV right here.
>
> Next, let's look at the **Immutable Audit Log** tab:"

**[Screen: Click on "Immutable Audit Log" Tab]**

> "Every single lifecycle event—from ingestion, deduplication flags, identity resolution confidence, to human approval timestamps—is recorded with a plain-English rationale and state delta. You can download this full audit history as JSON."

---

## 🏁 Section 5: Conclusion & Day-2 Roadmap (2:50 – 3:10)

**[Screen: Return to README.md or camera/IDE]**

> "Finally, regarding resilience: the system features a dual-engine design. It runs on live GPT-4o-mini with Pydantic contracts if an API key is provided, and includes a zero-cost deterministic fallback that guarantees zero crashes out-of-the-box.
>
> With another day, I would migrate the in-memory state to an ACID PostgreSQL database with vector embeddings for semantic company deduplication.
>
> Thank you for your time, and I look forward to your feedback!"

---

## 📋 Copy-Paste Email Submission Template for Matt

**Subject:** BEDA AI Internship — Test 2 Submission (Darell)

**Body:**

```text
Hi Matt,

Thank you for the detailed feedback on Test 1. I’ve incorporated the learnings directly into Test 2 to resolve the deductions around review gate boundary enforcement, multi-attribute identity resolution, and production deduplication.

Here are my submission materials for Test 2:

1. Repository: [Link to your GitHub repository]
2. Demo Video (3 mins): [Link to your Loom / Google Drive / Unlisted YouTube video]
3. Total Build Time: 2 hours 45 minutes

Quick Summary of Implementation & Decisions:
- Strict Review Gate: Completely decoupled Staging from Execution. Zero CRM mutations or external actions occur before explicit human operator approval.
- Multi-Attribute Identity Resolution: Combines exact email, E.164 normalized phone, corporate domain filtering, and fuzzy company matching. Also detects internal duplicates in the CRM seed (C001 vs C002).
- Multi-Tier Deduplication & Thread Correlation: Identifies E002 as a cross-channel duplicate of E001, and links E010 as a contact correction updating E009.
- Dual-Engine Resilience: Operates with gpt-4o-mini (Pydantic schema) or an instant deterministic fallback engine, guaranteeing zero crashes out-of-the-box without requiring API balance.
- Interfaces: Interactive Web UI (Streamlit), full-featured Headless CLI (with --json support), and automated unit test suite.

Full architecture documentation, known weaknesses, and Day-2 scaling improvements are detailed in the repository README.md.

Looking forward to hearing your thoughts!

Best regards,
Darell
```
