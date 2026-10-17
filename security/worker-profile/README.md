# External qualification authority

`profile.json` pins the exact merged source of
[victron-venus/terraform-cloudflare-inverter-gateway](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/tree/adc6d9f833bb4b42f3957bdedf5b6fed4ad21482).
The shared builder and runtime are not forked here. The adapter stages only the
listed source files, verifies each SHA-256, and executes them in an isolated
Python interpreter. The MIT notice is preserved in `SHARED-MIT-LICENSE`.
The provider's upstream license/notice remain in that source and in its build
output; they are not relicensed by this repository.

The original [qualification run 38970755003](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/actions/runs/38970755003)
completed all 397 package tests, source vulnerability analysis and 40 actual TLS
cases. Its tested PR head was `44dc0104d3a8fcdf70e7d2b3cc48fcfba465cd91`;
the merge `adc6d9f833bb4b42f3957bdedf5b6fed4ad21482` preserves that tree.
The workflow's synthetic merge checkout, recorded in every original receipt,
was `bb4ee06130afd80756b53839111f6f7f36115b1b`.

The sixteen original GitHub artifact ZIPs are retained without modification.
Their artifact IDs and GitHub SHA-256 digests are pinned. The shared reconciler
checks each original context, complete package inventory, individual log digest
and success result, and requires a disjoint complete union. Reusing that original
qualification ID means reusing those tests of those exact source/module/script
inputs; it does not claim tests ran again in the EPG workflow.

`qualified-provenance.json` is the original final production build record. The
final artifact's GitHub ID/digest is also recorded. Critically, the expected
production binary hash is an independent checked-in pin:
`f81caa08016c399537d367e11ea75f73731aad4c7a527a1bd8def1d83bbf3490`.
A provider supplied with its own matching JSON is insufficient. EPG rebuilds
with the same Go 1.27.2, Linux amd64, CGO disabled, `-trimpath`, `-buildvcs=false`,
version linker input, and unchanged modules. A mismatch fails qualification;
it must never be repaired by automatically updating the expected hash.

The adapter then independently exercises both EPG Worker schemas/mock plans
and the shared runtime's real TLS tests. Mock plans do not contact an account,
publish a Worker, migrate state or prove live cloud/device acceptance.
