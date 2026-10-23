# External qualification authority

`profile.json` pins the exact merged source of
[victron-venus/terraform-cloudflare-inverter-gateway](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/tree/fd90bb21075f40378409f8c8613156a7c224df19).
The shared builder and runtime are not forked here. The adapter stages only the
listed source files, verifies each SHA-256, and executes them in an isolated
Python interpreter. The MIT notice is preserved in `SHARED-MIT-LICENSE`.
The provider's upstream license/notice remain in that source and in its build
output; they are not relicensed by this repository.

The original [qualification run 39913575051](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/actions/runs/39913575051)
completed all 397 package tests, source vulnerability analysis and 40 actual TLS
cases. Its tested PR head was `2f42c5cdb61a2439d5e11b0d5413075983a78fd3`;
the merge `fd90bb21075f40378409f8c8613156a7c224df19` preserves that tree.
The workflow's synthetic merge checkout, recorded in every original receipt,
was `d93dd86c24942a9140dea60b06932f00b2e4a5e8`.

The sixteen original GitHub artifact ZIPs are retained without modification.
Their artifact IDs and GitHub SHA-256 digests are pinned. The shared reconciler
checks each original context, complete package inventory, individual log digest
and success result, and requires a disjoint complete union. Reusing that original
qualification ID means reusing those tests of those exact source/module/script
inputs; it does not claim tests ran again in the EPG workflow.

`qualified-provenance.json` is the original final production build record. The
final artifact's GitHub ID/digest is also recorded. Critically, the expected
production binary hash is an independent checked-in pin:
`cc4fb9bb02311dcffbada691ec3c080635e35782c0ec46275fab85168805e4f1`.
A provider supplied with its own matching JSON is insufficient. EPG rebuilds
with the same Go 1.27.3, Linux amd64, CGO disabled, `-trimpath`, `-buildvcs=false`,
version linker input, and unchanged modules. A mismatch fails qualification;
it must never be repaired by automatically updating the expected hash.

The adapter then independently exercises both EPG Worker schemas/mock plans
and the shared runtime's real TLS tests. Mock plans do not contact an account,
publish a Worker, migrate state or prove live cloud/device acceptance.
