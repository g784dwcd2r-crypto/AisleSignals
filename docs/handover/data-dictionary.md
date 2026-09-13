# AisleSignals Data Dictionary

This is an implementation design. Alembic migrations and database tests are still to be built.

- Composite organisation/site foreign keys for tenant resources.
- Runtime non-owner role without BYPASSRLS; FORCE RLS on tenant tables.
- Global auth/session resolver is a separately restricted exact-lookup path.
- Dispatcher enumerates minimal permitted tenant scheduling metadata, then claims each tenant job in its own context; no ordinary app-wide business-data bypass.
- Use current transaction context and reset at transaction end; connection pooling must not retain previous tenant.
- All secret values stored in secret manager or local protected store; DB stores references.

## branch_subscriptions

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| monthly_price_cents | int |
| currency | text |
| billing_interval | enum |
| billing_status | enum |
| included_branches | int |
| tax_treatment | text? |
| billing_reference | text? |
| version | int |

Exactly 6000 EUR cents monthly for one branch; unique active subscription per organisation/site. Owner-managed billing; tax and software terms must be resolved before charging. New site registration never silently charges.

## organisations

Scope: GLOBAL. Restricted global/authentication service access; no ordinary business listing API

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| name | text |
| status | enum |
| controller_contact_reference | text |
| version | int |

Unique id; provisioning permission; no footage in registry.

## identities

Scope: GLOBAL. Restricted global/authentication service access; no ordinary business listing API

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| oidc_issuer | text |
| oidc_subject | text |
| display_name | text |
| status | enum |

Unique issuer and subject; provider authentication is separate from tenant authority.

## memberships

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| user_id | uuid |
| roles | text[] |
| status | enum |
| version | int |

Unique organisation/user; role delegation bounded by actor authority.

## membership_sites

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| membership_id | uuid |

Composite tenant/site/membership foreign keys; no cross-tenant grants.

## sessions

Scope: AUTH. Restricted global/authentication service access; no ordinary business listing API

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| token_digest | bytes |
| user_id | uuid |
| active_organisation_id | uuid? |
| csrf_digest | bytes |
| token_secret_reference | text |
| expires_at | timestamptz |
| last_seen_at | timestamptz |
| reauthenticated_at | timestamptz? |
| revoked_at | timestamptz? |

Opaque 256-bit session token; store digest only; resolver uses exact token lookup; never browser token storage.

## invitations

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| email_reference | text |
| roles | text[] |
| site_ids | uuid[] |
| token_digest | bytes |
| expires_at | timestamptz |
| consumed_at | timestamptz? |

One use; seven-day expiry; explicit invitation permission.

## sites

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| name | text |
| address | text |
| timezone | text |
| trading_hours | validated_json |
| status | enum |
| active_policy_id | uuid? |
| version | int |

Timezone Europe/Dublin; active requires approved commissioning records.

## devices

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| host_kind | enum |
| os_family | enum |
| os_version | text |
| architecture | enum |
| qualified_view_count | int |
| name | text |
| status | enum |
| software_version | text? |
| policy_version | text? |
| latest_boot_id | uuid? |
| last_seen_at | timestamptz? |
| version | int |

One site per device; no shared credentials.

## device_credentials

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| device_id | uuid |
| fingerprint | text |
| expires_at | timestamptz |
| revoked_at | timestamptz? |

Unique credential fingerprint; revoke all related tokens and leases.

## device_enrolments

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| device_name | text |
| token_digest | bytes |
| expires_at | timestamptz |
| consumed_at | timestamptz? |
| csr_digest | bytes? |

Atomic one-use redemption; ten-minute expiry; no stored private key.

## device_health_samples

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| device_id | uuid |
| measured_at | timestamptz |
| received_at | timestamptz |
| runtime_state | enum |
| os_version | text |
| cpu_fraction | numeric? |
| memory_bytes | bigint? |
| free_disk_bytes | bigint? |
| feed_status | validated_json |
| power_state | enum |

Bounded technical health retention, initially 30 days; aggregate coverage before expiry; no screen capture or patient data.

## cameras

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| connection_kind | enum |
| device_id | uuid |
| name | text |
| zone | text |
| stream_reference | text |
| model | text? |
| firmware | text? |
| qualification | enum |
| enabled_classes | text[] |
| version | int |

Composite tenant/site/device key; camera credentials remain local.

## policy_revisions

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| version | int |
| validated_policy | json |
| signer_key_id | text |
| manifest_digest | bytes |
| effective_at | timestamptz |
| superseded_at | timestamptz? |
| reason | text |

Append-only version; monotonic policy revision; signed capture lease at most 15 minutes.

## commissioning_checks

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| resource_id | uuid |
| type | enum |
| result | enum |
| performed_by | uuid |
| performed_at | timestamptz |
| evidence_reference | text |
| limitations | text[] |

Manager/existing site IT contact roles checked; immutable check record; acceptance linked to exact version/view.

## duty_assignments

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| reviewer_id | uuid |
| alternate_id | uuid |
| starts_at | timestamptz |
| ends_at | timestamptz |
| version | int |

Start before end; users authorised for site; handover transition audited.

## integrations

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| provider | text |
| credential_reference | text |
| permitted_camera_ids | uuid[] |
| status | enum |
| capabilities | validated_json |
| version | int |

No raw untrusted media URL; credentials scoped to integration and site.

## observations

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| source_id | uuid |
| source_event_id | text |
| request_digest | bytes |
| camera_id | uuid |
| capture_start | timestamptz? |
| capture_end | timestamptz |
| received_at | timestamptz |
| class | text |
| score | numeric? |
| score_scale | enum |
| model_version | text |
| quality | enum |
| historical | boolean |
| facts | validated_json |

Unique organisation/source/source_event_id; changed duplicate conflicts; never infer zero or identity from null.

## candidates

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| event_time | timestamptz |
| status | enum |
| assignee_id | uuid? |
| incident_id | uuid? |
| historical | boolean |
| version | int |

Grouped only by supported event association; no cross-visit person key.

## candidate_observations

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| candidate_id | uuid |
| observation_id | uuid |

Unique candidate/observation; same tenant and site.

## incidents

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| event_time | timestamptz |
| workflow | enum |
| classification | enum |
| outcome | enum |
| assignee_id | uuid? |
| loss_cost_cents | bigint? |
| recovered_cost_cents | bigint? |
| valuation_note | text? |
| version | int |

Workflow, classification and outcome independent; nonnegative known values; manager-only confirmed loss.

## incident_facts

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| incident_id | uuid |
| kind | enum |
| text | text |
| author_id | uuid |
| evidence_ids | uuid[] |
| supersedes_id | uuid? |

Append-only correction; each AI input fact has stable identity and provenance.

## incident_reviews

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| incident_id | uuid |
| prior_version | int |
| resulting_version | int |
| reviewer_id | uuid |
| classification | enum |
| outcome | enum |
| reason | text |
| evidence_ids | uuid[] |
| values | validated_json |

Append-only review; transactionally updates incident version; enforce manager capability for financial finding.

## incident_links

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| incident_id | uuid |
| other_incident_id | uuid |
| author_id | uuid |
| reason | text |
| review_at | timestamptz |

Restricted same-site baseline; no self link or automated identity matching; both cases must be accessible.

## tasks

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| incident_id | uuid? |
| stock_exception_id | uuid? |
| title | text |
| assignee_id | uuid |
| due_at | timestamptz |
| status | enum |
| completion_reason | text? |
| version | int |

At least one permitted parent; parent and assignee site checks; explicit cancellation.

## upload_intents

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| source_id | uuid |
| source_event_id | text |
| owner_kind | enum |
| owner_id | uuid |
| final_evidence_id | uuid? |
| private_object_key | text |
| expected_bytes | bigint |
| expected_digest | bytes |
| content_type | text |
| variant | enum |
| parent_asset_id | uuid? |
| status | enum |
| expires_at | timestamptz |

Unpredictable key; max 100 MiB default; actual media validation before publication.

## evidence_assets

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| source_id | uuid |
| source_event_id | text |
| private_object_key | text |
| variant | enum |
| parent_asset_id | uuid? |
| sha256 | bytes |
| byte_count | bigint |
| content_type | text |
| interval_start | timestamptz |
| interval_end | timestamptz |
| lifecycle | enum |
| expires_at | timestamptz |
| transformation | validated_json? |

Immutable collected bytes/digest; derived asset gets new key/id; same-site lineage.

## incident_evidence

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| incident_id | uuid |
| evidence_id | uuid |
| attached_by | uuid |
| purpose | text |

Same tenant/site; authorisation checks incident and asset; no attachment after expiry without valid hold.

## media_grants

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| actor_id | uuid |
| session_id | uuid |
| candidate_id | uuid? |
| incident_id | uuid? |
| evidence_id | uuid |
| variant | enum |
| purpose | enum |
| expires_at | timestamptz |
| revoked_at | timestamptz? |

Maximum 60 seconds; proxy checks current authority and lifecycle for every range read.

## recipients

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| name | text |
| organisation | text |
| contact_reference | text |
| purpose_category | enum |
| status | enum |

Reference for reviewed disclosure; not an unrestricted external user account.

## exports

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| requested_by | uuid |
| recipient_id | uuid |
| purpose | text |
| include_original | boolean |
| status | enum |
| manifest_key | text? |
| expires_at | timestamptz? |
| revoked_at | timestamptz? |

Baseline export stays within one site; group export is separate extension; no automatic send.

## export_items

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| export_id | uuid |
| evidence_id | uuid |
| incident_id | uuid |
| variant | enum |
| digest | bytes |

Freeze authorised selection; revalidate at build/download; preserve original versus derivative labels.

## holds

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| owner_id | uuid |
| reason | text |
| next_review_at | timestamptz |
| end_condition | text |
| status | enum |
| version | int |

Review at most 30 days apart; serialise hold changes with deletion.

## hold_items

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| hold_id | uuid |
| evidence_id | uuid |

Scope to exact evidence; derived/export handling explicit.

## rights_requests

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| received_at | timestamptz |
| due_date | date |
| requester_reference | text |
| request_type | enum |
| scope | text |
| status | enum |
| decision_reference | text? |
| version | int |

Calendar-month deadline; proportionate identity verification; disclosure review required.

## support_grants

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| support_user_id | uuid |
| scope | enum |
| evidence_ids | uuid[] |
| reason | text |
| expires_at | timestamptz |
| revoked_at | timestamptz? |

Health scope cannot view media; purpose-limited grant; audit every use.

## assistance_requests

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| requester_id | uuid |
| location | text |
| reason | text |
| status | enum |
| expires_at | timestamptz |
| acknowledged_by | uuid? |
| version | int |

Acknowledgement is not arrival; repeated request deduplicated.

## notification_intents

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| resource_id | uuid |
| recipient_id | uuid |
| routing_step | text |
| effect_key | text |
| status | enum |
| expires_at | timestamptz |

Unique effect key; no sensitive image/text in push; cancel obsolete escalation.

## delivery_attempts

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| notification_id | uuid |
| channel | text |
| attempt | int |
| sent_at | timestamptz? |
| delivered_at | timestamptz? |
| acknowledged_at | timestamptz? |
| error_code | text? |

Server receipt times authoritative; bounded retry and provider idempotency when available.

## ai_jobs

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| incident_id | uuid |
| incident_version | int |
| fact_ids | uuid[] |
| model_version | text |
| prompt_version | text |
| schema_version | text |
| reserved_cost_cents | int |
| status | enum |
| provider_request_reference | text? |

Atomic budget reservation; fixed tools/input scope; one retry maximum.

## ai_drafts

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| ai_job_id | uuid |
| text | text? |
| unresolved_questions | validated_json |
| reviewer_id | uuid? |
| decision | enum? |
| approved_text | text? |
| version | int |

Stale incident version cannot approve automatically; staff approval and factual review required.

## ai_budget_months

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| period | date |
| budget_cents | int |
| reserved_cents | int |
| spent_cents | int |
| version | int |

Unique organisation/site/month; atomically reserve before call; zero/negative available budget blocks jobs.

## ai_usage_ledger

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| ai_job_id | uuid |
| entry_type | enum |
| billed_currency | text |
| billed_amount | numeric |
| reporting_cents | int |
| conversion_note | text |
| tokens | validated_json |

Append-only reservation/settlement/release; no double settlement.

## outputs

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| device_id | uuid |
| local_output_reference | text |
| action | enum |
| max_pulse_ms | int |
| feedback_available | boolean |
| commissioning_status | enum |
| policy_version | text |
| version | int |

Documented existing vendor software interface only; laptop speaker notification uses notification path. Only staff chime/attention sounder; no new relay or door lock; max attention pulse 5000 ms.

## live_contexts

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| actor_id | uuid |
| session_id | uuid |
| camera_id | uuid |
| output_id | uuid |
| device_id | uuid |
| challenge_id | uuid |
| nonce_digest | bytes |
| challenge_issued_at | timestamptz |
| challenge_expires_at | timestamptz |
| challenge_consumed_at | timestamptz? |
| stream_sequence | bigint? |
| transient_frame_key | text? |
| frame_delete_at | timestamptz? |
| server_received_at | timestamptz? |
| last_verified_frame_at | timestamptz? |
| clock_offset_ms | int? |
| status | enum |
| expires_at | timestamptz |

Freshness established by server/companion; frozen frame and uncertain clock invalidate context.

## action_authorisations

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| actor_id | uuid |
| session_id | uuid |
| review_id | uuid |
| output_id | uuid |
| live_context_id | uuid |
| action | enum |
| nonce_digest | bytes |
| policy_version | text |
| expires_at | timestamptz |
| consumed_at | timestamptz? |

One use; max ten seconds; bind all context; transactionally consumed with command/outbox creation.

## action_commands

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| device_id | uuid |
| output_id | uuid |
| authorisation_id | uuid |
| action | enum |
| pulse_ms | int |
| issued_at | timestamptz |
| expires_at | timestamptz |
| signed_payload | bytes |
| execution_state | enum |
| physical_state | enum |
| reason | text? |

Unique authorisation consumption; immutable command ID; strict expiry; unknown execution is represented.

## command_feedback

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| command_id | uuid |
| device_boot_id | uuid |
| device_sequence | bigint |
| execution_state | enum |
| physical_state | enum |
| source_time | timestamptz |
| received_at | timestamptz |

Append-only site/device-bound feedback; contradictory/replayed state flags investigation.

## jobs

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid? |
| job_type | text |
| resource_id | uuid |
| status | enum |
| priority | int |
| available_at | timestamptz |
| attempts | int |
| lease_token | uuid? |
| lease_until | timestamptz? |
| error_code | text? |

Short claim transaction; fence completion by lease token; no arbitrary user-supplied executable payload.

## outbox_messages

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid? |
| resource_id | uuid |
| event_type | text |
| effect_key | text |
| status | enum |
| available_at | timestamptz |
| attempt | int |

Same transaction as business change; unique effect key; at-least-once delivery with receiver deduplication.

## audit_events

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid? |
| actor_reference | text |
| action | text |
| resource_reference | text |
| request_id | uuid |
| occurred_at | timestamptz |
| version_before | int? |
| version_after | int? |
| previous_digest | bytes? |
| event_digest | bytes |

Append-only runtime; independent protected checkpoints; no raw media/credentials/unnecessary narrative.

## deletion_tombstones

Scope: TENANT. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid? |
| resource_reference | text |
| object_key_reference | text? |
| decision_at | timestamptz |
| deletion_verified_at | timestamptz? |
| backup_expiry_at | timestamptz |

Replicate minimal deletion/revocation journal separately; apply before exposing restored backups.

## stock_imports

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| feed_id | uuid |
| batch_id | text |
| source_version | int |
| mapping_version | text |
| source_watermark | timestamptz |
| period_start | timestamptz |
| period_end | timestamptz |
| classes_complete | text[] |
| status | enum |

P2; unique feed/batch/version; validate completeness against source contract, not caller assertion alone.

## stock_movements

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| import_id | uuid |
| source_row_id | text |
| source_version | int |
| sku | text |
| unit | text |
| quantity | numeric(18,6) |
| movement_type | enum |
| event_time | timestamptz |

P2; unique feed/source-row/version; explicit positive quantity and movement direction; reversal/version correction.

## stock_counts

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| import_id | uuid |
| sku | text |
| unit | text |
| quantity | numeric(18,6) |
| as_of | timestamptz |
| count_role | enum |
| source_row_id | text |

P2; opening/closing boundary validation; no ambiguous units or invented count.

## stock_exceptions

Scope: TENANT_SITE. Tenant RLS plus application site/action checks

| Field | Type or meaning |
|---|---|
| id | uuid primary key |
| created_at | timestamptz |
| organisation_id | uuid not null |
| site_id | uuid not null |
| sku | text |
| unit | text |
| period_start | timestamptz |
| period_end | timestamptz |
| variance_units | numeric? |
| data_complete | boolean |
| status | enum |
| reason | text? |
| version | int |

P2; incomplete data prevents definitive reconciliation; no person attribution.