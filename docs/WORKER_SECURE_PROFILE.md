# Qualified Worker deployment profile

For a new independent deployment, both the Google and Alexa Worker modules
support the same Cloudflare 5.24.0 security derivative and OpenTofu 1.13.1 on
Linux amd64. The resource schemas and relay features are unchanged. The
derivative fixes the old official provider's TLS/x509/HTTP2 dependencies; the
ordinary registry binary remains vulnerable to the older client advisories
listed below. Selecting this profile is therefore required for the qualified
Worker provisioning path.

The adapter delegates to the exact shared
[builder and local-state runtime](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/tree/adc6d9f833bb4b42f3957bdedf5b6fed4ad21482).
Its [qualification pins](../security/worker-profile/README.md) explain the
external byte authority and retained original test receipts. It never accepts
an arbitrary executable on the strength of self-supplied provenance.

## Acquire and verify

Use a clean checkout of this project and a separate checkout of the shared
project at the exact revision below. Keep these directories and binaries
writable only by the operator. Obtain the `epg-qualified-worker-provider-linux-amd64`
artifact from a successful **Checks** run for the EPG revision being deployed,
or reproduce it using the pinned Go compiler. No GitHub token is embedded in
the package. Artifact retention is 30 days; the original receipts remain in Git.

```sh
git clone https://github.com/victron-venus/terraform-cloudflare-inverter-gateway.git ../cloudflare-profile
git -C ../cloudflare-profile checkout --detach adc6d9f833bb4b42f3957bdedf5b6fed4ad21482
# If rebuilding, use the actual Go 1.27.2 Linux amd64 executable:
python3 scripts/worker_profile.py --shared-source ../cloudflare-profile build \
  --go /absolute/path/to/go1.27.2/bin/go --output /private/path/qualified-provider
# Run this even when using a downloaded CI artifact:
python3 scripts/worker_profile.py --shared-source ../cloudflare-profile verify \
  --provider-dir /private/path/qualified-provider
```

The builder requires Python 3.12+ with OpenSSL 3+, and rejects a compiler/platform
or binary digest mismatch. It reruns source analysis and production compilation,
reconciles the 397 original package-test receipts, and requires identical
production bytes. It does not claim a new run of those upstream unit tests.

Install the official OpenTofu 1.13.1 Linux amd64 executable (SHA-256
`a325c8c2f6834575e440b03c2ba67f94256072754ac7787fea718be6f01fef6a`),
and download the official `terraform-provider-local_2.9.1_linux_amd64.zip` from
HashiCorp. The shared runtime checks the executable and the archive against its
checked-in hashes before execution. The local provider is a dependency of the
unchanged shared mirror helper; these EPG modules do not instantiate it. No
registry fallback, unpinned provider discovery or automatic update is enabled.

## New workspace and deployment

Create a **new** private workspace and preserve each module's relative relay
path. Example for Alexa; use `google-home-worker-vpc` for Google:

```sh
umask 077
mkdir -p /private/path/epg-worker/alexa-worker-vpc/terraform
cp deploy/alexa-worker-vpc/relay.mjs /private/path/epg-worker/alexa-worker-vpc/
cp deploy/alexa-worker-vpc/terraform/*.tf /private/path/epg-worker/alexa-worker-vpc/terraform/
# Supply private tfvars and CLOUDFLARE_API_TOKEN through your normal secret channel.
python3 scripts/worker_profile.py --shared-source ../cloudflare-profile run \
  --provider-dir /private/path/qualified-provider \
  --tofu /absolute/path/to/tofu \
  --local-provider-archive /absolute/path/to/terraform-provider-local_2.9.1_linux_amd64.zip \
  --directory /private/path/epg-worker/alexa-worker-vpc/terraform \
  plan -var-file=/private/path/alexa-relay.tfvars -out=/private/path/alexa.plan
```

Review the plan, retain `publish=false` initially and follow the existing relay
runbook's namespace, private reachability and authentication checks. The same
command with `apply /private/path/alexa.plan` applies an explicitly approved
saved plan. It uses the same pinned provider, private mirror and engine. This
project's CI performs no live apply or resource creation.

Qualification uses real provider plans with empty local state, synthetic inputs
and an API endpoint trapped on loopback; any attempted API connection fails the
check. It checks private/public/channel variants, exact relay bytes, account and
resource dependencies. Computed namespace URLs remain unknown during planning;
the actual postcondition is evaluated separately by OpenTofu against valid,
wrong-host, wrong-scheme, null and empty returned URLs. An explicit null guard
ensures a missing provider URL cannot become an indeterminate condition.
These checks prove plan/guard behavior, not a successful cloud deployment.

**Existing Terraform/OpenTofu/HCP state is not automatically migrated.** Do not
copy the new-workspace instructions over a live deployment or re-create managed
resources in empty state. Existing owners must back up and verify state, locks,
addresses and provider checksums, plan a controlled migration, and review its
actual diff separately. Remote backend declarations/caches are rejected by
this shared local-state profile. Existing registry locks are deliberately not
rewritten in the source repository. The explicit `registry.terraform.io`
provider address has the same Terraform identity and avoids an OpenTofu registry
remapping.

## Boundaries

The old official Cloudflare binary's GO-2026-4870 / CVE-2026-32283,
GO-2026-4918 / CVE-2026-33814, GO-2026-4947 / CVE-2026-32280 and
GO-2026-5037 / CVE-2026-27145 are addressed only when this qualified derivative
is actually selected. Its Go 1.27.2/module patch does not upgrade an already
installed registry provider. This profile does not attest to operator-installed
Wrangler, cloudflared, Home Assistant, host OS packages or live cloud acceptance;
those remain explicit deployment prerequisites. Relay behavior, publication
controls and existing alternatives are preserved.
