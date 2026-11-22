# Author's re-examination: the 8 disputed calibration answers

Write `correct`, `partially_correct` or `incorrect` on each label line. Your decision may be the same as before or different.

The labels compare the answer with the gold answer shown:

- `correct`: gives the gold answer's key facts and nothing that contradicts it;
- `partially_correct`: some key facts right, some missing, or minor errors;
- `incorrect`: wrong, contradicts the gold answer, or misses its point.

This re-examination is not blind: you can see the judge's verdicts. Also, on 2026-11-21 the assistant commented on what the labels of some of these answers would normally be; that will be recorded with the result.

Full ICAI passages: `docs/review/local/calibration_passages.md` (on this laptop only).

---

## 1. Row 8: pol-09, baseline (run `20261121-151459_answers`)

**Question:** Who must approve the recovery of a client data encryption key?

**Gold answer:** (as it was at the time of the run, which is what the judge used; the question file was edited later)

> Both custodians: the IT manager and the quality partner. The request names the engagement, the reason and the requesting partner and is logged in the IT register.

**The system's answer:**

> The context doesn't say who must approve the recovery of a client data encryption key. None of the documents cover the firm's key recovery or key management approval procedures.
>
> The closest material is general and doesn't answer the question:
> - The Digital Auditing & Assurance chapter says auditors should assess "encryption methods, cryptographic key management, and secure transmission protocols" when reviewing security measures [CAF-P3-14:6.3.1:3]. That is an audit procedure for reviewing a client's controls, not an approval requirement.
> - The same chapter's section on recovering from cyber risk covers recovery plans after an attack [CAF-P3-14:4.3.5:1]. It names no approver for key recovery.
> - The other documents deal with tax return verification, tax recovery officers and powers of income tax authorities. They concern tax "recovery" and have nothing to do with encryption keys.
>
> To find the answer, check the firm's internal information security or key management policy, or ask the IT security team or the engagement's information security lead.

**Cited passages:**

**`CAF-P3-14:6.3.1:3`** (ICAI CA Final Paper 3 (Advanced Auditing, Assurance and Professional Ethics), Chapter 12: Digital Auditing & Assurance; version ICAI study material (CA Final); effective 2025-04-01; section 6.3.1, pages 37-38; `data/corpus/standards/ca_final/p3_advanced_auditing/14_Chapter_12_Digital_Auditing__Assurance.pdf`) - licensed: short excerpt here, full passage in the local file

> (e) Review Security Measures: Assess encryption methods, cryptographic key management, and secure transmission protocols used for data protection. Review measures taken to prevent unauthorized access, cyber threats, and potential vulnerabilities [...]

**`CAF-P3-14:4.3.5:1`** (ICAI CA Final Paper 3 (Advanced Auditing, Assurance and Professional Ethics), Chapter 12: Digital Auditing & Assurance; version ICAI study material (CA Final); effective 2025-04-01; section 4.3.5, pages 23-25; `data/corpus/standards/ca_final/p3_advanced_auditing/14_Chapter_12_Digital_Auditing__Assurance.pdf`) - licensed: short excerpt here, full passage in the local file

> Entity should undertake appropriate actions to recover from the attack and make sure the business is up and running. Once the impact evaluated and communicated with the regulators the recovery [...]

**Judge's label:** `incorrect`

**Judge's reason:** The answer incorrectly states that no approval requirement is available. The required approval is from both custodians—the IT manager and the quality partner—and the request must identify the engagement, reason, and requesting partner and be logged in the IT register. It therefore misses the core answer and its associated process requirements.

Author's label after re-examination:

---

## 2. Row 14: q001, full (run `20261121-151459_answers`)

**Question:** What are the sufficiency and appropriateness requirements for audit evidence under SA 500?

**Gold answer:**

> Sufficiency is the measure of the quantity of audit evidence; appropriateness is the measure of its quality, i.e. its relevance and reliability. They are interrelated: the higher the assessed risk, the more evidence is likely to be required, and the higher the quality, the less may be required.

**The system's answer:**

> The available material does not define sufficiency and appropriateness in detail. It only gives the overall requirement and some points on reliability.
>
> **Overall requirement:** SA 500 deals with the auditor's responsibility to design and perform audit procedures to obtain sufficient appropriate audit evidence, so that reasonable conclusions can be drawn on which to base the auditor's opinion [CAF-P3-06:1:1].
>
> **Reliability (relevant to appropriateness):**
> - SA 500 indicates that the reliability of audit evidence is influenced by its source and nature, and depends on the individual circumstances under which it is obtained [STD-CH04:3.1:1].
> - Evidence is more reliable when it comes from independent sources outside the entity [STD-CH04:3.1:1].
> - Evidence obtained directly by the auditor is more reliable than evidence obtained indirectly or by inference [STD-CH04:3.1:1].
> - Evidence is more reliable when it exists in documentary form, whether paper, electronic or other medium [STD-CH04:3.1:1].
>
> **Scope of audit evidence:** It includes information in the accounting records underlying the financial statements as well as other information [CAF-P3-06:1:1].
>
> **Use of a management's expert:** The auditor may rely on an expert's work if satisfied that sufficient and appropriate evidence is obtained with reasonable assurance to form an opinion [STD-CH04:1.13:1]. To the extent necessary, the auditor must evaluate the expert's competence, capabilities and objectivity, understand the expert's work, and evaluate its appropriateness as evidence for the relevant assertion [STD-CH04:1.13:1].
>
> **Not found:** The available material does not give the formal definitions of "sufficiency" (the quantity of evidence) or "appropriateness" (its quality, i.e. relevance and reliability), or the specific SA 500 paragraph numbers. Please refer to the full text of SA 500 for these.
>
> _This answer supports, and does not replace, the professional judgement of the engagement team; sign-off remains with the engagement partner._

**Cited passages:**

**`CAF-P3-06:1:1`** (ICAI CA Final Paper 3 (Advanced Auditing, Assurance and Professional Ethics), Chapter 5: Audit Evidence; version ICAI study material (CA Final); effective 2025-04-01; section 1, pages 3-4; `data/corpus/standards/ca_final/p3_advanced_auditing/06_Chapter_5_Audit_Evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> SA 500 deals with the auditor’s responsibility to design and perform audit procedures to obtain sufficient appropriate audit evidence to be able to draw reasonable conclusions on which to base [...]

**`STD-CH04:3.1:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 3.1, pages 43-43; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> SA 500 indicates that the reliability of audit evidence is influenced by its source and by its nature, and is dependent on the individual circumstances under which it is obtained. [...]

**`STD-CH04:1.13:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 1.13, pages 32-33; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> If the entity has employed or engaged experts, the auditor may rely on the works of experts, provided he is satisfied that sufficient and appropriate audit evidence is obtained with [...]

**Judge's label:** `partially_correct`

**Judge's reason:** The response correctly states SA 500’s overall objective and provides accurate ancillary information about reliability and management experts. However, it fails to provide the question’s key requirements: sufficiency is the quantity of evidence; appropriateness is its quality, comprising relevance and reliability; and the two are interrelated such that higher assessed risk generally requires more evidence while higher-quality evidence may reduce the quantity needed. Its assertion that these definitions were not available leaves the core question unanswered.

Author's label after re-examination:

---

## 3. Row 16: q001, full (run `20261121-171000_answers`)

**Question:** What are the sufficiency and appropriateness requirements for audit evidence under SA 500?

**Gold answer:**

> Sufficiency is the measure of the quantity of audit evidence; appropriateness is the measure of its quality, i.e. its relevance and reliability. They are interrelated: the higher the assessed risk, the more evidence is likely to be required, and the higher the quality, the less may be required.

**The system's answer:**

> The available material does not set out SA 500's definitions of sufficiency and appropriateness, and it gives no paragraph-level requirements for them. It does cover the overall objective and the reliability of evidence.
>
> **What the material says**
> - SA 500 deals with the auditor's responsibility to design and perform audit procedures to obtain sufficient appropriate audit evidence. [CAF-P3-06:1:1] This allows the auditor to draw reasonable conclusions on which to base the auditor's opinion [CAF-P3-06:1:1].
> - Audit evidence is the information the auditor uses to reach the conclusions on which the opinion is based. [CAF-P3-06:1:1] It includes information in the accounting records underlying the financial statements and other information [CAF-P3-06:1:1].
>
> **Reliability, which relates to appropriateness**
> - The reliability of audit evidence is influenced by its source and nature, and depends on the individual circumstances in which it is obtained [STD-CH04:3.1:1].
> - Evidence is more reliable when obtained from independent sources outside the entity [STD-CH04:3.1:1].
> - Evidence obtained directly by the auditor is more reliable than evidence obtained indirectly or by inference [STD-CH04:3.1:1].
> - Evidence is more reliable when it exists in documentary form, whether paper, electronic or other medium [STD-CH04:3.1:1].
>
> **Not found**
> - The material does not define sufficiency (the quantity of evidence) or appropriateness (its quality, meaning relevance and reliability).
> - It does not give the SA 500 paragraph requirements on how these are assessed, such as the effect of misstatement risk or evidence quality on the amount needed.
>
> _This answer supports, and does not replace, the professional judgement of the engagement team; sign-off remains with the engagement partner._

**Cited passages:**

**`CAF-P3-06:1:1`** (ICAI CA Final Paper 3 (Advanced Auditing, Assurance and Professional Ethics), Chapter 5: Audit Evidence; version ICAI study material (CA Final); effective 2025-04-01; section 1, pages 3-4; `data/corpus/standards/ca_final/p3_advanced_auditing/06_Chapter_5_Audit_Evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> SA 500 deals with the auditor’s responsibility to design and perform audit procedures to obtain sufficient appropriate audit evidence to be able to draw reasonable conclusions on which to base [...]

**`STD-CH04:3.1:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 3.1, pages 43-43; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> SA 500 indicates that the reliability of audit evidence is influenced by its source and by its nature, and is dependent on the individual circumstances under which it is obtained. [...]

**Judge's label:** `incorrect`

**Judge's reason:** The response fails to provide the requested SA 500 requirements. It expressly denies the key definitions and interrelationship stated in the gold answer: sufficiency is quantity; appropriateness is quality (relevance and reliability); higher assessed risk generally requires more evidence; and higher-quality evidence may reduce the quantity needed. The cited background on audit-evidence reliability is accurate but does not cure the omission or the contrary assertion that the definitions/requirements are unavailable.

Author's label after re-examination:

---

## 4. Row 17: q030, full (run `20261121-171000_answers`)

**Question:** Do we have to document population completeness when using audit sampling?

**Gold answer:**

> Yes. Technical Circular 2025/04 (15 June 2025) requires the team to document how it checked the population is complete; for a test of controls it must cover the whole period. Circular 2023/05 had no such requirement.

**The system's answer:**

> The material says the auditor must perform procedures to obtain evidence that the population is complete, and this is tied to the requirements of SA 500. [STD-CH04:6.9.1:1] "In fulfilling the requirement of SA 500 'Audit Evidence', when performing audit sampling, the auditor performs audit procedures to obtain evidence that the population from which the audit sample is drawn is complete." [STD-CH04:6.9.1:1]
>
> Supporting points:
> - The population must be complete. [STD-CH04:6.3.1:1] Where the sample is used to conclude on whether a control operated effectively during the period, the population needs to include all relevant items, meaning all activities that form part of that control, throughout the entire period. [STD-CH04:6.3.1:1]
> - If the population is complete in all respects, conclusions drawn from it are considered reasonable. [STD-CH04:6.3.1:1]
> - The auditor should also perform procedures to ensure the information underlying the sampling is sufficiently complete and accurate, and should obtain evidence about the reliability of the population. [STD-CH04:6.3.1:1]
> - If the population is not reliable with respect to accuracy and source, the sample drawn will not be relevant for the specific audit objective. [STD-CH04:6.3.1:1]
>
> The available material does not specify the form the documentation must take (for example, a particular working paper or firm template), and no firm policy on this was found. The sources state the requirement to obtain evidence of completeness, and the extent and form of documentation is a matter for professional judgement.
>
> _This answer supports, and does not replace, the professional judgement of the engagement team; sign-off remains with the engagement partner._

**Cited passages:**

**`STD-CH04:6.9.1:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 6.9.1, pages 85-86; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> When designing an audit sample, i) the auditor’s consideration includes the specific purpose to be achieved and the combination of audit procedures that is likely to best achieve that purpose. [...]

**`STD-CH04:6.3.1:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 6.3.1, pages 78-79; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> 1. Appropriateness: The auditor will need to determine that the population from which the sample is drawn is appropriate for the specific audit objective. Appropriate means population from which the [...]

**Judge's label:** `incorrect`

**Judge's reason:** The response misses the controlling superseding requirement: Technical Circular 2025/04 requires documentation of how population completeness was checked, including coverage of the entire period for tests of controls. It also incorrectly states that no firm policy was found and that the documentation form/extent is solely a matter of professional judgement. It does not mention that Circular 2023/05 lacked this requirement.

Author's label after re-examination:

---

## 5. Row 19: q024, full (run `20261121-171000_answers`)

**Question:** What performance materiality does the firm's current materiality policy require?

**Gold answer:**

> Under Materiality Policy v2.0 (1 April 2025), performance materiality is 50% to 75% of overall materiality depending on assessed risk: 50% for higher-risk and first-year audits, 75% for lower-risk recurring engagements. v1.0 (1 April 2023) used a flat 75%.

**The system's answer:**

> The current policy sets performance materiality at 50%–75% of overall materiality, depending on the assessed risk of material misstatement [POL-MAT-02:3:1].
>
> - 50% applies to higher-risk engagements and first-year audits [POL-MAT-02:3:1].
> - 75% applies to lower-risk recurring engagements [POL-MAT-02:3:1].
> - These are the firm's default benchmarks, and the engagement partner may depart from a default with documented reasons [POL-MAT-02:1:1].
>
> The earlier policy (POL-MAT-01, effective 2023-04-01) was revised, and POL-MAT-02 (version 2.0, effective 2025-04-01) is now current [POL-MAT-02:3:1][POL-MAT-01:1:1]. The earlier version's performance materiality percentages were not found in the available material.
>
> _This answer supports, and does not replace, the professional judgement of the engagement team; sign-off remains with the engagement partner._

**Cited passages:**

**`POL-MAT-02:3:1`** (Materiality Policy; version 2.0; effective 2025-04-01; replaces POL-MAT-01; section 3; `data/corpus/firm_policy/materiality_policy_v2.md`)

> Materiality Policy > 3. Performance materiality
> Performance materiality is set between 50% and 75% of overall materiality, depending on
> the assessed risk of material misstatement: 50% for higher-risk engagements and first-year
> audits, 75% for lower-risk recurring engagements.

**`POL-MAT-02:1:1`** (Materiality Policy; version 2.0; effective 2025-04-01; replaces POL-MAT-01; section 1; `data/corpus/firm_policy/materiality_policy_v2.md`)

> Materiality Policy > 1. Scope
> This policy sets the firm's default benchmarks for overall materiality, performance
> materiality and the clearly trivial threshold. The engagement partner may depart from a
> default with documented reasons.

**`POL-MAT-01:1:1`** (Materiality Policy; version 1.0; effective 2023-04-01; superseded by POL-MAT-02; section 1; `data/corpus/firm_policy/materiality_policy_v1.md`)

> Materiality Policy > 1. Scope
> This policy sets the firm's default benchmarks for overall materiality, performance
> materiality and the clearly trivial threshold. The engagement partner may depart from a
> default with documented reasons.

**Judge's label:** `partially_correct`

**Judge's reason:** The answer correctly states the current v2.0 performance-materiality range and its risk-based application. However, it omits the gold answer's required historical comparison that v1.0 used a flat 75%, and instead says the earlier percentages were not found.

Author's label after re-examination:

---

## 6. Row 20: q013, full (run `20261121-171000_answers`)

**Question:** Above what level of branch advances is an EQCR required for a bank branch audit?

**Gold answer:**

> Total branch advances above ₹100 crore (Technical Circular 2026/03, FY 2025-26), reduced from ₹150 crore in Circular 2025/02.

**The system's answer:**

> I could not find this in the material available to your role and engagement assignments. If you believe you need it, please ask your engagement manager.

**Cited passages:**

None: the answer cites nothing.

**Judge's label:** `incorrect`

**Judge's reason:** The answer does not provide the required EQCR threshold of total branch advances above ₹100 crore, nor does it note that this was reduced from ₹150 crore.

Author's label after re-examination:

---

## 7. Row 25: q012, baseline (run `20261121-171000_answers`)

**Question:** How many items must we sample for a test of controls?

**Gold answer:**

> Under Technical Circular 2025/04 (15 June 2025): minimum 25 items for low, 40 for moderate and 60 for high assessed control risk. It replaced Circular 2023/05, which set a flat minimum of 25.

**The system's answer:**

> Under the firm's current guidance, the minimum sample size for a test of controls depends on the assessed control risk. Technical Circular 2025/04 (effective 15 June 2025) sets these minimums [CIR-2025-04:4:1]:
>
> | Assessed control risk | Minimum items |
> |---|---|
> | Low | 25 |
> | Moderate | 40 |
> | High | 60 |
>
> **Superseded guidance:** Technical Circular 2023/05 (effective 1 June 2023) set a flat minimum of 25 items regardless of assessed risk [CIR-2023-05:3:1]. The revised 2025/04 circular replaces this with the risk-based table above, so the flat 25 no longer applies to moderate or high risk. The context does not say explicitly that the 2025 circular supersedes the 2023 one. I am inferring this from its later date and its title, "Revised".
>
> **Professional standard context:** SA 530 requires a sample size large enough to reduce sampling risk to an acceptably low level. The lower the risk you are willing to accept, the larger the sample must be. Sample size also increases the more you rely on the operating effectiveness of controls [STD-CH04:6.9.2:1] [STD-CH04:6.9:1]. For very small populations, examining all items may be more efficient than sampling [STD-CH04:6.9.2:6].
>
> The firm's figures are minimums. Use your judgment to go higher if the circumstances call for it.

**Cited passages:**

**`CIR-2025-04:4:1`** (Technical Circular 2025/04: Audit Sampling Guidance (Revised); version 2.0; effective 2025-06-15; replaces CIR-2023-05; section 4; `data/corpus/circulars/cir_2025_04_audit_sampling_guidance_revised.md`)

> Technical Circular 2025/04: Audit Sampling Guidance (Revised) > 4. Sample sizes for tests of controls (fictional firm defaults)
> | Assessed control risk | Minimum items |
> |---|---|
> | Low | 25 |
> | Moderate | 40 |
> | High | 60 |

**`CIR-2023-05:3:1`** (Technical Circular 2023/05: Audit Sampling Guidance; version 1.0; effective 2023-06-01; superseded by CIR-2025-04; section 3; `data/corpus/circulars/cir_2023_05_audit_sampling_guidance.md`)

> Technical Circular 2023/05: Audit Sampling Guidance > 3. Sample sizes for tests of controls (fictional firm default)
> Use a minimum of 25 items for any test of controls, regardless of assessed risk.

**`STD-CH04:6.9.2:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 6.9.2, pages 88-89; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> The auditor shall determine a sample size sufficient to reduce sampling risk to an acceptably low level. The level of sampling risk that the auditor is willing to accept affects [...]

**`STD-CH04:6.9:1`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 6.9, pages 85-85; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> When designing an audit sample, the auditor shall consider the purpose of the audit procedures and the characteristics of the population from which the sample will be drawn. The auditor [...]

**`STD-CH04:6.9.2:6`** (ICAI Auditing and Ethics, Chapter 4: Audit Evidence; version ICAI study material; effective 2025-04-01; section 6.9.2, pages 92-93; `data/corpus/standards/ch04_audit_evidence.pdf`) - licensed: short excerpt here, full passage in the local file

> Once the population is sufficiently large, the number of items in the population has very little influence on the required sample size. For very small populations, examining all items may [...]

**Judge's label:** `partially_correct`

**Judge's reason:** The answer provides the correct current minimums (25 low, 40 moderate, 60 high) and correctly describes the former flat 25-item requirement. However, it incorrectly says the context does not explicitly state that Circular 2025/04 supersedes/replaces Circular 2023/05; both cited document metadata explicitly state this.

Author's label after re-examination:

---

## 8. Row 27: pol-09, full (run `20261121-151459_answers`)

**Question:** Who must approve the recovery of a client data encryption key?

**Gold answer:** (as it was at the time of the run, which is what the judge used; the question file was edited later)

> Both custodians: the IT manager and the quality partner. The request names the engagement, the reason and the requesting partner and is logged in the IT register.

**The system's answer:**

> I could not find this in the material available to your role and engagement assignments. If you believe you need it, please ask your engagement manager.

**Cited passages:**

None: the answer cites nothing.

**Judge's label:** `incorrect`

**Judge's reason:** The answer does not provide the required approval details: both custodians (the IT manager and quality partner), nor the required request and logging information. Instead it incorrectly states that the information could not be found.

Author's label after re-examination:
