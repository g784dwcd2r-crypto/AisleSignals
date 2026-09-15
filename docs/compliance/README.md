# AisleSignals privacy and compliance pack

This pack helps an Irish pharmacy document and operate AisleSignals before
processing real CCTV-derived evidence. It is a controlled template, not legal
advice or a completed assessment for any pharmacy.

The pharmacy is the controller for its deployment. AisleSignals' legal entity
and every subprocesser must be recorded before evidence mode is enabled. The
controller remains responsible for choosing and documenting the purpose,
lawful basis, necessity, proportionality, retention and access rules.

## Required records

1. Complete [the DPIA](dpia-template.md) before live automated analysis.
2. Complete [the legitimate-interests assessment](legitimate-interests-assessment.md)
   if legitimate interests is the proposed Article 6 basis.
3. Adapt and display [the privacy notice and entrance sign](privacy-notice-and-signage.md).
4. Approve [the retention, rights and disclosure policy](retention-rights-and-disclosure.md).
5. Sign [the controller/processor checklist](controller-processor-checklist.md).
6. Maintain [the processing record](processing-record-template.md) and
   [the personal-data breach procedure](personal-data-breach-procedure.md).
7. Train staff with [the operating procedure](staff-operating-procedure.md).
8. Create one machine-readable branch record from
   [the example](branch-activation.example.json) and validate it:

   ```bash
   python docs/compliance/tools/validate_compliance_record.py \
     path/to/branch-compliance.json
   ```

The example deliberately fails activation. It contains synthetic names and
open decisions so it cannot be mistaken for approval. Keep completed customer
records and signatures outside Git in the customer's controlled record system.

## Activation rule

Metadata-only operation may continue when evidence mode is blocked. Evidence
mode must remain disabled when the record is absent, expired, incomplete, has
an unmitigated high risk, or lacks controller and privacy-review sign-off.
Changing cameras, purposes, recipients, models, evidence types, retention,
hosting regions or automated consequences invalidates approval and requires a
review before reactivation.

## Product boundaries

- Describe detections as observations requiring staff review.
- Do not label a person as a thief or make a criminality finding.
- Do not use face recognition, cross-visit biometric identification or shared
  offender watchlists.
- Do not collect audio, consultation-room footage, prescription screens or
  other unrelated areas.
- Do not let an AI result independently trigger a public accusation, physical
  intervention or other significant decision.
- Upload evidence only when that branch has separately approved evidence mode.

## Official guidance used

- [Irish DPC CCTV guidance](https://www.dataprotection.ie/en/dpc-guidance/guidance-on-the-use-of-cctv)
- [Irish DPC DPIA guide and template](https://www.dataprotection.ie/en/dpc-guidance/guide-data-protection-impact-assessments)
- [Irish DPC list of processing requiring a DPIA](https://www.dataprotection.ie/sites/default/files/uploads/2018-11/Data-Protection-Impact-Assessment.pdf)
- [EDPB Guidelines 3/2019 on video devices](https://www.edpb.europa.eu/documents/guideline/guidelines-32019-on-processing-of-personal-data-through-video-devices_en)
- [GDPR](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
