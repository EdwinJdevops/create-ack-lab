# The cloud created it. The controller never found out.

Crossplane recovery investigation for Edwin Jonathan Chibuike · 14 November 2026

## Replay the reference experiment

Linux with Python 3.10 or later. No external dependencies, cluster, cloud credentials, or services. The replay uses loopback HTTP, two SQLite stores, and disposable controller child processes.

```sh
python3 -m unittest -v
python3 replay.py --output my-evidence.json
cmp evidence.json my-evidence.json
python3 replay.py --case create-committed-hidden-read
```

Each JSON event has a sequence number. At a fault boundary, the HTTP handler waits for the supervisor. The supervisor reads the durable Kubernetes-like and cloud-like stores independently of the worker, kills the worker and then lets a new worker reconcile the same stores. A missed boundary or failed restart raises an error. The supervisor sees resources that are deliberately hidden from a controller read.

## Observed in this reference fixture

| Fault and recovery policy | Controller record at cut | Independent inventory at cut | Observed result |
|---|---|---|---|
| Acknowledgment lost just after the pending write | Pending, no external ID | Empty | Stop policy remains blocked. Named recovery creates the resource. |
| External create never forwarded | Pending, no external ID | Empty | Stop policy remains blocked. Named recovery creates the resource. |
| External create committed, response withheld | Pending, no external ID | Original resource exists | Stop policy remains blocked. Named recovery observes and binds the original resource. |
| External create committed, first read deliberately stale | Pending, no external ID | Original resource exists | Reference worker re-observes after the create conflict. Only one create committed. |
| A different intent creates the same name and schema while the worker is down | Pending, no external ID | Empty at cut, foreign resource before restart | Reference worker declines to bind the foreign resource. |
| Create committed with a service-assigned ID | Pending, no external ID | Original resource exists | Guard keeps one resource and stops. Blind retry reports Ready while an extra resource remains. |

The fixture implements one reference recovery strategy and one deliberately unsafe strategy as a negative control. Its intent marker is a test device. It does not establish that the Crossplane or AWS APIs offer the same ownership mechanism, that AWS implements the fixture's exact schedule, or that either policy is the production default.

## Where this adds evidence upstream

- [AWS provider issue #2251](https://github.com/crossplane-contrib/provider-upjet-aws/issues/2251) independently reports a Kubernetes API write that committed the pending annotation and lost its acknowledgment. The reporter says CloudTrail showed no external write for the affected S3 operations.
- [AWS provider PR #2265](https://github.com/crossplane-contrib/provider-upjet-aws/pull/2265) proposes enabling the existing recovery option for deterministic identifiers. The PR describes resource matching unit tests and compilation of an S3 controller. The changed-file list has the template and matching helper and tests, with no regenerated controller files. A comparison must therefore generate the patched controllers and confirm their compiled options.
- The [v2.9.0 generated DynamoDB controller](https://github.com/crossplane-contrib/provider-upjet-aws/blob/v2.9.0/internal/controller/namespaced/dynamodb/table/zz_controller.go) does not pass the option. Neither does the [generated controller at inspected main commit 80fc406](https://github.com/crossplane-contrib/provider-upjet-aws/blob/80fc406268c7c82805e8f1660b5aad32683f239b/internal/controller/namespaced/dynamodb/table/zz_controller.go). The [external-name config](https://github.com/crossplane-contrib/provider-upjet-aws/blob/v2.9.0/config/externalname.go) uses `NameAsIdentifier` for DynamoDB Table and `IdentifierFromProvider` for KMS Key.
- The [runtime used by that release](https://github.com/crossplane/crossplane-runtime/blob/9b2fb6b1d1ff/pkg/reconciler/managed/reconciler.go#L1197) checks for an incomplete create before external observation. It persists the pending marker before calling external Create. [Runtime unit tests](https://github.com/crossplane/crossplane-runtime/blob/9b2fb6b1d1ff/pkg/reconciler/managed/reconciler_modern_test.go#L1157) already exercise deterministic-name recovery from a pending object and also cover the pending safeguard. The missing evidence in the inspected PR is behavior across process restart and actual external inventory.
- [AWS describes the stale-read case](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_DescribeTable.html). An immediate DescribeTable after CreateTable can return ResourceNotFoundException. The reference run simulates one such read, without claiming a real AWS consistency window.
- [Rainmaker](https://www.usenix.org/conference/nsdi23/presentation/chen-yinfang) already demonstrates request-side and response-side fault injection, cloud-side effects, and state divergence. [Acto](https://www.usenix.org/publications/loginonline/acto-push-button-end-end-testing-operation-correctness-kubernetes-operators) already checks whether actual Kubernetes operators converge and can generate regression cases. The proposed contribution is a scoped evaluation of this provider patch across both persistence boundaries and both identity classes. Priority or global novelty is unverified.

## Acceptance criteria for the real provider replay

1. Establish an uninterrupted managed Table baseline against an emulator in a fresh Kubernetes cluster with dummy credentials and traffic confined to the emulator. Confirm the provider can create and read a table using the tagged provider build before adding faults. Record package digests, runtime and generator revisions, and provider configuration.
2. Use the same desired resource and independently inspect Kubernetes annotations and emulator state at the first cut. Verify the pending update reached Kubernetes in the first case and verify CreateTable reached the emulator in the second case. Include the no-forward control. Reject any trace where the intended write boundary cannot be proved.
3. Compare the released v2.9.0 controller, inspected main commit, and the generated build from the proposed AWS-provider patch. For each trace record create attempts separately from successful commits, any identity collision, adoption of the original table, false Ready, any surviving unbound resource, and convergence within a declared observation window.
4. Test the delayed visibility case and verify the provider-assigned-ID safeguard with an emulator that supports the chosen API. Treat a same-name preexisting object separately from a previously committed object. An apparent regression requires review of the emulator and independent rerun before attribution to the provider.
5. Use the patch review to contribute new evidence. Lead any publication with a trace from the shipped provider and credit the original reporter, patch author, and related research. A fixture result alone does not justify an upstream bug claim.

Actual Crossplane processes, a Kubernetes API server, and AWS did not run in this workspace. The executable evidence is from the local reference fixture. This workspace lacked a container engine and registry access, and the linked DigitalOcean account had no existing Droplets.
