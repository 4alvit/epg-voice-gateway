# External qualification authority

`profile.json` pins the exact merged source of
[victron-venus/terraform-cloudflare-inverter-gateway](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/tree/648f853b9eaed2cd5640c428c18df3952cb0c2f9).
The shared builder and runtime are not forked here. The adapter stages only the
listed source files, verifies each SHA-256, and executes them in an isolated
Python interpreter. The MIT notice is preserved in `SHARED-MIT-LICENSE`.
The provider's upstream license/notice remain in that source and in its build
output; they are not relicensed by this repository.

The original [qualification run 43552645192](https://github.com/victron-venus/terraform-cloudflare-inverter-gateway/actions/runs/43552645192)
completed all 397 package tests, source vulnerability analysis and 24 actual TLS
cases. Its tested PR head was `a4e66772b7c3b1737c5cff89979843477d823126`;
the merge `648f853b9eaed2cd5640c428c18df3952cb0c2f9` preserves that tree.
The workflow's synthetic merge checkout, recorded in every original receipt,
was `7a08269b18aae0afa33e6942ec4023b315557db9`.

The sixteen original GitHub artifact ZIPs are retained without modification.
Their artifact IDs and GitHub SHA-256 digests are pinned. The shared reconciler
checks each original context, complete package inventory, individual log digest
and success result, and requires a disjoint complete union. Reusing that original
qualification ID means reusing those tests of those exact source/module/script
inputs; it does not claim tests ran again in the EPG workflow.

`qualified-provenance.json` is the original final production build record. The
final artifact's GitHub ID/digest is also recorded. Critically, the expected
production binary hash is an independent checked-in pin:
`99331c0f0c0c79d21bee25c2345ef06984b7309411fec0ca6038d24b877846d0`.
A provider supplied with its own matching JSON is insufficient. EPG rebuilds
with the same Go 1.27.4, Linux amd64, CGO disabled, `-trimpath`, `-buildvcs=false`,
version linker input, and unchanged modules. A mismatch fails qualification;
it must never be repaired by automatically updating the expected hash.

The adapter then independently exercises both EPG Worker schemas/mock plans
and the shared runtime's real TLS tests. Mock plans do not contact an account,
publish a Worker, migrate state or prove live cloud/device acceptance.
The derivative label in the source qualification record is retained exactly as
produced. This profile rebuilds from pinned source and does not download, publish
or overwrite an immutable provider release. The original final artifact contains
qualification metadata and logs; the independently pinned binary digest is
checked against EPG's new build before its Worker plan and TLS checks.
