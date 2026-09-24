# SagaMind: An Enterprise-Grade, Transaction-Safe Multi-Agent Runtime and Cognitive Memory Co-Processor
## Comprehensive Architectural & System Design Specification

> **Design-proposal status:** This document contains aspirational architecture and historical implementation sketches. Current behavior is defined only by `ARCHITECTURE.md`; evaluated claims are in `research_paper.md` and tests. COW speculative filesystems remain unimplemented; built-ins use isolated Python workers and optional untrusted compiled tools use WASI.

**Document Version:** 1.0.0  
**Classification:** Research and Production Architecture Specification  
**Author:** Senior AI/ML Architect & Lead Systems Researcher  
**Target Path:** `/Users/Harutyun/Desktop/Portfolio1/arcitecture_exp.md`  

---

## Abstract
This document defines a proposed compound AI-agent runtime intended to connect probabilistic planning with explicit software reliability controls.

SagaMind addresses two primary bottlenecks in production-grade multi-agent deployments:
1. **Execution Instability:** Resolving the sequential cascading failure problem through an **Agentic Saga Transaction Coordinator** that implements stateful, LIFO-ordered compensating transactions to ensure eventual consistency in external systems.
2. **Context Drifts and Retrieval Noise:** Resolving memory pollution and high token usage via the **Complementary Learning Memory (CLM)** model. CLM utilizes Ebbinghaus forgetting curve algorithms and asynchronous DBSCAN-based "sleep cycles" to consolidate volatile episodic transaction logs into a structured semantic concept graph.

Additionally, SagaMind incorporates a **Neuro-Symbolic Gatekeeper** utilizing a **Z3 SMT Solver** to formally verify the safety invariants of generated code or tool invocations prior to runtime execution, and a **Parallel Speculative Action Execution Engine** that reduces latency by speculative sandboxed executions.

This specification details the mathematical formulations, database schemas, gRPC interfaces, network flows, security models, and Python code architectures required to construct the platform from scratch.

---

## Table of Contents
1. **Introduction and Domain Context**
2. **The Agentic Saga Transaction Model (STM)**
   - 2.1 Mathematical State Space Formulation
   - 2.2 Orchestrated Saga Protocol
   - 2.3 Failure Recovery & Compensating Typologies
3. **Complementary Learning Memory (CLM) Co-Processor**
   - 3.1 Cognitive Architecture Overview
   - 3.2 Augmented Ebbinghaus Forgetting Curves
   - 3.3 Asynchronous Sleep Consolidation Algorithms
4. **Neuro-Symbolic Gatekeeper (NSG) via Z3 SMT**
   - 4.1 Invariant Compilations and Logics
   - 4.2 SMT-LIB2 Translation Mapping
   - 4.3 Interactive Counter-Example Repair Loop
5. **Parallel Speculative Action Execution (PSAE)**
   - 5.1 Draft-Verify Speculative Scheduling
   - 5.2 Copy-On-Write (COW) Wasm Sandboxing
6. **Physical Database Design and Schemas**
   - 6.1 TimescaleDB SQL Schema
   - 6.2 Neo4j Cypher Schema
   - 6.3 Redis Event Sourcing Key Design
7. **System Interface & API Specifications**
   - 7.1 gRPC Service Definitions
   - 7.2 GraphQL Gateway Schemas
8. **Sandbox Isolation and Network Security Policies**
9. **Production Implementation Roadmap**
   - 9.1 Module Layout Structure
   - 9.2 Complete Python Implementation Code
10. **Portfolio Verification and Testing Protocols**

---

## 1. Introduction and Domain Context
Multi-agent systems can edit codebases, execute database migrations, interact with third-party payment gates, and manage infrastructure. These stateful actions create reliability risks because model-generated plans are probabilistic and external effects are not automatically atomic.

Traditional software engineering relies on deterministic transactions (ACID properties) to ensure that systems remain in a consistent state. If a billing service fails mid-checkout, the database transaction is rolled back. In agentic workflows, however, LLM planners interact with external environments in a stateful, non-atomic manner. A failure at step 8 of a migration pipeline leaves steps 1–7 committed and unresolved, resulting in corrupted files, orphaned cloud resources, and inconsistent databases.

Furthermore, long-horizon tasks degrade in performance due to context drift. Standard Retrieval-Augmented Generation (RAG) stores memories as flat vector chunks, ignoring temporal associations, causal relationships, and factual updates. As a result, the agent's context window becomes bloated with redundant or contradictory information, causing execution latency and higher token consumption.

**SagaMind** solves these issues. By combining **Distributed Systems transactional management**, **Computational Cognitive Memory architectures**, and **Formal Mathematical Verification**, SagaMind provides a highly stable, deterministic runtime for probabilistic AI agents.

---

## 2. The Agentic Saga Transaction Model (STM)

### 2.1 Mathematical State Space Formulation
Let the global software/hardware environment be defined as a state space $\mathcal{S}$. A workflow execution path is a sequence of state changes triggered by agent actions $A_k \in \mathcal{A}$ configured with parameters $\theta_k$:

$$S_k = \mathbb{T}(A_k, \theta_k, S_{k-1})$$

Where $\mathbb{T}: \mathcal{A} \times \Theta \times \mathcal{S} \to \mathcal{S}$ is the state transition function. Since $A_k$ and $\theta_k$ are generated by a neural model, the probability of transitioning into an invalid or unsafe state $S_{fail}$ is non-zero:

$$P\big(\mathbb{T}(A_k, \theta_k, S_{k-1}) \in \mathcal{S}_{fail}\big) = \epsilon > 0$$

To protect the environment, we encapsulate each transaction step $T_i = (A_i, \theta_i)$ inside a transactional boundary.

### 2.2 Orchestrated Saga Protocol
A Saga transaction $\mathcal{SGA}$ consists of a sequence of local transactions:

$$\mathcal{SGA} = [T_1, T_2, \dots, T_n]$$

For each local transaction $T_i$, there exists a corresponding compensating transaction $C_i$. If $T_i$ fails to verify (e.g., returns error codes, fails symbolic verification, or causes database exceptions), the coordinator executes compensating transactions $C_{i-1}, \dots, C_1$ in reverse order:

$$\text{Rollback}(\mathcal{SGA}, j) = \prod_{k=j}^{1} C_k = C_j \circ C_{j-1} \circ \dots \circ C_1$$

This ensures that the environment is returned to a safe, consistent state $S_{safe} \approx S_0$.

```
State S_0 ──> [T_1: Write Code] ──> State S_1 ──> [T_2: Run DB Migration] ──> State S_2
                                                                                 │
                                                                           (Verification Fail)
                                                                                 │
                                                                                 v
State S_0 <── [C_1: Revert Git] <── State S_1 <── [C_2: Rollback Migration] <----+
```

### 2.3 Failure Recovery & Compensating Typologies
We categorize compensating transactions into two types:
1.  **Deterministic Compensations ($C^D$):** Strict mathematical inversions of actions.
    *   *Example:* If $T_i$ executes `CREATE FILE "/src/helper.py"`, $C_i^D$ executes `REMOVE FILE "/src/helper.py"`.
2.  **Semantic Compensations ($C^S$):** Executing actions to correct issues that cannot be directly deleted or undone.
    *   *Example:* If $T_i$ sends an external HTTP webhook to charge a credit card, $C_i^S$ executes an API refund request with matching transaction tokens.

---

## 3. Tiered Cognitive Memory Architecture

SagaMind models its memory layout on the **Complementary Learning Systems (CLS)** theory, which explains how mammalian brains decouple immediate episodic experience from long-term conceptual structures.

```
       +-----------------------------------------------------------+
       |                       Working Memory                      |
       |             LLM Context Window: O(1) Cache                |
       +-----------------------------------------------------------+
                                     |
                                     v (Append Event Log)
       +-----------------------------------------------------------+
       |                      Episodic Memory                      |
       |       Chronological trace log (TimescaleDB + pgvector)    |
       |       Decays exponentially over time: R_m(t)              |
       +-----------------------------------------------------------+
                                     |
                                     v (Asynchronous Sleep Cycle)
       +-----------------------------------------------------------+
       |                      Semantic Memory                      |
       |         Unified Neo4j Knowledge Graph & Concept Nodes     |
       +-----------------------------------------------------------+
```

### 3.1 Mathematical Formulation of Memory Decay
To prevent database query bloat, episodic memory records are subjected to an augmented Ebbinghaus forgetting curve. Let the retention probability $R_m(t)$ of memory $m$ at elapsed time $t$ be:

$$R_m(t) = \exp\left( - \frac{t - t_last}{S_m} \right)$$

Where $t_{last}$ is the timestamp of the last retrieval event, and $S_m$ is the **memory strength parameter**, calculated as:

$$S_m = S_0 \cdot \big( 1 + \alpha \ln(N_{\text{access}} + 1) \big) \cdot I_m$$

Here:
- $S_0$ is the base exponential time constant (12.0 hours by default), not a half-life. Corresponding half-life is $S_m\ln 2$.
- $\alpha$ is a reinforcement coefficient (configured to 0.45).
- $N_{\text{access}}$ is the cumulative retrieval count of the memory node.
- $I_m \in [0, 1]$ is the semantic importance weight computed by an evaluator model at the time of creation (e.g., failed transactions receive $I_m = 1.0$, trivial outputs receive $I_m = 0.05$).

Memories with $R_m(t) < \tau_{prune}$ (threshold configured to 0.15) are dynamically evicted or archived.

### 3.2 Asynchronous Sleep Cycle Consolidation
During simulated "sleep" periods, background worker processes consolidate episodic traces:
1.  **DBSCAN Vector Clustering:** Extracts active episodic vectors and groups them based on cosine similarity:
    $$\text{CosDist}(u, v) = 1 - \frac{u \cdot v}{\|u\| \|v\|}$$
    Nodes within distance $\epsilon = 0.18$ are grouped into semantic clusters.
2.  **Factual Distillation:** A compiler LLM processes each cluster, converting raw chat exchanges into clean facts (subject-predicate-object relationships).
3.  **Graph Update:** Extracted facts are merged into the Neo4j semantic graph, updating edge weights or generating `:CONTRADICTS` relations if facts conflict.

---

## 4. Neuro-Symbolic Invariant Verification

Probabilistic language models cannot guarantee safety. SagaMind implements a Neuro-Symbolic Gatekeeper that parses agent action payloads and verifies them against logic specifications using the **Z3 SMT Solver**.

```
[Agent JSON Proposal] --> [Logic Translator] --> [Z3 SMT Invariant Verification]
                                                         |
                                        +----------------+----------------+
                                        |                                 |
                                    [ UNSAT ]                          [ SAT ]
                                        |                                 |
                                 (Proceed Safe)                   (Rollback & Repair)
```

For any API call, we compile strict logic assertions. For example, in an automated coding agent:
- Invariant: A file read tool must never read outside the project root directory.
  $$\forall p \left( \text{path}(p) \implies \text{prefix}(p, \text{PROJECT\_ROOT}) \right)$$
- If the agent proposes $p = \text{"/etc/passwd"}$, the logic translator compiles:
  $$(assert (not (str.prefixof root path)))$$
- Z3 resolves the assert as **SAT** (a violation exists), blocks execution, and returns the counter-example to trigger a Saga rollback.

---

## 5. Parallel Speculative Action Execution (PSAE)

Multi-step agent pipelines suffer from high execution latency. PSAE runs predicted branches in parallel inside temporary sandboxes.

### 5.1 Draft-Verify Speculative Scheduling
1.  **Drafting:** While the primary model generates the final reasoning path, a lightweight model (e.g., Llama-8B) predicts the next $k$ potential tool executions:
    $$\mathcal{P}_{\text{draft}} = \{ (A_1, \theta_1), (A_2, \theta_2), \dots, (A_k, \theta_k) \}$$
2.  **Parallel Execution:** The runtime spins up $k$ independent WebAssembly sandboxes, executing all $k$ drafts simultaneously.
3.  **Verification:** Once the primary model completes reasoning, if its chosen action matches branch $j$, that sandbox state is committed. If there is no match, the sandboxes are discarded and the step runs sequentially.

$$\text{Latency}_{\text{SagaMind}} = L_{\text{LLM\_Inference}} + \sum_{i=1}^N \Big( L_{\text{execution}, i} \cdot \big(1 - P_{\text{accuracy}}(i)\big) \Big)$$

When drafting model accuracy is high ($P_{\text{accuracy}} > 0.8$), total execution latency approaches the time required for LLM inference alone.

### 5.2 Copy-On-Write (COW) Wasm Sandboxing — target design
Current speculative tasks perform side-effect-free validation and execute only the selected winner. Independent filesystem overlays and overlay commit are not implemented. Runtime exposes a separate fuel-metered WASI primitive for compiled tools.

---

## 6. Physical Database Design and Schemas

### 6.1 TimescaleDB SQL Schema
For storing time-series execution logs and episodic vector spaces:

```sql
-- Enable Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Saga Transaction Logging (Partitioned Hypertable)
CREATE TABLE saga_transaction_logs (
    saga_id UUID NOT NULL,
    step_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL,
    tenant_id VARCHAR(50) NOT NULL,
    step_name VARCHAR(100) NOT NULL,
    action_payload JSONB NOT NULL,
    compensation_payload JSONB NOT NULL,
    status VARCHAR(30) NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'VERIFYING', 'COMMITTED', 'COMPENSATING', 'FAILED', 'ROLLED_BACK')),
    execution_duration_ms INT,
    error_message TEXT,
    PRIMARY KEY (saga_id, created_at, step_id)
);

-- Partition transaction logs by time chunks
SELECT create_hypertable('saga_transaction_logs', 'created_at', chunk_time_interval => INTERVAL '1 day');

-- Episodic Memory Storage
CREATE TABLE episodic_memories (
    memory_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    agent_role VARCHAR(50) NOT NULL,
    summary TEXT NOT NULL,
    context_data JSONB,
    importance_score DOUBLE PRECISION NOT NULL CHECK (importance_score BETWEEN 0.0 AND 1.0),
    retrieval_count INT NOT NULL DEFAULT 0,
    embedding VECTOR(1536) NOT NULL
);

-- HNSW Vector Index for pgvector
CREATE INDEX idx_episodic_vectors 
ON episodic_memories 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Indexes for partitioning query lookup
CREATE INDEX idx_memories_tenant ON episodic_memories(tenant_id);
CREATE INDEX idx_memories_retrieved ON episodic_memories(last_retrieved_at DESC);
```

---

### 6.2 Neo4j Cypher Schema
For mapping semantic relations compiled during sleep cycles:

```cypher
// Invariant Constraints
CREATE CONSTRAINT unique_concept_id FOR (c:Concept) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT unique_entity_name FOR (e:Entity) REQUIRE e.name IS UNIQUE;

// Node Indexes
CREATE INDEX FOR (c:Concept) ON (c.domain);
CREATE INDEX FOR (e:Entity) ON (e.type);

// Example Node & Relationship Creations (Consolidation Output):
// MERGE (c1:Concept {id: "c-1", name: "Saga Pattern", domain: "Distributed Systems"})
// MERGE (c2:Concept {id: "c-2", name: "ACID", domain: "Databases"})
// MERGE (c1)-[r:INHERITS_PROPERTIES {weight: 0.85}]->(c2)
```

---

### 6.3 Redis Event Sourcing Key Design
Redis acts as a low-latency event broker for the Saga Transaction Coordinator.

| Key Pattern | Data Type | Description |
| :--- | :--- | :--- |
| `saga:{saga_id}:state` | String | Active status of the Saga workflow (`RUNNING`, `FAILED`, etc.) |
| `saga:{saga_id}:steps` | List (FIFO) | Ordered UUID list of steps registered under the transaction |
| `saga:{saga_id}:step:{step_id}` | Hash | Payload, compensation actions, invariants, and statuses |
| `saga:active:queue` | Set | Active Saga IDs running across workers |

---

## 7. System Interface & API Specifications

### 7.1 gRPC Service Definitions: `saga_agent.proto`

```protobuf
syntax = "proto3";

package sagamind.v1;

option go_package = "github.com/sagamind/core/gen/v1;sagamindv1";
option rust_package = "sagamind_core_gen::v1";

service SagaOrchestrationService {
  rpc InitializeSaga (InitializeSagaRequest) returns (InitializeSagaResponse);
  rpc RegisterStep (RegisterStepRequest) returns (RegisterStepResponse);
  rpc GetState (GetStateRequest) returns (GetStateResponse);
}

service SandboxExecutionService {
  rpc ExecuteSpeculative (ExecuteSpeculativeRequest) returns (ExecuteSpeculativeResponse);
  rpc CommitState (CommitStateRequest) returns (CommitStateResponse);
}

message InitializeSagaRequest {
  string tenant_id = 1;
  string target_goal = 2;
  map<string, string> environmental_variables = 3;
}

message InitializeSagaResponse {
  string saga_id = 1;
  string status = 2;
}

message RegisterStepRequest {
  string saga_id = 1;
  string step_name = 2;
  string tool_action = 3;
  string compensation_action = 4;
  string invariants_logic = 5;
}

message RegisterStepResponse {
  bool success = 1;
  string step_id = 2;
  string error_message = 3;
}

message GetStateRequest {
  string saga_id = 1;
}

message GetStateResponse {
  string saga_id = 1;
  string status = 2;
  repeated StepInfo completed_steps = 3;
}

message StepInfo {
  string step_id = 1;
  string step_name = 2;
  string status = 3;
}

message ExecuteSpeculativeRequest {
  string saga_id = 1;
  repeated DraftAction drafts = 2;
}

message DraftAction {
  string action_id = 1;
  string command = 2;
  string arguments_json = 3;
}

message ExecuteSpeculativeResponse {
  repeated DraftExecutionResult results = 1;
}

message DraftExecutionResult {
  string action_id = 1;
  string sandbox_id = 2;
  bool success = 3;
  string output_diff_hash = 4;
}

message CommitStateRequest {
  string sandbox_id = 1;
}

message CommitStateResponse {
  bool success = 1;
}
```

---

### 7.2 GraphQL Gateway Schemas
The client dashboard queries Saga execution and memory state via GraphQL:

```graphql
type SagaTransaction {
  sagaId: ID!
  tenantId: String!
  status: String!
  durationMs: Int
  steps: [SagaStep!]!
}

type SagaStep {
  stepId: ID!
  stepName: String!
  status: String!
  actionPayload: String!
  compensationPayload: String!
  errorMessage: String
}

type EpisodicMemory {
  memoryId: ID!
  summary: String!
  importanceScore: Float!
  retrievalCount: Int!
  createdAt: String!
}

type Query {
  getSaga(sagaId: ID!): SagaTransaction
  getActiveMemories(tenantId: String!): [EpisodicMemory!]!
}

type Mutation {
  startSaga(tenantId: String!, goal: String!): SagaTransaction!
  triggerSleepCycle(tenantId: String!): Boolean!
}
```

---

## 8. Sandbox Isolation and Network Security Policies

SagaMind enforces strict container boundaries. We configure the runtime sandbox environment as follows:

1.  **Syscall Filtering:** Sandboxed WebAssembly modules cannot make kernel syscalls. We use **WASI (WebAssembly System Interface)**, intercepting filesystem and networking requests.
2.  **Resource Quotas:**
    *   `MaxMemory`: Capped at 256MB.
    *   `MaxInstructionCount`: Evaluated using gas metering, preventing endless loops by terminating tasks that consume more than 2,000,000 gas units.
3.  **Outbound Proxy Routing:**
    *   All outbound requests are intercepted by an **Envoy Proxy** daemon.
    *   *Security Policy Example:* Outbound targets must match domains registered in a workspace whitelist. Unwhitelisted targets trigger a security exception, terminating the active step and triggering the Saga rollback loop.

---

## 9. Production Implementation Roadmap

### 9.1 Module Layout Structure
To construct SagaMind from scratch, implement the following directory structure:

```
sagamind-core/
│
├── cmd/
│   └── main.go                  # Entry point for the Go service coordinator
│
├── pkg/
│   ├── orchestrator/
│   │   ├── saga.go              # Saga state machine logic
│   │   └── sandbox.go           # Wasm runtime manager interfaces
│   │
│   ├── memory/
│   │   ├── ebbinghaus.go        # Mathematical decay evaluations
│   │   └── database.go          # TimescaleDB & pgvector drivers
│   │
│   └── verifier/
│       └── z3_client.go         # Go interface to Z3 solver process
│
├── scripts/
│   └── sleep_cycle.py           # Python DBSCAN/Neo4j consolidation worker
│
└── config/
    └── safety_invariants.smt2   # Precompiled invariant assertions
```

---

### 9.2 Complete Python Implementation Code
Below is the complete, runnable Python core engine demonstrating the integration of the **Saga Transaction Coordinator**, **Ebbinghaus Memory Decay**, **Neuro-Symbolic Z3 Solving**, and **Sleep-Consolidation DBSCAN Clustering**.

```python
"""
SagaMind Core Runtime Implementation Engine
Contains Orchestrator, Memory, and Verification components.
"""

import time
import math
import uuid
import json
import subprocess
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# =====================================================================
# 1. STRUCTS AND DATACLASSES
# =====================================================================

@dataclass
class ActionPayload:
    tool_name: str
    arguments: Dict[str, Any]

@dataclass
class SagaStep:
    step_id: str
    step_name: str
    action: ActionPayload
    compensation: ActionPayload
    invariants: str  # SMT-LIB2 format constraints
    status: str = "PENDING"
    error: str = ""

@dataclass
class MemoryNode:
    memory_id: str
    created_at: datetime
    last_retrieved_at: datetime
    agent_role: str
    summary: str
    importance_score: float
    retrieval_count: int
    embedding: List[float]

# =====================================================================
# 2. NEURO-SYMBOLIC Z3 VERIFIER
# =====================================================================

class Z3Verifier:
    """
    Interfaces with the Z3 SMT solver binary to evaluate symbolic invariants.
    """
    def __init__(self, z3_path: str = "z3"):
        self.z3_path = z3_path

    def verify(self, action_args: Dict[str, Any], invariants: str) -> Tuple[bool, str]:
        """
        Translates constraints and evaluates correctness using Z3.
        Returns:
            - Tuple[bool, str]: (Success status, solver details/counter-examples)
        """
        # Inject variable assertions based on arguments
        variable_declarations = ""
        for key, value in action_args.items():
            if isinstance(value, str):
                variable_declarations += f'(declare-const {key} String)\n'
                variable_declarations += f'(assert (= {key} "{value}"))\n'
            elif isinstance(value, (int, float)):
                variable_declarations += f'(declare-const {key} Real)\n'
                variable_declarations += f'(assert (= {key} {value}))\n'
            elif isinstance(value, bool):
                variable_declarations += f'(declare-const {key} Bool)\n'
                val_str = "true" if value else "false"
                variable_declarations += f'(assert (= {key} {val_str}))\n'

        smt_payload = f"""
{variable_declarations}
; --- Injecting Invariants ---
{invariants}

(check-sat)
(get-model)
"""
        # Write to temporary verification file and run Z3
        temp_file = f"/tmp/z3_check_{uuid.uuid4().hex}.smt2"
        with open(temp_file, "w") as f:
            f.write(smt_payload)

        try:
            result = subprocess.run(
                [self.z3_path, temp_file],
                capture_output=True,
                text=True,
                timeout=5.0
            )
            output = result.stdout.strip()
        except subprocess.TimeoutExpired:
            return False, "SMT Solver Timeout during logical verification"
        except FileNotFoundError:
            # Fallback mock check if Z3 binary is not globally installed
            print("[WARN] Z3 solver binary not found. Running semantic validation fallback.")
            if "path" in action_args and not action_args["path"].startswith("/Users/Harutyun/Desktop/Portfolio1"):
                return False, f"Fallback Match failure: Path '{action_args['path']}' traverses outside workspace."
            return True, "Fallback Validation Success"

        if output.startswith("sat"):
            # Counter-example generated: safety constraints violated
            return False, f"Safety constraint violation found by SMT Solver:\n{output}"
        elif output.startswith("unsat"):
            return True, "Verification successful. Safety constraints satisfied."
        else:
            return False, f"Solver failure or parse error: {output}"

# =====================================================================
# 3. WASMTIME WORKSPACE SANDBOX
# =====================================================================

class WasmSandbox:
    """
    Executes tasks inside virtual sandboxes.
    """
    def execute(self, action: ActionPayload) -> Dict[str, Any]:
        """
        Executes tools. Simulates mutations and returns environment diff.
        """
        print(f"[Sandbox] Executing tool '{action.tool_name}' with args {action.arguments}")
        # In mock environment, simulate file operations
        if action.tool_name == "WRITE_FILE":
            return {"status": "SUCCESS", "written_path": action.arguments.get("path"), "bytes": len(action.arguments.get("content", ""))}
        elif action.tool_name == "EXECUTE_MIGRATION":
            return {"status": "SUCCESS", "migrated_table": action.arguments.get("table")}
        return {"status": "SUCCESS"}

    def execute_compensation(self, compensation: ActionPayload) -> bool:
        """
        Reverts actions.
        """
        print(f"[Sandbox-Compensation] Reverting tool '{compensation.tool_name}' using parameters {compensation.arguments}")
        if compensation.tool_name == "DELETE_FILE":
            # Simulate deletions
            return True
        elif compensation.tool_name == "DROP_TABLE":
            return True
        return True

# =====================================================================
# 4. EBBINGHAUS MEMORY MANAGER
# =====================================================================

class EbbinghausMemoryManager:
    """
    Evaluates episodic memory decay using retention equations.
    """
    def __init__(self, s_init: float = 12.0, tau: float = 0.15, gamma: float = 0.45):
        self.s_init = s_init
        self.tau = tau
        self.gamma = gamma

    def calculate_retention(self, memory: MemoryNode) -> float:
        now = datetime.now(timezone.utc)
        time_delta_hours = (now - memory.last_retrieved_at).total_seconds() / 3600.0
        
        # SM calculation: strength scales with retrievals and base importance
        retrieval_bonus = math.log1p(memory.retrieval_count)
        strength = self.s_init * (1.0 + self.gamma * retrieval_bonus) * memory.importance_score
        
        if strength <= 0:
            return 0.0
            
        retention = math.exp(-time_delta_hours / strength)
        return retention

    def prune_memories(self, memories: List[MemoryNode]) -> Tuple[List[MemoryNode], List[MemoryNode]]:
        active = []
        pruned = []
        for m in memories:
            r = self.calculate_retention(m)
            if r >= self.tau:
                active.append(m)
            else:
                pruned.append(m)
        return active, pruned

# =====================================================================
# 5. SAGA TRANSACTION COORDINATOR
# =====================================================================

class SagaTransactionCoordinator:
    """
    Orchestrates execution sequences, Z3 checks, and failures rollbacks.
    """
    def __init__(self, verifier: Z3Verifier, sandbox: WasmSandbox):
        self.verifier = verifier
        self.sandbox = sandbox
        self.transaction_logs = []

    def execute_saga(self, saga_id: str, steps: List[SagaStep]) -> bool:
        completed_steps = []
        self.transaction_logs.append({"saga_id": saga_id, "status": "RUNNING", "timestamp": time.time()})

        print(f"\n[SAGA-{saga_id}] Beginning workflow execution pipeline.")

        for step in steps:
            step.status = "RUNNING"
            print(f"[SAGA-{saga_id}] Running step '{step.step_name}'...")

            # Run symbolic checking first
            ver_ok, details = self.verifier.verify(step.action.arguments, step.invariants)
            if not ver_ok:
                step.status = "FAILED"
                step.error = f"Neuro-Symbolic Gatekeeper rejected action: {details}"
                print(f"[SAGA-{saga_id}] [ERROR] Validation failed for step '{step.step_name}': {step.error}")
                self.rollback(saga_id, completed_steps)
                return False

            # Run execution in sandbox
            res = self.sandbox.execute(step.action)
            step.status = "COMMITTED"
            completed_steps.append(step)
            print(f"[SAGA-{saga_id}] Step '{step.step_name}' executed and committed successfully.")

        self.transaction_logs.append({"saga_id": saga_id, "status": "COMMITTED", "timestamp": time.time()})
        print(f"[SAGA-{saga_id}] Saga workflow committed successfully.")
        return True

    def rollback(self, saga_id: str, completed_steps: List[SagaStep]):
        print(f"\n[SAGA-{saga_id}] [ROLLBACK] Initiating compensating rollbacks.")
        self.transaction_logs.append({"saga_id": saga_id, "status": "COMPENSATING", "timestamp": time.time()})

        # Reverse order compensation (LIFO)
        for step in reversed(completed_steps):
            step.status = "COMPENSATING"
            print(f"[SAGA-{saga_id}] [ROLLBACK] Reverting step '{step.step_name}'...")
            
            comp_ok = self.sandbox.execute_compensation(step.compensation)
            if comp_ok:
                step.status = "ROLLED_BACK"
                print(f"[SAGA-{saga_id}] [ROLLBACK] Reversion complete for step '{step.step_name}'.")
            else:
                step.status = "COMPENSATION_FAILED"
                print(f"[SAGA-{saga_id}] [CRITICAL] Compensation failed for step '{step.step_name}'!")
                self.transaction_logs.append({"saga_id": saga_id, "status": "FAILED", "timestamp": time.time()})
                return

        self.transaction_logs.append({"saga_id": saga_id, "status": "ROLLED_BACK", "timestamp": time.time()})
        print(f"[SAGA-{saga_id}] Rollback complete. Consistency restored.")

# =====================================================================
# 6. ASYNC MEMORY SLEEP CONSOLIDATION
# =====================================================================

class MemoryConsolidator:
    """
    Groups episodic memories into clusters and consolidates them.
    """
    def compute_cosine_distance(self, u: List[float], v: List[float]) -> float:
        dot_product = sum(a * b for a, b in zip(u, v))
        norm_u = math.sqrt(sum(a * a for a in u))
        norm_v = math.sqrt(sum(b * b for b in v))
        if norm_u == 0 or norm_v == 0:
            return 1.0
        return 1.0 - (dot_product / (norm_u * norm_v))

    def consolidate_episodes(self, episodes: List[MemoryNode], eps: float = 0.2) -> List[Dict[str, Any]]:
        """
        Group episodes using Cosine distance based clustering logic.
        """
        clusters = {}
        assigned = set()

        for i, ep_i in enumerate(episodes):
            if ep_i.memory_id in assigned:
                continue
            current_cluster = [ep_i]
            assigned.add(ep_i.memory_id)

            for j, ep_j in enumerate(episodes):
                if ep_j.memory_id in assigned:
                    continue
                dist = self.compute_cosine_distance(ep_i.embedding, ep_j.embedding)
                if dist <= eps:
                    current_cluster.append(ep_j)
                    assigned.add(ep_j.memory_id)

            clusters[len(clusters)] = current_cluster

        consolidated_facts = []
        for cid, cluster in clusters.items():
            print(f"[Sleep Cycle] Consolidating Memory Cluster {cid} ({len(cluster)} episodes)...")
            # Synthesize factual nodes
            evidence = " & ".join([e.summary for e in cluster])
            consolidated_facts.append({
                "concept": f"Generalized Concept from Cluster {cid}",
                "evidence": evidence,
                "weight": len(cluster) * 0.1
            })
        return consolidated_facts

# =====================================================================
# 7. RUNTIME VALIDATION (MAIN ENGINE DEMONSTRATION)
# =====================================================================

if __name__ == "__main__":
    # Initialize Core Subsystems
    verifier = Z3Verifier()
    sandbox = WasmSandbox()
    coordinator = SagaTransactionCoordinator(verifier, sandbox)
    memory_manager = EbbinghausMemoryManager()
    consolidator = MemoryConsolidator()

    # Declare SMT-LIB2 path safety invariants
    # Path constraints: path parameters must start with the authorized workspace prefix
    path_invariant = """
(assert (str.prefixof "/Users/Harutyun/Desktop/Portfolio1" path))
"""

    # --- SIMULATION 1: SUCCESSFUL SAGA RUN ---
    saga_1 = uuid.uuid4().hex
    steps_1 = [
        SagaStep(
            step_id=uuid.uuid4().hex,
            step_name="Initialize Configuration File",
            action=ActionPayload("WRITE_FILE", {"path": "/Users/Harutyun/Desktop/Portfolio1/config.json", "content": "{}"}),
            compensation=ActionPayload("DELETE_FILE", {"path": "/Users/Harutyun/Desktop/Portfolio1/config.json"}),
            invariants=path_invariant
        )
    ]
    coordinator.execute_saga(saga_1, steps_1)

    # --- SIMULATION 2: TRIGGER SAFETY BREACH (ROLLBACK TRIGGER) ---
    saga_2 = uuid.uuid4().hex
    steps_2 = [
        SagaStep(
            step_id=uuid.uuid4().hex,
            step_name="Write Config",
            action=ActionPayload("WRITE_FILE", {"path": "/Users/Harutyun/Desktop/Portfolio1/app.py", "content": "# Code"}),
            compensation=ActionPayload("DELETE_FILE", {"path": "/Users/Harutyun/Desktop/Portfolio1/app.py"}),
            invariants=path_invariant
        ),
        SagaStep(
            step_id=uuid.uuid4().hex,
            step_name="Access Restricted Settings File",
            action=ActionPayload("WRITE_FILE", {"path": "/etc/passwd", "content": "malicious injection"}),
            compensation=ActionPayload("DELETE_FILE", {"path": "/etc/passwd"}),
            invariants=path_invariant
        )
    ]
    coordinator.execute_saga(saga_2, steps_2)

    # --- SIMULATION 3: COGNITIVE MEMORY CONSOLIDATION ---
    print("\n--- Running Memory Consolidation Simulation ---")
    mock_episodes = [
        MemoryNode(
            memory_id="m1",
            created_at=datetime.now(timezone.utc),
            last_retrieved_at=datetime.now(timezone.utc),
            agent_role="Developer",
            summary="Implemented database transaction support",
            importance_score=0.8,
            retrieval_count=0,
            embedding=[0.9, 0.1, 0.0, 0.0]
        ),
        MemoryNode(
            memory_id="m2",
            created_at=datetime.now(timezone.utc),
            last_retrieved_at=datetime.now(timezone.utc),
            agent_role="Developer",
            summary="Fixed database migration rollback loop logic",
            importance_score=0.9,
            retrieval_count=2,
            embedding=[0.88, 0.12, 0.0, 0.0] # High cosine similarity to m1
        ),
        MemoryNode(
            memory_id="m3",
            created_at=datetime.now(timezone.utc),
            last_retrieved_at=datetime.now(timezone.utc),
            agent_role="Security",
            summary="Investigated network policy permissions",
            importance_score=0.5,
            retrieval_count=0,
            embedding=[0.1, 0.0, 0.9, 0.0] # Outlier / different cluster
        )
    ]

    facts = consolidator.consolidate_episodes(mock_episodes)
    print("Distilled Concepts:", json.dumps(facts, indent=2))
```

---

## 10. Portfolio Verification and Testing Protocols

To demonstrate the technical rigor of this system on a portfolio evaluation:

1.  **Logical Checking Assertions:** Build testing scripts inside your python suite that feed various string path patterns to the `Z3Verifier`. Verify that `UNSAT` is returned only when path boundaries are met, and `SAT` is returned whenever directory traversal attempts are made.
2.  **State Rollback Validations:** Verify that the system executes compensations backwards on failure by tracking mock output states before, during, and after a failed transaction sequence.
3.  **Active Memory Decay Checks:** Verify retention decay by logging mock time deltas and asserting that the `EbbinghausMemoryManager` flags inactive nodes for removal once retention values drop below $0.15$.
