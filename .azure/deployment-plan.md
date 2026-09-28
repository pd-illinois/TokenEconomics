# Public deployment template

**Status:** Reference template only

This public repository intentionally excludes environment-specific deployment evidence,
resource identifiers, hostnames, revision names, image digests, policy ETags, incident
timelines, exception details, and rollback instructions.

## Architecture

- Azure Container Apps hosts the Studio service.
- Azure App Configuration is the fail-closed policy authority.
- Managed identities receive only the resource-scoped roles required at runtime.
- A separately authorized GitHub Actions workflow publishes reviewed policy changes.
- Persistent evidence storage, monitoring, and cost exports are configured per environment.

## Required private inputs

Operators must supply deployment values through their private CI/CD environment or a
local, ignored parameter file:

- Azure subscription and resource-group scope
- resource names and endpoints
- managed-identity and Entra application identifiers
- Key Vault secret references
- GitHub App installation details
- policy key, label, and approval environment
- storage and monitoring bindings

Do not commit real values to this file, Bicep parameter files, examples, tests, generated
artifacts, or screenshots.

## Validation boundary

Before deploying, validate the application, infrastructure template, identity/RBAC
assignments, private networking, persistence readiness, and authenticated ingress in the
target environment. Keep command transcripts, deployment receipts, policy provenance,
incident response notes, and endpoint verification in a private operational system.

This template is not evidence that any environment is deployed, healthy, secure,
production-ready, or cost-complete.
