# Private evidence and local recovery

This implementation applies to protected pilot workspaces. The existing synthetic demonstration database keeps its current JPEG format and is never converted into client data.

Each pilot sampled JPEG is encrypted with AES-256-GCM using the `cryptography` library. A random nonce and authenticated organisation, branch, interaction and frame identifiers prevent a ciphertext being reused for another scope or frame. The private 32-byte key is stored beside the database as `<database>.evidence-key`, outside the evidence directory. Missing or damaged keys fail closed; the application does not silently replace a key for existing interaction records. Normal authenticated evidence requests decrypt only after branch access and expiry checks.

SQLite metadata, staff reviews and account password hashes are **not encrypted by this application**. Private filesystem permissions and the laptop's full-disk protection remain necessary. An operating-system administrator who can read both the database and key can recover media. Python's private permission bits do not replace verification of Windows account-directory ACLs; that remains a client-device acceptance check.

## Bounded collection

Sampled records expire after 24 hours, including reviewed records; expiry independently denies reads and the retention worker removes files and metadata. Each branch can hold 100 samples, up to 600 on an installation. At capacity, the oldest unreviewed ordinary-shopping results (take, return, place in basket, normal shopping), unclear results and failed/cancelled jobs roll off automatically. Reviewed results and potential concealment stay until explicit deletion or their 24-hour expiry. A branch never evicts another branch's samples. A full store of protected samples pauses analysis with an explicit error. Collection also pauses before writing if less than 128 MiB of free disk space plus the incoming sample size remains.

Rolling deletion is recorded in the audit log. Audit metadata has its own lifecycle and is not silently removed by the media retention worker. The 600-record bound applies to interaction samples, not every database row. Retention is for sampled review derivatives, not preservation of the original CCTV recording.

## Create a recovery archive

Stop the pilot normally and wait for an active model request to finish cancellation. The backup tool takes an OS lock also owned by the pilot API; it refuses while that database is active. A SQLite write lock serialises account provisioning while a consistent database snapshot and its referenced media are copied. API locks and backup/restore behavior must also pass the Windows CI and device checks before Windows acceptance is claimed.

```sh
python scripts/pilot_backup.py create --db .local/pilot/aislesignals.db --output work/private-recovery.asbackup
```

The destination parent must exist and the filename must be new. Enter a unique 14–256-character passphrase at the hidden terminal prompt and store it separately. No command-line or environment passphrase option is provided. The archive includes the database, authenticated encrypted media and its matching recovery key, all wrapped in streaming AES-256-GCM with a scrypt-derived passphrase key (N=32768, r=8, p=1; random 16-byte salt). Temporary plaintext database/archive files exist in a private temporary directory during the operation and are removed on normal success/failure. A crash or power loss may require the operator to remove abandoned `.aisle-backup-*` or `.aisle-restore-*` directories. Local disk encryption protects those remnants; secure erasure on SSDs is not promised.

This is an operator-created local backup, with a 64 MiB database and 2 GiB archive limit. It is not cloud storage, automatic scheduling or point-in-time recovery. Store the backup privately outside the running workspace to survive workspace loss. The application cannot delete copies moved elsewhere: apply the pharmacy's documented backup retention, including the 24-hour sampled-media limit, and remove expired archives. Losing the passphrase makes the archive unrecoverable.

## Restore and verify

```sh
python scripts/pilot_backup.py restore --archive work/private-recovery.asbackup --target-dir work/recovered-pilot
```

The target directory must not exist. Restore authenticates the complete archive before reading any archive entry, checks fixed filenames, size bounds, file manifests, SQLite schema/integrity, branch memberships and each media authentication/hash. Existing files are never overwritten. The restored database is `aislesignals.db` inside the chosen directory.

All restored sessions are revoked and every account is disabled. An older backup must not reactivate a former employee's access. The local setup operator must inspect current staff membership and explicitly re-enable appropriate named accounts using the accounts administration command. Expired samples are discarded; pending/running jobs are cancelled and their media removed. Restoring never resumes monitoring or arms alarms. Configure the launcher to use the restored directory, sign in after account review, and repeat camera, frame freshness, audio and recovery checks before resuming a supervised session.

Reference APIs: [cryptography AES-GCM](https://cryptography.io/en/latest/hazmat/primitives/aead/) and [streaming authenticated encryption](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/). The automated recovery fixtures contain synthetic coloured images and test account names, not client footage. A local automated restore test is not a completed pharmacy recovery drill.
