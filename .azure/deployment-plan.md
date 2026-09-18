# TokenEconomics Studio Container Apps Deployment Plan

> **Status:** Validation Blocked

## September 18 accumulated Studio release

The user requested committing all accumulated product changes to GitHub and
updating the existing TokenEconomics Studio Container App. This is a
**MODIFY**, image-only Azure CLI deployment to the existing development
environment. It does not provision resources, publish a new TokenGov policy,
resume the paused Foundry campaign, migrate local runtime evidence, or alter
networking, identity, secrets, scaling, storage, or role assignments.

### Confirmed target and recipe

- Subscription: `ME-DIAx28327944-pdhiman-1`
  (`a91cc1ba-bd19-43a7-90ea-120794c0fbc6`), explicitly confirmed.
- Region: East US 2, explicitly confirmed with the existing deployment target.
- Resource group: `rg-tokeneconomics`.
- Container App: `ca-tokeneconomics-studio`.
- Registry: `crtokeneconomicsjf6s7lqw.azurecr.io`.
- Recipe: Azure CLI day-2 image update using an immutable ACR digest.
- Current rollback revision: `ca-tokeneconomics-studio--health-20260914`.
- Current rollback image:
  `tokeneconomics-studio@sha256:3166d1e8ec56560266cce3849d87249d40582700f8cf7b1a63179bfa63866242`.
- Revision mode: Single; the existing ready revision remains the rollback
  reference if the new revision is unhealthy.

### GitHub scope

The commit will include all accumulated tracked and untracked product changes
selected by the user, excluding files already ignored by `.gitignore` and
runtime artifacts excluded from the container build. Before commit, validate
that no credential, local environment file, runtime store, generated screenshot,
or campaign response content is staged. The commit will include the required
Copilot co-author trailer and will be pushed to the current branch
`tokengov/pcr-169289ebca`; it will not silently rewrite `main`.

### Preparation and validation gates

1. Review staged paths and secret-sensitive filenames before committing.
2. Run the complete repository test suite, plus Python compilation and Docker
   packaging/import checks covering Studio, TokenGov, RAG, campaign, and
   FutureTokenPredictor changes.
3. Verify the paused campaign remains immutable and stopped: 17 completed
   responses, eight undispatched, no Foundry evaluation, no automatic retry.
4. Commit and push the validated source to GitHub.
5. Build a new Linux image remotely in ACR with a unique September 18 tag.
6. Resolve and record the immutable image digest; do not deploy by mutable tag.
7. Compare the live Container App template, persistent mounts, environment,
   probes, identity, scale, ingress, and roles before and after the proposed
   image-only update.
8. Run ARM validation for the exact existing template with only image and
   revision suffix changed. No full Bicep deployment is planned.
9. Update only the Container App image/revision suffix after `azure-validate`
   records actual validation proof and marks this plan `Validated`.
10. Verify the new revision is ready with one replica, zero unexpected restarts,
    HTTPS `/`, `/health`, `/livez`, `/readyz`, reports, policy view, and the
    selected report/forecast APIs.
11. Verify persisted cloud report/evidence identities and hashes are unchanged.
12. Verify the active Azure policy remains
    `2026-09-18.campaign.1`; deployment must not publish policy.

### Lifecycle and evidence alignment

This release advances the product surfaces supporting
`predict -> compare policy -> admit -> execute -> evaluate -> respond ->
reconcile -> learn`. The paused campaign work itself remains at
`execute -> evaluate -> reconcile` and is not resumed by deployment.

The image consumes versioned source, contracts, tests, the existing Azure
template, current revision/digest, persistent-store inventory, and authoritative
Azure policy provenance. Deployment produces an ACR build identity and digest,
ARM validation output, Container App revision identity, health checks, and
post-deployment preservation evidence.

Model-call and complete-trajectory behavior remain explicitly distinct.
Automated Foundry graders remain advisory, human acceptance remains append-only,
segment-level sample controls remain visible, and modeled percentiles are not
presented as calibrated tail guarantees. Two-plane separation, fail-closed
Azure policy authority, least-privilege publication, immutable historical
evidence, and workload-specific RAG logic outside TokenGov core are preserved.

### Rollback and stop conditions

Stop before deployment on test, packaging, build, ARM validation, role, mount,
policy, or preservation failure. Stop after deployment if the new revision is
not ready or any required endpoint/evidence check fails. Roll back to the
recorded September 14 digest/revision without deleting new evidence. No campaign
inference or evaluation is part of deployment verification.

Remaining constitutional gaps after deployment are unchanged: the campaign
needs safe within-repetition continuation, complete 25-response evaluation,
human accepted-task outcomes, complete-trajectory cost, representative segment
samples, calibrated tail-risk evidence, bounded response/reversion proof,
billing reconciliation, and predictor learning.

### September 18 validation proof

- Source validation: `python -m pytest -q` completed with 1,428 passed and one
  skipped. The two initially detected regressions were repaired: concurrent
  same-request preflight is now rejected within a running Studio process while
  the durable billable claim remains after preflight, and historical plan input
  fallback remains compatible.
- Predictor validation: the nested MVPWeaver evidence runner completed with
  530 passed and an explicit `verdict: pass (as expected)`. Its optional
  `scripts/verify.py` board check could not run because this checkout has no
  `.weave/state.yaml`; no generated board files were edited.
- Hosted/package validation: 35 persistence, hosted-release, and plan-boundary
  tests passed. The build context excludes credentials, runtime evidence,
  documentation, infrastructure source, tests, and generated UI captures.
- Compilation: `az bicep build --file infra/studio-container-app.bicep`
  succeeded; Python `compileall` succeeded for Studio, TokenGov, RAG, scripts,
  and predictor source.
- Static/live RBAC: the Studio user-assigned identity has scoped `AcrPull` on
  the Studio registry, `App Configuration Data Reader` on the TokenGov store,
  and `Storage Blob Data Reader` on the cost-export container. The image update
  does not add data operations or role assignments.
- Policy review: the resource group has pre-existing Azure Security Baseline
  findings on storage, search, VM, registry, App Configuration, Foundry, and
  network resources. The proposed update changes none of those resources or
  settings. No Container App policy denial was identified.
- Remote Linux build: ACR run `ch8` succeeded at
  `2026-09-18T19:26:52Z`. Prepared tag
  `tokeneconomics-studio:20260918.accumulated1` resolves to immutable digest
  `sha256:01beb767ed6b53f9f90b075d332dba3556f6a68ddb2ff614bed039ad6c95eca5`.
- Campaign preservation baseline: campaign
  `campaign-5c1b823fd5934343bdbdaa3c0cdc45d8` remains stopped with
  `repetition_incomplete`, response-model allocation `$0.0111568`, no quality
  evaluation, human review pending, and no operational promotion.
- **Blocking live validation failure:** all public endpoints timed out. The
  active replica is not ready and is waiting in `PodInitializing`. Container
  Apps system logs show repeated Azure Files SMB mount failures with
  `mount error(13): Permission denied`. The state account
  `ststudiojf6s7lqws6o4` currently has `allowSharedKeyAccess=false`, while the
  Container Apps environment storage is an Azure Files key-based mount.

The deployment is not validated and must not proceed. Restoring the account
setting again would be a security/governance mutation and would not solve the
known recurring automation reversal. The governance automation owner must make
the active account-scoped exception durable, or an approved replacement storage
mount design must be prepared and validated. After the existing revision is
ready and the preservation baseline can be read, repeat Azure validation before
deploying the prepared digest.

Generated: 2026-09-01T16:51:28Z

## September 14 storage recovery and dependency readiness

The user approved continuing after the health investigation. Recover the existing
Studio Azure Files mount under the current account-scoped exception; do not change
the export source, execution authorization, policy publication or tenant-wide
governance automation. The September 12 reversal was made by
`MCAPSGovernance-AutomationApp` (app ID `a1a9c5b6-7a4f-4288-89ab-6805873f65d1`),
using PowerShell. Resource Changes confirms only `allowSharedKeyAccess` changed
from true to false. Durable prevention requires that automation's owner to honor
the existing exception; do not disable the automation or repeatedly override it.

The dedicated account setting was restored under the still-valid October 1
exception. The affected revision was restarted to reconnect its stale mount.
Recovery must be proved against the imported report and immutable evidence.

Prepare an application/probe update in the same Container App and environment:
separate process liveness from bounded storage-aware health/readiness. The check
must not rewrite report evidence, expose paths/secrets, invoke Azure policy/model
calls, create unbounded blocked threads, or claim complete-task/quality readiness.
Check persistent mount identity and storage I/O, with an explicit timeout.
Use dedicated operational probe data, not business records.

Roll out only the image and health probe definitions after targeted checks,
remote Linux build and Azure validation. Preserve all other live configuration,
one active report and its evidence. No new resources or tenant role changes.
Record any governance-owner dependency explicitly rather than declaring recurring
configuration drift permanently fixed.

Preparation proof: 68 targeted health/API/hosted-scope/persistence checks passed.
The new strict mount/binding/read-write probe also passed on the recovered live
Azure Files share in an isolated diagnostic process. ACR run `ch7` succeeded at
`2026-09-14T15:16:04Z`; image `20260914.health1`, digest
`sha256:3166d1e8ec56560266cce3849d87249d40582700f8cf7b1a63179bfa63866242`.
The proposed ARM PATCH contains only `properties.template`, cloned from live
state with only image, revision suffix and probe paths/timeouts changed.
No full infrastructure deployment is planned. Current authentication is not
enabled, so no live authentication exclusions need alteration.

Deployment result: `ca-tokeneconomics-studio--health-20260914` is ready with one
replica, zero restarts and 100% latest-revision traffic. `/health`, `/readyz`,
`/livez`, reports, the selected forecast receipt and icon all return HTTP 200.
The deployed template exactly matches the scoped patch; configuration, identity
and role assignments are unchanged. All 40 imported file hashes and 147 retirement
records are preserved. An isolated process using deployed code proved storage
failure yields 503 for health/readiness while liveness remains 200; the production
server and share were not fault-injected.

The first PATCH attempt with API 2024-03-01 was rejected without mutation because
the existing scale settings include newer `cooldownPeriod`/`pollingInterval`
fields. Provider discovery confirmed API 2025-07-01; retrying with that supported
API preserved those settings and succeeded. No field was dropped to force deployment.

Remaining external action: the MCAPS governance automation owner must honor the
account-scoped exception before its next enforcement pass. The application is
recovered and correctly monitored, but recurring configuration reversal is not
fixed by this deployment. Execution authorization and billing-source access remain
outside this recovery's scope.

## September 11 hosted icon and release-scope update

User-requested image-only update to the same Container App, subscription,
resource group and East US 2 environment. Include the existing brand PNG in the
image and default `TOKENECONOMICS_FOUNDRY_ONLY=true` in Docker. Display all nine
delivery options, but disable eight with future-release help; enforce the same
restriction before forecast creation/resume. Local native Studio stays unrestricted.
No infrastructure, authentication, storage networking, runtime policy or role
changes are planned. The existing billing-source network limitation is unchanged.

Preserve the one active imported report `RPT-20260908-CA037FE9`, its linked
evidence and all retirement records. Do not seed or migrate local stores.
Rollback target is the healthy `feedback-20260911-v2` revision and digest
`sha256:874c79eb16a92743818ea3600a1f5b818dfb4808e95b96a6f89acf7c65852e50`.

Validation: targeted UI/API/packaging tests, browser proof of disabled cards and
rendered PNG, ACR Linux image build, current identity/mount/health checks and
read-only ARM validation. Deploy only the resulting immutable image digest,
then verify image health, icon rendering, Foundry-only selection, API refusal
without side effects, and preservation of the imported report/evidence.

Alignment and remaining end-to-end gaps: D94. Release availability is not
runtime admission, quality acceptance, or production-validated optimization.

Preparation proof (2026-09-11): 75 targeted API/UI/packaging checks passed.
Read-only browser proof confirmed both icons render, eight options are disabled
with visible help, Foundry remains selectable with keyboard navigation, and
mobile width does not overflow. The one report and three forecasts reopen.
Current subscription, ready revision, persistent volume and narrowly scoped
managed-identity roles were rechecked; no settings changed.

ACR run `ch6` succeeded at `2026-09-11T19:27:51Z`, including Linux compilation,
imports and the packaged-icon existence gate. Prepared image:
`crtokeneconomicsjf6s7lqw.azurecr.io/tokeneconomics-studio:20260911.foundry1`;
digest `sha256:ab3b860221f409ec75e2723cf596c94ead5ef3ca8d23fe7668881c6ddb7bab9a`.

Deployment complete: revision `ca-tokeneconomics-studio--foundry-20260911`
is Healthy with one replica and 100% latest-revision traffic. Hosted browser proof
confirms both brand icons, eight disabled future-release cards, working Foundry
selection, keyboard behavior and mobile layout. All eight unavailable-route
API submissions return `403 route_read_only` without creating report artifacts.
The one active report, all 40 imported file hashes and all 147 retirement records
are preserved. Post-rollout managed-identity roles are unchanged.

## September 11 image-only update

Update the existing Container App in the same subscription/resource group and
East US 2 environment; do not reprovision infrastructure, modify runtime policy,
change authentication, replace Azure records or run model inference. Before this
update, the image was `tokeneconomics-studio:20260901.3` on revision `--0000003`.

The image adds persistent mounts for billing, response-learning, lifecycle and
policy-request stores. Build context excludes local runtime stores and credentials;
existing Azure Files contents remain authoritative and are not overwritten.
Local September 9 report/batch records are not automatically migrated to Azure.

### Current validation gates

- [x] Verify exact existing subscription, environment, image, mounts and identity.
- [x] Verify ACR pull, App Configuration read and export-container Blob Reader scopes.
- [x] Diagnose the pre-existing public endpoint timeout before deployment.
- [x] Validate persistence packaging and changed application tests (85 passed).
- [x] Complete remote Linux image build (ACR run `ch4`; local Docker Linux engine unavailable).
- [x] Restore an operational persistent mount with explicit approval.
- [x] Revalidate, update only the image, and prove healthy HTTPS behavior.

At the initial preflight, the replica had been stuck mounting `studio-state` since
September 10. System events reported CIFS permission denied; `allowSharedKeyAccess=false`.
The account-scoped `tokeneconomics-studio-files-mount` exception is still valid
through October 1, 2026. At that point no access setting or exemption was changed.
Approval was requested to restore the previously approved setting; the user was
initially unavailable. The image was prepared without switching the application
until the user subsequently approved recovery (recorded below).

### Initial September 11 validation proof

| Check | Result |
|---|---|
| Existing target | Same subscription `a91cc1ba-bd19-43a7-90ea-120794c0fbc6`, `rg-tokeneconomics`, East US 2 |
| Runtime permissions | Existing ACR-scoped AcrPull, App Configuration Data Reader, and export-container Storage Blob Data Reader verified; no assignments changed |
| Persistence wiring | Twelve runtime stores consistently declared in Dockerfile and entrypoint; local stores excluded from build context |
| Targeted application/persistence regression | `python -m pytest tests/test_container_persistence.py tests/test_billing_ingestion.py tests/test_batch_feedback.py tests/test_batch_feedback_api.py tests/test_batch_feedback_ui.py tests/test_performance_ui.py tests/test_reconciliation_ui.py -q`: 85 passed |
| Bicep syntax | `az bicep build --file infra/studio-container-app.bicep --stdout`: passed; template not applied |
| Existing storage link | AzureFile `studio-state`, ReadWrite, dedicated account `ststudiojf6s7lqws6o4` verified |
| Public health / existing replica | BLOCKED: HTTPS timed out; replica not started, CIFS mount permission denied |
| Storage exception | Existing account-scoped exception remains valid until 2026-10-01T23:59:59Z, but shared-key access is disabled |
| Access-setting approval | Requested; user unavailable, so no storage/security setting changed |
| ARM apply / revision switch | Not attempted while platform readiness is blocked |
| Remote Linux build | `az acr build --registry crtokeneconomicsjf6s7lqw --image tokeneconomics-studio:20260911.feedback1 --file Dockerfile --no-logs .`: run `ch4` succeeded at 2026-09-11T16:40:05Z, including Python 3.11 compilation and Studio/feedback imports |

The user explicitly approved restoring shared-key access on September 11 at
13:39 EDT. The setting was restored only on the dedicated Studio account; existing
public networking, anonymous-blob denial, TLS 1.2, exception expiry and role
assignments were unchanged. Restarting the stuck existing revision restored a
Running/ready replica and HTTPS `/health` returned healthy.

Revalidation then passed the required CLI/auth/Bicep/ARM validation/what-if helper.
The detailed preview includes property-level drift; the resource-only preview
shows ten existing resources to deploy and ten ignored, with no resource deletion.
The Bicep template will NOT be applied: only the existing app's image will change.
The image digest and AcrPull assignment were independently rechecked, and the
pre-update cloud report listing was saved for a preservation comparison.

All gates for this image-only update are satisfied. This `Validated` status is
recorded by the azure-validate workflow after those actual checks, not inferred
from build success.

### Azure Files publication compatibility follow-up

The first update became ready and served the new UI while preserving all 147
existing cloud report IDs. Container-side checks verified all twelve persistent
paths, but found that Azure Files rejects `os.link` with `EOPNOTSUPP`.
Native Linux `renameat2(RENAME_NOREPLACE)` was then tested on the same share:
it published a complete file and refused to overwrite an existing destination.

A shared create-only publication helper now uses this verified operation only
when hard links are unsupported; ordinary permission/I/O failures still propagate.
All immutable evidence publishers use the helper, and the forecast-only release
manifest includes it. No check-then-overwrite or partial-final-file fallback is
introduced. Initial publisher/store regression: 443 passed, one platform skip.
A replacement image and hosted publication proof are required before completion.

Replacement validation completed: 443 publisher/store checks and 46 lifecycle/
packaging checks passed (one platform-specific skip). ACR run `ch5` built
`20260911.feedback2` with Python 3.11 compile/import gates. ARM validation of the
replacement digest returned `Succeeded`; the earlier resource preview and
image-only scope remain unchanged. The approved storage setting remains enabled,
anonymous blob access disabled and TLS 1.2 required.

Validated replacement digest:
`sha256:874c79eb16a92743818ea3600a1f5b818dfb4808e95b96a6f89acf7c65852e50`.
The azure-validate workflow authorizes an image-only switch to this replacement,
followed by hosted no-replace publication and managed-identity read verification.

### Final September 11 deployment result

- Active/ready revision: `ca-tokeneconomics-studio--feedback-20260911-v2`, healthy,
  one replica, 100% latest-revision traffic.
- Image: `20260911.feedback2`, pinned to digest
  `sha256:874c79eb16a92743818ea3600a1f5b818dfb4808e95b96a6f89acf7c65852e50`.
- Public root and health returned HTTP 200; new bold comparison/billing headings
  are present. All 147 pre-update cloud report IDs remain present.
- All twelve `/app/studio_*` directories resolve to their `/data` counterparts.
  A temporary, isolated share probe proved complete-file publication and refusal
  to overwrite an existing destination through the deployed helper; probe files
  were removed. No business evidence was fabricated.
- Hosted billing source read remains BLOCKED: `stxbk6ickycmp22` has public network
  access disabled and no private endpoint. The correct container-scoped Blob Reader
  assignment exists; the hosted SDK returned `AuthorizationFailure`. This is
  distinct from the recovered Studio Azure Files account and was not relaxed.
- No policy publication, inference, local-report migration, data deletion or
  additional role assignment occurred. The dedicated mount exception still expires
  October 1. Full hosted billing sync needs a separately approved source network path.

Historical first prepared image (superseded; do not deploy):
`crtokeneconomicsjf6s7lqw.azurecr.io/tokeneconomics-studio:20260911.feedback1`

Immutable digest:
`sha256:6f96140e19543ec986ad0a388b1765a5c744cd90d13ac40c068f39c33c4d8f1f`

The initial blocked rollout and its recovery are historical. The current
deployment result is recorded at the top of this plan; the superseded first
image above must not be used for a new rollout.

## 1. Project Overview

**Goal:** Containerize TokenEconomics Studio, deploy it with public HTTPS ingress to
Azure Container Apps in the existing `rg-tokeneconomics` resource group, and document
how an operator can execute and verify the measured Microsoft Foundry RAG test case.

**Path:** Modernize Existing

### Lifecycle alignment

The deployment exposes the already implemented
`predict -> compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn`
control loop. It changes hosting and operator documentation only; it does not redefine
policy authority, acceptance thresholds, measured evidence, or complete-trajectory semantics.

**Evidence consumed:** immutable Plan receipts, authoritative App Configuration policy,
experiment manifests, Foundry RAG trajectories, acceptance outcomes, meter ledgers,
governance decisions, reconciliation proofs, learning proofs, and portability proofs.

**Evidence produced:** Azure deployment outputs and application telemetry. Existing runtime
evidence remains append-only on persistent storage. Hosting does not turn prototype evidence
into production-validated evidence.

## 2. Requirements

| Attribute | Value |
|---|---|
| Classification | Research prototype / development |
| Scale | Small; one replica to preserve current file-store consistency |
| Budget | Cost-optimized |
| Subscription | `ME-DIAx28327944-pdhiman-1` (`a91cc1ba-bd19-43a7-90ea-120794c0fbc6`) - confirmed |
| Resource group | `rg-tokeneconomics` - confirmed |
| Location | `eastus2` - confirmed |
| Networking | Public HTTPS ingress; no VNet/private endpoints |
| Authentication to Azure services | Microsoft Entra managed identity |

## 3. Components Detected

| Component | Type | Technology | Path |
|---|---|---|---|
| Studio UI/API | Web application | Python 3.11 `ThreadingHTTPServer` + static HTML | `studio.py`, `studio.html` |
| TokenGov core | Control plane | Python | `costgov/` |
| Predictor | Forecasting service invoked over stdio MCP | Python package | `FutureTokenPredictor/` |
| Foundry RAG adapter | Reference workload | Python, Foundry Responses API, Foundry IQ MCP | `rag/` |
| Runtime evidence | Append-only file stores | JSON, JSONL, SQLite | `studio_*` directories |
| Azure policy authority | Existing external dependency | Azure App Configuration | `appcs-xbk6ickycmp22` |

## 4. Recipe Selection

**Selected:** Bicep plus Azure CLI

**Rationale:** The repository already uses Bicep and explicit Azure CLI provisioning,
the target resource group already exists, and only one containerized web component is
being added. The deployment remains repeatable without introducing an unrelated AZD
environment over the existing infrastructure.

## 5. Architecture

**Stack:** Azure Container Apps

| Component | Azure service | SKU / configuration |
|---|---|---|
| Studio | New Azure Container App | Consumption, 0.5 vCPU, 1 GiB, min/max replicas 1 |
| Runtime host | New Container Apps managed environment | Existing Log Analytics workspace |
| Image repository | New Azure Container Registry | Basic; admin user disabled |
| Runtime identity | New user-assigned managed identity | `AcrPull` and App Configuration Data Reader only |
| Persistent Studio state | New StorageV2 account + Azure Files share | Standard LRS, TLS 1.2, no anonymous blob access |
| Policy authority | Existing Azure App Configuration | Read-only from Studio identity |
| Monitoring | Existing Application Insights and Log Analytics | Connection string injected as configuration |
| Foundry/Search | Existing resources | Not modified by this hosting deployment |

The Azure Files mount is isolated from the existing `stxbk6ickycmp22` evidence account.
Azure Container Apps currently requires a storage-account key for an Azure Files environment
mount and does not support identity-based file-share mounts. The key is held by the managed
environment storage link rather than exposed to browser code or committed source. This is a
documented platform-constrained prototype exception; App Configuration and ACR access remain
keyless. The storage account uses public network reachability because private networking was
explicitly deferred, but the share is not anonymous.

## 6. Provisioning Limit Checklist

| Resource Type | Number to Deploy | Total After Deployment | Limit / Quota | Notes |
|---|---:|---:|---:|---|
| `Microsoft.App/managedEnvironments` | 1 | 1 | 50 | Azure Quota CLI `ManagedEnvironmentCount`; usage 0 |
| Container Apps sandbox cores | 0.5 | 0.5 | 2,000 | Azure Quota CLI `SandboxCores`; usage 0 |
| `Microsoft.App/containerApps` | 1 | 1 | Governed by environment/core limits | ARG found 0 existing; no separate count quota returned |
| `Microsoft.Storage/storageAccounts` | 1 | 7 | 250 | Azure Quota CLI `StorageAccounts`; usage 6 |
| `Microsoft.ContainerRegistry/registries` | 1 | 4 | No resource-count quota returned | Quota API returned no rows; ARG found 3 existing registries |

**Status:** All quota-bearing resources are within limits. ARM validation remains mandatory
for resource types that do not expose a count quota.

## 7. Execution Checklist

### Validation proof - storage readiness (2026-09-14)

The azure-validate AZCLI helper passed CLI, authentication, Bicep compilation,
ARM validation and what-if for image `20260914.health1`. ACR `ch7` passed Linux
compilation/import gates; 68 targeted checks and a live isolated Azure Files
probe passed. Static/live role assignments still use the same narrowly scoped
AcrPull, App Configuration reader and export-container Blob Reader permissions.

The Bicep preview includes unrelated property drift and will NOT be applied.
An explicit structural comparison of the live-derived PATCH proves exactly six
changes: image, revision suffix, three probe paths and readiness timeout.
All other template fields are preserved. Application configuration, identity,
networking and authentication are omitted from the PATCH and remain unchanged.
Recheck the captured template for concurrent changes immediately before PATCH.

All validation gates pass for the scoped image/probe update. Full recurrence
prevention remains blocked on the governance automation owner's exception handling.

### Validation proof - hosted icon and release scope (2026-09-11)

The azure-validate workflow completed the AZCLI recipe's five-step helper using
`infra/studio-container-app.bicep`, the prepared image digest and the existing
subscription/resource group: CLI, authentication, Bicep compilation, ARM
validation and what-if all PASS. The preview counts property-level drift
(Create 10 / Modify 40 / Delete 14); this template will NOT be applied. Only the
Container App image changes. ACR `ch6` supplies the successful Linux build and
icon packaging gate. Targeted application checks: 75 passed; hosted-mode browser
proof passed. Existing static/live AcrPull and App Configuration reader scopes
remain correct, with no new role requirements. The account-scoped storage
exception remains valid through October 1; live mount settings, health and
the sole active report were confirmed.

All validation checks pass for an image-only rollout of digest
`sha256:ab3b860221f409ec75e2723cf596c94ead5ef3ca8d23fe7668881c6ddb7bab9a`.
No networking, policy, identity, storage or resource deletion is authorized by
this validation. The earlier billing-source connectivity blocker is unchanged.

### Phase 1: Planning

- [x] Analyze workspace
- [x] Gather requirements
- [x] Confirm subscription, resource group, and location
- [x] Inventory existing resources
- [x] Fetch quotas and validate capacity
- [x] Select deployment recipe
- [x] Plan architecture and persistence
- [x] User approved this plan

### Phase 2: Preparation

- [x] Add configurable Studio host/port and health endpoint
- [x] Add Docker image and persistent-volume entrypoint
- [x] Add Bicep for Container Apps, ACR, managed identity, and Azure Files
- [x] Add Foundry RAG operator and verification guide to `README.md`
- [x] Run targeted API tests and compile Bicep locally
- [x] Record local Docker daemon unavailable; validate image with ACR remote build
- [x] Set plan status to `Ready for Validation`

### Phase 3: Validation

- [x] Invoke `azure-validate`
- [x] Restore declared Python dependencies and run the full test suite
- [x] Compile Bicep with Azure CLI
- [x] Validate Bicep against `rg-tokeneconomics`
- [x] Run resource-group what-if and confirm no deletes
- [x] Review applicable Azure Policy assignments and deployment compatibility
- [x] Verify least-privilege RBAC scopes and role definition IDs statically
- [x] Record Docker daemon constraint; make ACR remote build the first deployment gate
- [x] Populate validation proof and set status to `Validated`

### Phase 4: Deployment

- [x] Invoke `azure-deploy`
- [x] Provision supporting resources
- [x] Build and push the Studio image
- [x] Deploy the ACR-backed Container App revision
- [x] Verify HTTPS health, Studio UI, policy loading, persistence, and live RBAC
- [x] Set status to `Deployed`

## 8. Validation Proof

Validated on 2026-09-01 against subscription
`a91cc1ba-bd19-43a7-90ea-120794c0fbc6` and resource group
`rg-tokeneconomics`.

| Check | Command / evidence | Result |
|---|---|---|
| Python regression suite | `.venv\Scripts\python.exe -m pytest -q` after restoring `requirements.txt` | PASS: 190 tests |
| Bicep compilation | `az bicep build --file .\infra\studio-container-app.bicep` | PASS |
| ARM validation | `az deployment group validate --resource-group rg-tokeneconomics --template-file .\infra\studio-container-app.bicep --subscription a91cc1ba-bd19-43a7-90ea-120794c0fbc6` | PASS |
| Change preview | `az deployment group what-if` at the same scope | PASS: 11 create, 0 modify, 0 delete |
| Azure Policy | Policy assignments listed at the resource-group scope, including Azure Security Baseline and MCAPSGov deploy/deny/audit initiatives | PASS: live ARM validation and what-if completed under enforced assignments |
| ACR pull authorization | `AcrPull` role `7f951dda-4ed3-4680-a7ca-43fe172d538d`, scoped only to the new registry | PASS |
| Policy read authorization | App Configuration Data Reader role `516239f1-63e1-4d78-a4de-a74fb236a071`, scoped only to existing `appcs-xbk6ickycmp22` | PASS |
| Policy publication separation | No App Configuration write/publish role assigned to the Studio identity | PASS |
| Container image | Local Docker Linux daemon is unavailable | ACR remote build is the first deployment gate; deployment must stop if it fails |

The live ARM checks prove that the declared resources are currently admissible under the
applicable Azure Policy assignments. They do not convert the hosted application or its
Foundry RAG evidence into production-validated behavior.

### UI update revalidation

Revalidated on 2026-09-02 before publishing the Studio navigation and status update.

| Check | Command / evidence | Result |
|---|---|---|
| Python regression suite | `python -m pytest -q` | PASS: 196 tests |
| Studio JavaScript parse | Node `new Function(...)` over the embedded script | PASS |
| Bicep compilation | `az bicep build --file infra\studio-container-app.bicep` | PASS |
| ARM validation | `az deployment group validate` in `rg-tokeneconomics` | PASS: Succeeded |
| Change preview | `az deployment group what-if --result-format ResourceIdOnly` | PASS: Succeeded; no delete action |
| Static RBAC | Resource-scoped `AcrPull` and App Configuration Data Reader assignments | PASS |
| Container Apps environment | `cae-tokeneconomics`, East US 2 | PASS: Succeeded |
| Azure Files prerequisite | Shared key enabled, public network enabled, default action Allow under the recorded temporary exemption | PASS |
| Local browser behavior | Standard, Console Light, Console Dark, report table, four-Plan selection, and fail-closed Govern navigation | PASS |
| Local Linux image | Docker Desktop Linux engine unavailable | ACR remote build remains the deployment gate |

### Deployment Verification

Deployed on 2026-09-01 with deployment
`tokeneconomics-studio-20260901`.

| Check | Result |
|---|---|
| Infrastructure deployment | PASS: managed environment, Container App, Basic ACR, managed identity, dedicated storage, Azure Files share, and scoped RBAC provisioned |
| Image build | PASS: ACR run `ch2`; `tokeneconomics-studio:20260901.2`; digest `sha256:3f74a7bfee67d06a8031600cd9a57ec075299038dc38d842fe2ec5fb0ee70dee` |
| Active revision | PASS: `ca-tokeneconomics-studio--0000002`, one replica, 100% traffic |
| Public health | PASS: `https://ca-tokeneconomics-studio.wittysand-085ba2f3.eastus2.azurecontainerapps.io/health` returned HTTP 200 |
| Public UI | PASS: root returned HTTP 200 and the TokenEconomics Studio HTML |
| Policy authority | PASS: `/api/policy` loaded version `2026-08-31.1`, label `te003-live-v2`, and ETag `cvO1KGul1sC2Mfpk0wZ0u-CukrL1uXRFczkqujEZPCk` from Azure App Configuration |
| Persistent evidence | PASS: all eight Studio evidence directories exist on `studio-state`; `/api/reports` returned the seeded immutable evidence after a new image revision |
| Live ACR RBAC | PASS: Studio identity has only `AcrPull` at the new registry scope |
| Live policy RBAC | PASS: Studio identity has only App Configuration Data Reader at the existing policy-store scope |

Two deployment defects were found and corrected before activation: the App Configuration
Data Reader role ID had a trailing character, and consumption-only Container Apps
environments reject `workloadProfileName`. The Linux image also now normalizes the
entrypoint's Windows line endings during build.

Tenant policy modified the dedicated storage account after creation by disabling shared
keys and public networking. Azure Files mounts in Container Apps require a storage key,
so the user approved a resource-scoped, time-bounded mitigation:

- Exemption: `tokeneconomics-studio-files-mount`
- Scope: only `ststudiojf6s7lqws6o4`
- Policy assignment: `MCAPSGovDeployPolicies`
- Reference IDs: `storageaccountdisablelocalauth`, `storageaccountpublicnetworkmodify`
- Category: `Mitigated`
- Expiration: 2026-10-01T23:59:59Z

Anonymous blob access remains disabled, TLS 1.2 remains required, the mount key remains
inside the managed-environment storage link, and no policy-publication permission was
granted to Studio.

## 9. Files to Generate

| File | Purpose | Status |
|---|---|---|
| `.azure/deployment-plan.md` | Deployment source of truth | Complete |
| `Dockerfile` | Studio container image | Validated for ACR remote build |
| `.dockerignore` | Safe and efficient image context | Complete |
| `scripts/container-entrypoint.sh` | Initialize and link persistent evidence paths | Complete |
| `infra/studio-container-app.bicep` | Azure Container Apps infrastructure | ARM validated |
| `README.md` | Foundry RAG user and verification guide | Complete |

## 10. Development Alignment Gate

1. **Lifecycle:** hosting exposes all lifecycle steps but changes none of their decisions.
2. **Scope:** the guide distinguishes provider model-call usage from complete RAG trajectories.
3. **Evidence:** deployment consumes immutable evidence and produces deployment/telemetry evidence.
4. **Quality:** existing segment acceptance and confidence thresholds are unchanged.
5. **Economics:** no savings or calibrated tail-risk claim is added.
6. **Boundaries:** two-plane separation, fail-closed App Configuration authority, least privilege,
   and immutable history are preserved.
7. **Claim status:** this remains measured research-prototype evidence, not production validation.
8. **Reusability:** Foundry RAG logic remains under `rag/`, outside reusable `costgov/`.

**Remaining constitutional gaps:** no held-out Tier-2 forecast improvement, no cheaper candidate
that passes all material segments, and the second workload remains below decision-grade sample size.
