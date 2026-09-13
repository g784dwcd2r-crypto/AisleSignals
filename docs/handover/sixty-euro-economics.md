# AisleSignals: €60 per pharmacy branch economics and architecture adjustment

13 September 2026. The user fixed pricing at **€60 per pharmacy branch per month** and requires **no hardware purchases**. The software runs on the pharmacy's existing laptop and connects to existing cameras or their recorder. One paid subscription covers one branch; one, two and three separately agreed branch subscriptions cost €60, €120 and €180 monthly. Codex plus delegated agents implement the product, with Jawahir as product owner; no hired engineering team is assumed.

**Decision:** use a lightweight desktop connector on the existing laptop, the existing recorder and a small shared cloud application. Validate actual laptop/stream performance before enabling detection. Under the retained running-cost assumptions, 25/100 paid branches leave €42.80/€46 per branch for detector licensing and other costs before owner time; the two-branch pilot leaves only €10 per branch. These are headroom ceilings, not profit or available supplier prices.

## Assumptions carried forward

Retain the prior **€5/paid branch/month application AI allowance**, **€5/branch/month storage/notifications/monitoring allowance**, and the support benchmark of 20 minutes/branch/month valued at €45/hour. This produces a **€10/branch running allowance plus shared hosting**, before detector licensing, other cash overhead and owner time. These are estimates, not quotes.

Hardware purchase, dedicated-appliance purchase, hardware installation, depreciation and hardware replacement allowances are **zero in this authorised scope**. Software onboarding is a separate activity: measure Jawahir's time to install/configure the connector, obtain permitted credentials and qualify streams. Its duration and any approved software-specific cash expense are unknown. Do not substitute a hardware-installation charge for this measurement.

Prior shared hosting allowances were €80/€180/€400 at 2/25/100 sites. For the additional branch scenarios only, use this explicit unvalidated capacity assumption: €80 for up to 10 sites, €180 for 11–50, €400 for 51–100, then €4/site above 100. These are budget bands, not guaranteed capacity or a hosting quotation.

Calculations use €60 available revenue per subscribed branch before taxes and collection fees. VAT treatment is unresolved; this does **not** authorise adding tax above the agreed customer price. If €60 must include VAT, use retained net revenue and reduce headroom accordingly. No customer hardware charge, purchased replacement laptop or dedicated appliance is assumed.

## Company and branch scenarios

All figures are monthly. “Running allowance” is shared hosting plus €10/branch; it excludes detector licences and the unknown costs itemised below. Owner-support value is not a cash salary.

| Companies | Paid branches/company | Total paid branches | Revenue | Running allowance | Headroom/branch before owner time | Headroom/branch after routine owner support |
|---:|---:|---:|---:|---:|---:|---:|
| 2 | 1 | 2 | €120 | €100 | €10.00 | −€5.00 |
| 2 | 2 | 4 | €240 | €120 | €30.00 | €15.00 |
| 2 | 3 | 6 | €360 | €140 | €36.67 | €21.67 |
| 25 | 1 | 25 | €1,500 | €430 | €42.80 | €27.80 |
| 25 | 2 | 50 | €3,000 | €680 | €46.40 | €31.40 |
| 25 | 3 | 75 | €4,500 | €1,150 | €44.67 | €29.67 |
| 100 | 1 | 100 | €6,000 | €1,400 | €46.00 | €31.00 |
| 100 | 2 | 200 | €12,000 | €2,800 | €46.00 | €31.00 |
| 100 | 3 | 300 | €18,000 | €4,200 | €46.00 | €31.00 |

The change from 50 to 75 branches reflects the explicit hosting budget-band increase. These projections do not guarantee the capacity of a server or laptop. Hardware-purchase funding is zero; initial software onboarding, development subscriptions, hosting and any detector setup licence still require actual cost records.

### Maximum detector-licence headroom

For C companies, B paid branches/company, N=C×B subscribed sites and hosting H, the zero-profit ceiling per paid branch is:

`Lmax = max(0, [60N − H − 10N − S − F − O] / N)`

S is additional shared Codex/development subscription or usage cost allocated monthly; F is other cash overhead/collection fees; O is any explicitly approved software-onboarding cash cost allocated monthly. They are **unknown, not zero-cost claims**. The following upper bounds set them to zero solely to show maximum available headroom:

| Paid branches | Cash licence ceiling/branch | After valuing routine owner support |
|---:|---:|---:|
| 2 | €10.00 | €0; owner-support-adjusted baseline is negative |
| 25 | €42.80 | €27.80 |
| 100 | €46.00 | €31.00 |

These ceilings leave no profit, contingency or unlisted overhead. For other combinations, use the table's positive remainder, floored at zero. A per-camera licence must fit inside the branch allowance: four analysed cameras at 100 paid branches permit at most €11.50/camera before owner support or €7.75 after it. Four cameras is a cost example, not an accepted laptop capacity.

At 100 paid branches, a hypothetical €20/branch detector licence would leave €26/branch before owner support and €11 after it, before S/F/O and other owner effort. This is sensitivity analysis, not a supplier offer. At two branches, the total €20 running-cost surplus can be consumed entirely by development-usage costs or a small licence charge.

## Separate money, effort and AI usage

- **Cash:** hosting, object storage, messaging, production API calls, detector/software licences, collection charges and actual paid AI subscriptions/credits. There are no hardware purchases or replacement reserves in this scope.
- **Owner time:** product decisions, laptop software onboarding, obtaining permitted CCTV access, performance tests, support, release review and evaluation. Record onboarding hours separately; they are currently unknown. The €15/branch routine-support valuation is an opportunity-cost benchmark, not salary. Development/sales time is additional. One support hour/branch/month values at €45 and removes another €30/branch of economic headroom.
- **Development AI:** Codex and its research/coding agents consume the owner's account allowance or paid credits. Record incremental subscription/credit spend and capacity limits. Do not equate a Codex subscription with production API credits, and do not assume parallel agents are costless or available continuously.
- **Product AI:** bounded report-drafting/image jobs billed by the chosen API provider. Retain **one €5 monthly allowance per paid branch**, with atomic reservations and usage reporting. Codex development credits are separate. A cheaper text-only job mix can reduce actual consumption without silently promising the same image-analysis workload.

## Economic delivery sequence at the fixed price

1. **Build and verify locally.** Codex implements one end-to-end slice at a time; agents handle bounded implementation and independent review. Use synthetic events. Track tested completion rather than retaining the earlier engineering staffing or calendar commitment. Jawahir supplies decisions and site access.
2. **Deliver the core workflow.** Incident/candidate review, manual evidence upload, staff assistance, reports and approved AI drafts remain useful while the camera connection is qualified. Make simulated, manual and live sources visibly distinct.
3. **Install the software connector on the existing laptop.** Determine its operating system, permissions, CPU/GPU, free storage, normal business workload and allowed network access. Connect existing RTSP/ONVIF or supported recorder interfaces; add no purchased camera, encoder or appliance. Test one view first, then increase only within measured headroom. Retain a replaceable detector adapter, but reject vendor integrations that require new purchased hardware.
4. **Expand on measured costs.** Keep €60/branch fixed. Record actual detector licensing, software onboarding, owner support and model usage. Reduce cost or defer a capability explicitly if it does not fit. Do not add charges or sell a generic vision model as a validated theft detector. Company oversight covers only its authorised subscribed branches.

Keep React/FastAPI/PostgreSQL jobs/outbox and key entitlements/AI allowance to **tenant + site**. The Python edge component is laptop software, not a dedicated device purchase. Use OS-supported credential storage, signed software updates, bounded buffers and CPU/memory limits. Let the existing recorder retain routine footage; the connector should handle only permitted event work. Qualification must check sleep/closed lid, reboot, Wi-Fi loss, app exit and contention with pharmacy business applications. Offline or overloaded laptops mean unavailable/degraded analytics, not guaranteed background coverage.

Do not change laptop power settings silently or assume it remains on continuously. No hardware-buying fallback is permitted: if existing cameras are inaccessible or performance is insufficient, use manual upload/incident workflows and mark the live capability unsupported until a software-only remedy is validated. Additional electricity/bandwidth is a pharmacy operating cost, currently unmeasured. Optional physical actions require an already-existing authorised interface; no new relay/sounder is assumed. Retain all command-expiry and human-authorisation safeguards.

The remaining gates are existing-laptop/camera qualification, permitted credentials, detector software terms, owner-onboarding time, VAT treatment and development-usage allocation. No hardware purchases or supplier commitments are authorised by this note.
