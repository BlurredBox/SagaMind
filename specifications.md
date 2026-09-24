# SagaMind: Technical Specifications & Core Algorithms

> **Reference status:** This document includes protocol and schema material that may describe target behavior. `ARCHITECTURE.md` is authoritative for shipped behavior; executable migrations, source, and tests override pseudocode here.

This document contains gRPC definitions, database schemas, and algorithm pseudocode for the **SagaMind** transaction-safe runtime and memory co-processor.

---

## 1. gRPC Protocol Buffers: `saga_agent.proto`

SagaMind microservices communicate via gRPC. Below is the service and message definition for the Saga Transaction Coordinator (STC) and the Speculative Execution Orchestrator (SEO).

```protobuf
syntax = "proto3";

package sagamind.v1;

option go_package = "github.com/sagamind/core/gen/v1;sagamindv1";
option rust_package = "sagamind_core_gen::v1";

service SagaOrchestrator {
  rpc StartSaga (StartSagaRequest) returns (StartSagaResponse);
  rpc SubmitStep (SubmitStepRequest) returns (SubmitStepResponse);
  rpc GetSagaStatus (GetSagaStatusRequest) returns (GetSagaStatusResponse);
}

service SpeculativeEngine {
  rpc SpeculateActions (SpeculateActionsRequest) returns (SpeculateActionsResponse);
  rpc CommitSandbox (CommitSandboxRequest) returns (CommitSandboxResponse);
}

enum TransactionStatus {
  TRANSACTION_STATUS_UNSPECIFIED = 0;
  TRANSACTION_STATUS_PENDING = 1;
  TRANSACTION_STATUS_RUNNING = 2;
  TRANSACTION_STATUS_VERIFYING = 3;
  TRANSACTION_STATUS_COMMITTED = 4;
  TRANSACTION_STATUS_COMPENSATING = 5;
  TRANSACTION_STATUS_FAILED = 6;
  TRANSACTION_STATUS_ROLLED_BACK = 7;
}

message StartSagaRequest {
  string tenant_id = 1;
  string workspace_id = 2;
  string workflow_goal = 3;
  map<string, string> global_variables = 4;
}

message StartSagaResponse {
  string saga_id = 1;
  TransactionStatus status = 2;
}

message SubmitStepRequest {
  string saga_id = 1;
  string step_id = 2;
  string agent_role = 3;
  string proposed_action = 4;
  string compensation_action = 5; // Deterministic command or API invocation to revert this step
  string logic_invariants = 6;     // Invariants in SMT-LIB2 format
}

message SubmitStepResponse {
  bool verification_success = 1;
  string validation_feedback = 2; // In case of Z3 solver failure, counter-example feedback
  TransactionStatus status = 3;
}

message GetSagaStatusRequest {
  string saga_id = 1;
}

message GetSagaStatusResponse {
  string saga_id = 1;
  TransactionStatus status = 2;
  repeated StepHistory steps = 3;
}

message StepHistory {
  string step_id = 1;
  string agent_role = 2;
  TransactionStatus status = 3;
  int64 completed_at = 4;
}

message SpeculateActionsRequest {
  string context = 1;
  int32 max_branches = 2;
  repeated string tool_whitelist = 3;
}

message SpeculateActionsResponse {
  repeated SpeculativeBranch branches = 1;
}

message SpeculativeBranch {
  string sandbox_id = 1;
  string predicted_tool = 2;
  string predicted_arguments = 3;
  string resulting_state_diff = 4;
}

message CommitSandboxRequest {
  string sandbox_id = 1;
}

message CommitSandboxResponse {
  bool commit_success = 1;
  string committed_state_hash = 2;
}
```

---

## 2. Relational & Vector Database Schema (PostgreSQL/TimescaleDB)

SagaMind uses TimescaleDB for time-series event metrics and transactional histories, combined with `pgvector` for episodic vector embeddings.

```sql
-- Enable necessary extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Saga Execution Log (Time-Series partitioned table)
CREATE TABLE saga_execution_logs (
    saga_id UUID NOT NULL,
    step_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMPTZ NOT NULL,
    tenant_id VARCHAR(100) NOT NULL,
    agent_id VARCHAR(100) NOT NULL,
    step_name VARCHAR(150) NOT NULL,
    action_payload JSONB,
    compensation_payload JSONB,
    status VARCHAR(50) NOT NULL,
    PRIMARY KEY (saga_id, timestamp, step_id)
);

-- Convert to hypertable for time-series optimizations
SELECT create_hypertable('saga_execution_logs', 'timestamp');

-- Episodic Memory Store (Episodic Hippocampal trace)
CREATE TABLE episodic_memories (
    memory_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(100) NOT NULL,
    workspace_id VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    agent_role VARCHAR(100) NOT NULL,
    experience_summary TEXT NOT NULL,
    raw_interaction JSONB,
    importance_score DOUBLE PRECISION NOT NULL CHECK (importance_score BETWEEN 0.0 AND 1.0),
    retrieval_count INT NOT NULL DEFAULT 0,
    embedding VECTOR(1536) -- Matches OpenAI text-embedding-3-small dimensions
);

-- Indices for rapid vector lookup (HNSW index for cosine distance)
CREATE INDEX ON episodic_memories 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Indexing tenant segregation and chronological retrieval
CREATE INDEX idx_episodic_tenant_workspace 
ON episodic_memories (tenant_id, workspace_id);

CREATE INDEX idx_episodic_created_at 
ON episodic_memories (created_at DESC);
```

---

## 3. Graph Database Schema (Neo4j Cypher Schema)

The neocortical semantic memory layer maps entity relationships. Relational constraints and node indexes are defined using Cypher:

```cypher
// Enforce unique constraints on concepts/entities
CREATE CONSTRAINT unique_concept_id FOR (c:Concept) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT unique_entity_name FOR (e:Entity) REQUIRE e.name IS UNIQUE;

// Indexes for fast lookup
CREATE INDEX FOR (c:Concept) ON (c.category);
CREATE INDEX FOR (e:Entity) ON (e.type);

// Example Node Types:
// (Concept {id: "uuid", name: "Saga Pattern", category: "System Design", explanation: "...", embedding: [...]})
// (Entity {name: "TimescaleDB", type: "Database", properties: "..."})
// (CodebaseUnit {file_path: "/src/main.rs", symbols: ["StartSaga", "SubmitStep"]})

// Example Edge Labels:
// - [:DEPENDS_ON] -> Connects CodebaseUnit to Entity/Concept
// - [:INFLUENCED_BY] -> Connects Concepts dynamically during consolidation
// - [:CONTRADICTS] -> Logic solver edge flagging inconsistent semantic facts
// - [:REPLACES] -> Linked during system refactor traces
```

---

## 4. Core Algorithms Pseudocode

### 4.1 Saga Orchestration Engine Loop

This algorithm manages execution steps, verification gates, and recursive compensation execution in the event of failure.

```python
import time
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass

@dataclass
class Step:
    step_id: str
    action: Dict[str, Any]
    compensation: Dict[str, Any]
    logic_invariants: str
    status: str = "PENDING"

class SagaCoordinator:
    def __init__(self, db_client, symbolic_verifier, execution_sandbox):
        self.db = db_client
        self.verifier = symbolic_verifier
        self.sandbox = execution_sandbox

    def execute_saga(self, saga_id: str, steps: List[Step]) -> bool:
        completed_steps: List[Step] = []
        
        self.db.update_saga_status(saga_id, "RUNNING")
        
        for idx, step in enumerate(steps):
            step.status = "RUNNING"
            self.db.log_step_state(saga_id, step)
            
            # Execute step inside sandbox environment
            sandbox_state = self.sandbox.execute(step.action)
            
            # Pass outputs to the Neuro-Symbolic Logic Verification Gate
            verification_ok, feedback = self.verifier.verify(
                state=sandbox_state, 
                invariants=step.logic_invariants
            )
            
            if verification_ok:
                step.status = "COMMITTED"
                self.db.log_step_state(saga_id, step)
                completed_steps.append(step)
                # Commit sandbox mutations to main production context
                self.sandbox.commit(step.step_id)
            else:
                step.status = "FAILED"
                self.db.log_step_state(saga_id, step)
                
                # Initiate compensations rollback
                self.rollback_saga(saga_id, completed_steps, failure_reason=feedback)
                return False
                
        self.db.update_saga_status(saga_id, "COMMITTED")
        return True

    def rollback_saga(self, saga_id: str, completed_steps: List[Step], failure_reason: str):
        self.db.update_saga_status(saga_id, "COMPENSATING")
        
        # Compensations are executed in reverse order (LIFO)
        for step in reversed(completed_steps):
            step.status = "COMPENSATING"
            self.db.log_step_state(saga_id, step)
            
            # Run compensating tool execution
            compensation_result = self.sandbox.execute_compensation(step.compensation)
            
            if compensation_result.success:
                step.status = "ROLLED_BACK"
            else:
                step.status = "COMPENSATION_FAILED"
                self.db.log_step_state(saga_id, step)
                # Escalate to human operator / circuit breaker log
                self.db.raise_critical_alert(saga_id, step.step_id, error=compensation_result.error)
                self.db.update_saga_status(saga_id, "FAILED")
                return
                
            self.db.log_step_state(saga_id, step)
            
        self.db.update_saga_status(saga_id, "ROLLED_BACK")
        # Log failure to episodic memory with high importance
        self.db.save_failure_experience(saga_id, failure_reason)
```

### 4.2 Ebbinghaus Memory Decay and Importance Scoring

Manages memory retention values to determine which episodic traces to retain, strengthen, or discard.

```python
import math
from datetime import datetime, timezone

class EbbinghausMemoryManager:
    def __init__(self, base_strength: float = 10.0, pruning_threshold: float = 0.15):
        self.S_init = base_strength
        self.tau = pruning_threshold

    def calculate_retention(self, memory: Dict[str, Any]) -> float:
        """
        Computes the current retention score R_m(t) of an episodic memory node.
        Formula: R_m(t) = e^(-t_delta / S_m)
        """
        now = datetime.now(timezone.utc)
        t_delta = (now - memory['last_retrieved_at']).total_seconds() / 3600.0 # Time delta in hours
        
        # Calculate dynamic memory strength S_m
        # Incorporates retrieval counts and original importance (0.0 to 1.0)
        retrieval_bonus = math.log1p(memory['retrieval_count'])
        strength = self.S_init * (1.0 + retrieval_bonus) * memory['importance_score']
        
        # Avoid division by zero
        if strength <= 0:
            return 0.0
            
        retention = math.exp(-t_delta / strength)
        return retention

    def evaluate_retention_states(self, memories: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
        """
        Iterates over a list of memories, evaluating retention.
        Returns:
            - keep_list: memory IDs to retain in active vector store.
            - prune_list: memory IDs that have decayed below threshold.
        """
        keep_list = []
        prune_list = []
        
        for m in memories:
            r = self.calculate_retention(m)
            if r >= self.tau:
                keep_list.append(m['memory_id'])
            else:
                prune_list.append(m['memory_id'])
                
        return keep_list, prune_list
```

### 4.3 Asynchronous Sleep Cycle Consolidation

Executes clustering, rule distillation, and semantic integration during simulated agent rest cycles.

```python
from sklearn.cluster import DBSCAN
import numpy as np

class SleepConsolidationWorker:
    def __init__(self, vector_client, graph_client, distillation_llm):
        self.vector_db = vector_client
        self.graph_db = graph_client
        self.llm = distillation_llm

    def run_sleep_cycle(self, tenant_id: str, workspace_id: str):
        # 1. Retrieve all episodic memories pending consolidation
        episodes = self.vector_db.get_unconsolidated_episodes(tenant_id, workspace_id)
        if len(episodes) < 10:
            return # Insufficient data to warrant consolidation

        embeddings = np.array([ep['embedding'] for ep in episodes])
        
        # 2. Perform density-based spatial clustering (DBSCAN) on embeddings
        # Cosine metric checks semantic similarity clusters
        clustering = DBSCAN(eps=0.2, min_samples=3, metric='cosine').fit(embeddings)
        labels = clustering.labels_
        
        clustered_episodes = {}
        outliers = []
        
        for idx, label in enumerate(labels):
            if label == -1:
                outliers.append(episodes[idx])
            else:
                clustered_episodes.setdefault(label, []).append(episodes[idx])
                
        # 3. Consolidate and abstract each cluster
        for label, cluster in clustered_episodes.items():
            # Format raw experiences for the summarizer agent
            raw_text_traces = "\n---\n".join([
                f"Role: {ep['agent_role']}\nSummary: {ep['experience_summary']}\nDetails: {ep['raw_interaction']}"
                for ep in cluster
            ])
            
            # Distill memories into abstract concepts and relationships
            distilled_facts = self.llm.distill_memories_to_relations(raw_text_traces)
            
            # 4. Upsert generalized facts and edges to the Neo4j Knowledge Graph
            for fact in distilled_facts:
                self.graph_db.upsert_relationship(
                    source=fact['subject'],
                    relation=fact['predicate'],
                    target=fact['object'],
                    evidence=fact['evidence_summary']
                )
                
            # Mark consolidated episodes as processed and update retention strengths
            self.vector_db.mark_as_consolidated([ep['memory_id'] for ep in cluster])

        # 5. Clean up heavily decayed outlier episodes to prevent vector pollution
        for ep in outliers:
            retention = self.vector_db.get_memory_retention(ep['memory_id'])
            if retention < 0.15:
                # Remove if not important and long unused
                self.vector_db.delete_memory(ep['memory_id'])
```
