# Harness Workflow Performance Spec

Spec ID: `SPEC-HARNESS-WORKFLOW-001`

Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

| Metric | Target |
| --- | --- |
| Manifest validation latency | `< 1s on the template repository` |
| Release gate overhead | `< 2s excluding existing Go/tool checks` |
| Manifest size | `< 96KB` |
| Benchmark command | `time scripts/check_harness_workflows.sh` |

Dynamic workflows can intentionally spend more tokens or machine time when the task needs parallelism, adversarial verification, or repeated loops. The workflow class must declare the expected budget and stop condition before that extra compute is used.
