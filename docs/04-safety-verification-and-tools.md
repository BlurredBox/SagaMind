# Safety, verification, contracts, and tool execution

## Defense in depth

SagaMind does not equate “Z3 accepted it” with “the action is safe.” The execution boundary consists of several checks with different purposes.

| Layer | Protects against | Does not prove |
| --- | --- | --- |
| Pydantic API schema | Missing/wrongly shaped public inputs | Semantic safety or direct Python callers |
| Typed tool policy | Missing, extra, wrong-type, oversized, out-of-range, or escaping arguments | Human intent or external effects |
| SMT invariant | Violation of the supplied logical predicate | Completeness/correctness of the predicate |
| Effect journal | Loss of recovery intent | External atomicity |
| Capability manifest | Undeclared filesystem/environment/network resources | Full kernel/container isolation |
| Worker/WASI boundary | Some process/resource failures | Correctness of the tool logic |
| Compensation contract | Restoration over a declared finite model | Equivalence between model and arbitrary implementation |

## Public request schemas

The REST layer allow-lists tool names and validates tool-specific arguments before constructing `SagaStep`. This is the first boundary, not the last. The sandbox repeats enforcement using its own registry and policies so in-process callers cannot bypass the core controls merely by skipping FastAPI.

## Typed tool policy

Every registered mutating tool must declare `mutating=True` and attach a `ToolPolicy`. Registration fails otherwise. A policy defines rules such as string, number, boolean, integer, or path; required fields; min/max; enum membership; and maximum length. Extra arguments are rejected by default.

Path rules call `contain_path`, which resolves relative paths against the configured workspace, resolves symlinks and `..`, and uses real path containment rather than a vulnerable string-prefix check.

Policy decisions can carry:

- a machine-readable status;
- the violated property;
- the submitted arguments as a counterexample;
- repair constraints such as required fields, types, bounds, or fields to remove.

## SMT invariant verification

`Z3Verifier` supports two APIs.

### Compatibility API: `verify`

The coordinator uses this API. For each supported scalar argument, the verifier:

1. declares a Z3 constant based on the Python type;
2. constrains it to the concrete submitted value;
3. parses supplied SMT-LIB2 assertions;
4. adds the negation of the invariant;
5. checks satisfiability.

If `arguments ∧ ¬invariant` is unsatisfiable, the concrete arguments entail the invariant and the action is accepted. If satisfiable, the action is rejected with a counterexample. `unknown` and timeout reject.

An empty invariant is accepted. Invalid SMT is converted to a false guard and therefore rejected when Z3 is active. When Z3 is unavailable, this compatibility API falls back to path containment and may admit non-path arguments without checking the supplied SMT text.

### Structured API: `verify_detailed`

This stricter API rejects unsupported types, invalid/empty assertion sets, unavailable-solver cases with non-empty policies, timeouts, and `unknown`. It also derives repair hints for a conservative subset of simple comparisons.

Typed policies can attach `advanced_smt`; those use the structured path. The built-in policies currently use typed rules only.

## Bounded compensation contracts

The contract DSL describes a small, total state-transition model:

- typed state variables;
- typed action inputs;
- finite domains;
- precondition;
- simultaneous forward assignments;
- postcondition;
- simultaneous compensation assignments;
- restoration condition;
- declared implementation identity;
- optional irreversible-effects declaration.

The verifier exhaustively enumerates every declared state/input combination up to `max_cases` (default 100,000). For every case satisfying the precondition it checks:

```text
postcondition(forward(original, input), original, input)
and
restoration(compensate(forward(original, input)), original, input)
```

The verifier rejects vacuous proofs with zero admissible cases. Infinite/unbounded domains, oversized proof spaces, invalid types, unsupported operations, and declared irreversible effects cannot receive `PROVED`.

### Certificates

A `VerificationCertificate` binds:

- contract name/version;
- proof status and verifier version;
- hash of the full contract;
- hash of the declared implementation identity string;
- cases checked and admissible cases;
- proof payload hash;
- counterexample and repair constraints when rejected.

A tool can require a proved certificate at registry time. The registry checks that the certificate's implementation hash matches the tool's declared identity.

This is identity binding, not semantic code verification. A string naming an implementation can match while the actual callable is wrong or later monkey-patched.

## Tool registry and capability manifests

`ToolDefinition` combines the callable with its security declaration:

- forward and optional compensation handler;
- whether it mutates state;
- typed policy;
- whether it is allowed as a compensation;
- filesystem read/write glob sets;
- network endpoint declaration;
- environment-variable allow-list;
- fuel, memory, and output limits;
- path argument/access mappings;
- optional WASM module;
- optional verified-compensation requirement.

The registry is an allow-list. Unknown tool names fail.

## Built-in reference tools

| Tool | Actual behavior | Mutating | Compensation use |
| --- | --- | --- | --- |
| `WRITE_FILE` | Atomically replaces one contained text file with optimistic preconditions. | Yes | Server captures an exact `RESTORE_FILE` preimage. |
| `DELETE_FILE` | Deletes one contained file; missing file succeeds. | Yes | Allowed; its compensation handler also deletes. |
| `RESTORE_FILE` | Restores captured text bytes or removes a newly created file, with optimistic preconditions. | Yes | Compensation-only at the REST boundary. |
| `NOOP` | Returns success without an effect. | No | Allowed. |

The REST API derives file compensations from the current preimage and binds both directions
to expected content hashes or absence. Direct in-process callers remain responsible for
supplying an accurate compensation contract.

## Python worker boundary

Trusted built-ins normally run in a fresh Python subprocess. The parent sends a small JSON request and a minimal environment. The worker applies, where supported:

- wall-clock timeout in the parent;
- CPU limit;
- file-descriptor limit;
- address-space limit on Linux;
- output-size limit;
- workspace path containment;
- environment allow-list.

On macOS, the code deliberately does not apply `RLIMIT_AS`. The worker shares the host kernel and, apart from the declared checks, is not a container, namespace jail, or microVM. Custom Python callables are not silently treated as isolated.

## WASI boundary

Tools with a `wasm_module_path` can run through Wasmtime when installed. SagaMind applies fuel and memory limits, passes only declared environment variables, denies declared network access under WASI Preview 1, and preopens the workspace only when filesystem access is declared.

Filesystem glob rules are checked for arguments before invocation, but WASI preopening is directory-level; write-vs-read enforcement depends on Wasmtime support and the manifest branch. Treat WASI as the stronger path for untrusted compiled tools, not as proof that the module implements the intended business action.

## Boundary schema alignment

Action and compensation schemas are separate and reject extra fields. The same typed
arguments are then checked by the sandbox policy registry. Generic SQL is not accepted
at either layer.
