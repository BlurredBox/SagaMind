# SagaMind: System Architecture and Subsystem Specifications

> **Status:** This document mixes implemented components with target architecture. `ARCHITECTURE.md` is the sole source of truth for shipped behavior; `research_paper.md` and executable tests contain evidence. COW filesystem overlays, an 8B drafter, a solver cluster, and a Rust core runtime are design targets, not current features.

This document outlines the engineering specifications, network protocols, isolation mechanisms, and database structures of the **SagaMind** transaction-safe runtime.

---

## 1. Subsystem Decomposition

SagaMind's current core execution engine is Python. A small optional Rust/PyO3 cosine-distance kernel exists, but it is not the coordinator or a safety boundary.

```mermaid
graph TD
    Client[Client App] -->|gRPC / WebSockets| API[SagaMind API Gateway]
    
    subgraph Core Orchestration Engine
        API --> STC[Saga Transaction Coordinator]
        STC --> SEO[Speculative Execution Orchestrator]
        STC --> NSG[Neuro-Symbolic Gate]
        
        SEO -->|Async execution| SandboxPool[Wasmtime Wasm Sandbox Pool]
        NSG -->|AST Parsing & Logic Compilation| Z3Server[Z3 SMT Solver Cluster]
    end
    
    subgraph Memory & Storage Layer
        STC --> MCP[Memory Co-Processor]
        MCP -->|Episodic vectors| Timescale[(TimescaleDB + pgvector)]
        MCP -->|Semantic graph| Neo4j[(Neo4j Graph Database)]
        MCP -->|Sleep Cycle trigger| Workers[Offline Consolidation Workers]
    end
    
    classDef main fill:#3A7DFF,stroke:#333,stroke-width:2px,color:#fff;
    classDef data fill:#00C79A,stroke:#333,stroke-width:2px,color:#fff;
    classDef verify fill:#FF9F1C,stroke:#333,stroke-width:2px,color:#fff;
    
    class STC,SEO main;
    class MCP,Timescale,Neo4j data;
    class NSG,Z3Server verify;
```

---

## 2. Technical Architecture of Core Subsystems

### 2.1 Saga Transaction Coordinator (STC)
The STC oversees the execution of distributed multi-agent pipelines. 
*   **State Machine:** Implemented as a stateful event-sourced engine. Transitions are logged to an append-only transaction ledger in Redis for performance and sub-millisecond recovery.
*   **Rollback Protocol:** If a step fails verification, the STC queries the Memory Co-Processor to retrieve compensating actions. The STC then coordinates rollbacks (LIFO order) in isolated environments.

### 2.2 Memory Co-Processor (MCP)
Decoupled from primary inference, the MCP processes memory updates in the background.
*   **Episodic Store (TimescaleDB):** Captures high-frequency temporal interaction logs. Uses Timescale hypertables partitioned by `timestamp` and tenant IDs. Includes an HNSW vector index for high-speed cosine similarity lookup of embeddings.
*   **Semantic Graph (Neo4j):** Maps generalized conceptual clusters. This graph is queryable by Cypher queries, allowing agents to navigate relational systems context.
*   **Consolidation Workers:** Triggers background DBSCAN clustering of episodic logs during low-load periods, compiling concepts using a specialized summarization model.

### 2.3 Neuro-Symbolic Gate (NSG)
Bridges the probabilistic output of LLMs with safety verification systems.
*   **Translation Engine:** Converts output JSON schemas into first-order logical representations.
*   **SMT Verification Service:** Interfaces directly with a cluster of Z3 solvers via standard input/output streams over Unix sockets for sub-millisecond performance.

### 2.4 Speculative Execution Orchestrator (SEO)
Maximizes system throughput by pre-computing likely tool execution paths.
*   **Drafter Agent (target):** A future lightweight model may predict next tool-call parameters.
*   **Sandbox Pools (target):** COW filesystem overlays are not implemented. Current speculation validates without side effects and executes only the winner.

---

## 3. Communication and Data Sequences

### 3.1 Normal Flow: Transaction Verification and Log
```mermaid
sequenceDiagram
    autonumber
    participant Gateway as API Gateway
    participant STC as Saga Coordinator
    participant SEO as Speculative Engine
    participant NSG as Symbolic Gate
    participant MCP as Memory Co-Processor
    participant Sandbox as Wasm Sandbox

    Gateway->>STC: Start Agentic Saga (Goal)
    STC->>MCP: Query Context & Semantic Memory
    MCP-->>STC: Context Nodes & Embeddings
    STC->>SEO: Predict and Pre-execute Speculative Steps
    SEO->>Sandbox: Execute Tool Drafts in Parallel Sandbox
    SEO-->>STC: Return Branch Diff Hash & Sandboxes
    STC->>STC: Confirm Primary Reason Path
    Alt Primary matches speculative path i
        STC->>NSG: Validate State change i against Logic Invariants
        NSG->>NSG: Z3 Solver verification
        NSG-->>STC: OK (UNSAT)
        STC->>Sandbox: Commit Sandbox i changes to main state
        STC->>MCP: Write Episodic Memory Log
        STC-->>Gateway: Transaction Step Succeeded
    Else Speculative Mismatch
        STC->>Sandbox: Discard Sandboxes
        STC->>Sandbox: Execute traditional sequential tool run
        STC->>NSG: Validate Sequential output
        STC->>MCP: Log state & retrieve embeddings
        STC-->>Gateway: Return response
    End
```

### 3.2 Rollback Flow: Eventual Consistency Restoration
```mermaid
sequenceDiagram
    autonumber
    participant STC as Saga Coordinator
    participant Sandbox as Sandbox State
    participant NSG as Symbolic Gate
    participant MCP as Memory Co-Processor

    Note over STC, Sandbox: Step T1 and T2 executed successfully.
    STC->>Sandbox: Execute Step T3
    STC->>NSG: Verify T3 outputs against Invariants
    NSG->>NSG: Z3 Solver returns SAT (Invariant Violated)
    NSG-->>STC: Reject output with counter-example data
    Note over STC: Trigger Rollback!
    STC->>MCP: Get Compensations for T3, T2, T1
    MCP-->>STC: Return list: [C3, C2, C1]
    STC->>Sandbox: Execute C3 (Revert DB records)
    STC->>Sandbox: Execute C2 (Delete files)
    STC->>Sandbox: Execute C1 (Undo external state)
    STC->>MCP: Log Saga failure and save counter-example as important trace
```

---

## 4. Sandbox Isolation and Containerization

SagaMind enforces strict boundaries between agent processes and physical host resources.

```
       +---------------------------------------------+
       |             Host Operating System           |
       +---------------------------------------------+
                              |
              +---------------+---------------+
              |                               |
              v                               v
+--------------------------+    +--------------------------+
|  Sandbox 1: Wasmtime VM  |    |  Sandbox 2: Wasmtime VM  |
|  - Limits: 256MB RAM     |    |  - Limits: 256MB RAM     |
|  - COW File System       |    |  - COW File System       |
|  - Outbound Proxy Gate   |    |  - Outbound Proxy Gate   |
+--------------------------+    +--------------------------+
```

1.  **Memory Limits:** Each sandbox runs in a dedicated WebAssembly (Wasmtime) runtime instance, capped at 256MB memory.
2.  **File System Virtualization (target):** Future sandboxes may use COW disk mounts. Current speculative drafts do not mutate the filesystem during validation.
3.  **Outbound Network Proxying:** All external HTTP/gRPC requests are forced through a system gateway proxy that runs validation checks on request headers, filters against whitelists, and rate-limits agent API calls.
