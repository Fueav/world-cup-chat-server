Feature: Harness workflow classification
  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Classify a task before selecting an execution shape
    Given a task that may be handled by an AI tool
    When the task is release-sensitive, broad, adversarial, repetitive, or evidence-heavy
    Then the task is mapped to a documented HARNESS-* workflow class
    And the workflow class declares its isolation model
    And the workflow class declares its verification rubric
    And the workflow class declares Agent Team suitability
    And the workflow class declares stop conditions

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Preserve the release harness as the hard gate
    Given a dynamic workflow has produced code, docs, specs, prompts, or migrations
    When the change is prepared for release
    Then the repository release harness remains the final verification authority
    And workflow evidence is written under .artifacts/
    And approval-required paths still require owner approval

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Preserve official source traceability
    Given a HARNESS-* workflow class is defined
    When the workflow manifest is validated
    Then the workflow class declares source_ids from the official source set
    And the workflow class declares adopted principle_ids
    And the source IDs and principle IDs are documented in the source analysis

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Decide whether an Agent Team is useful
    Given a HARNESS-* workflow class is selected
    When the task is evaluated for parallel slices, independent review, breadth-first research, tournaments, or long-running task nodes
    Then the workflow class explains when an Agent Team is recommended, optional, or usually avoided
    And the lead agent records the Agent Team decision when the task is not a focused change
    And the workflow does not require Agent Team execution when coordination overhead is larger than the expected quality gain

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Quarantine untrusted inputs
    Given a workflow reads public issues, tickets, resumes, incidents, logs, web pages, or user-provided files
    When agents classify or summarize that untrusted content
    Then those agents do not perform high-privilege write actions
    And a separate acting agent or human performs privileged changes after review

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Resume long-running task graph work
    Given a workflow is too large for one context window
    When the workflow uses HARNESS-LONG-RUN-TASK-GRAPH
    Then it records task status, blockers, evidence, and next actions
    And a fresh session can continue without relying on hidden chat memory

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Verify runtime legibility
    Given a workflow changes startup, local development, UI feedback, observability, logs, metrics, traces, or smoke paths
    When the workflow uses HARNESS-RUNTIME-LEGIBILITY
    Then an agent can discover how to start and inspect the running system
    And runtime evidence is captured or the blocker is explicit

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Turn repeated failures into durable checks
    Given an agent, prompt, skill, review, or workflow failure repeats
    When the workflow uses HARNESS-EVAL-IMPROVEMENT-LOOP
    Then the failure is captured as an example or regression class
    And the improvement becomes an eval, test, hook, script, skill, or documented reviewer check

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Govern higher-autonomy agent execution
    Given a workflow changes agent permissions, sandboxing, automatic execution, credentials, remote sessions, or operator controls
    When the workflow uses HARNESS-AUTONOMY-GOVERNANCE
    Then actions are classified as automatic, approval-required, or forbidden
    And sandbox, credential, and network boundaries are explicit
    And telemetry plus pause or kill controls are documented when autonomy increases
    And prompt-injection and privilege-escalation risks are reviewed separately

  @SPEC-HARNESS-WORKFLOW-001
  Scenario: Calibrate eval and runtime infrastructure noise
    Given a workflow changes evals, harnesses, runtime agent sessions, MCP tools, or code-execution surfaces
    When the workflow reports readiness evidence
    Then model behavior is separated from flaky infrastructure, evaluator bias, and noisy tool output
    And tools expose compact task-shaped context when feasible
