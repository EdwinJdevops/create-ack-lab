# Design Chaos Experiment

Crossplane create acknowledgment recovery · Edwin Jonathan Chibuike · 14 November 2026

## Reference evidence

I reran the supplied archive in Ubuntu under WSL 2 with Python 3.14.4. All seven tests passed. The 11-scenario replay matched evidence.json byte for byte. Both files have SHA-256 be193e7dc2bce1f26693be6dc2cb3c95b292af6ae46c34abb2c71399d3f57cd6.

The fixture supervisor reads the durable controller state and resource inventory independently before restarting the worker. A lost acknowledgment of the pending annotation and a lost acknowledgment of a committed external create can leave the same controller record but different inventory. The named reference worker recovered in both cases, including a simulated stale read. It declined to bind a foreign resource with the same name and schema. With service-assigned IDs, the safeguard stopped with one resource in inventory while blind retry reported Ready with an extra resource.

## Upstream source review

Ouisticram reported issue [#2251](https://github.com/crossplane-contrib/provider-upjet-aws/issues/2251) against provider v2.6.0 and authored [PR #2265](https://github.com/crossplane-contrib/provider-upjet-aws/pull/2265). The reporter attributed the stuck S3 resources to a committed Kubernetes pending update whose response was lost and reported no corresponding external writes in CloudTrail.

The v2.9.0 generated [DynamoDB Table](https://github.com/crossplane-contrib/provider-upjet-aws/blob/v2.9.0/internal/controller/namespaced/dynamodb/table/zz_controller.go) and [KMS Key](https://github.com/crossplane-contrib/provider-upjet-aws/blob/v2.9.0/internal/controller/namespaced/kms/key/zz_controller.go) controllers both omit WithDeterministicExternalName. Their [external-name configurations](https://github.com/crossplane-contrib/provider-upjet-aws/blob/v2.9.0/config/externalname.go) are NameAsIdentifier and IdentifierFromProvider, respectively. Runtime revision [9b2fb6b1d1ff](https://github.com/crossplane/crossplane-runtime/blob/9b2fb6b1d1ff/pkg/reconciler/managed/reconciler.go) checks an incomplete create before Observe and writes the pending annotation before Create. Its [existing unit tests](https://github.com/crossplane/crossplane-runtime/blob/9b2fb6b1d1ff/pkg/reconciler/managed/reconciler_modern_test.go) cover the safeguard and opt-in recovery.

The PR adds the option in the generator template for resources with a name initializer or an allowlisted Terraform resource type. Its helper tests cover S3 examples, provider-assigned examples and an unknown type. The diff has no generated controllers. The author reports an S3 BucketVersioning compilation. The DynamoDB and KMS options still require confirmation in generated and compiled builds.

## Provider validation still open

Docker is installed in WSL, but its daemon was inactive and kubectl had no configured contexts during verification. No provider process, cluster or AWS endpoint participated in the reference replay. Provider generation, compilation and restart traces remain pending.

Start with an uninterrupted v2.9.0 Table create/read against an emulator in an isolated cluster. Record build digests and pin the comparison revisions, including main at 80fc406268c7c82805e8f1660b5aad32683f239b and PR head 31dd204. Compare the release, main and regenerated patch against the same desired resources. Independently inspect annotations and emulator inventory before restart. Record attempts and committed writes separately. Include the pending-write response loss, no-forward control, committed cloud response loss, stale read, same-name foreign object and provider-assigned ID cases. Attribute outcomes only after verifying each boundary and independently rerunning any apparent regression.

## Local environment follow-up

Docker 29.1.3 ran a smoke-test container in WSL. A loopback-only DynamoDB Local container using the image digest pinned in emulator-check.sh returned an empty inventory, accepted CreateTable, returned ACTIVE from DescribeTable, and then listed exactly ack-baseline. The commands used dummy credentials and addressed the emulator directly. This verifies the emulator API baseline. The Crossplane managed-resource baseline and restart comparison are still pending.
