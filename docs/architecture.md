# Architecture decisions

## Local writer, provider-independent inference

The gateway and exporter run together on the machine with the target checkout.
Hosted inference is the default; Ollama is an optional backend. LiteLLM is confined
to one adapter, so it can later be replaced without changing the database or domain.
This is a constrained model-assisted workflow, not an autonomous tool-using agent.

## Durable ingestion, hourly processing

Ingestion commits before inference. A stored revision is the compare-and-set token
for completing work. Pending rows survive crashes; a process lock prevents two
workers. The deadline lives in SQLite. A batch is capped at 100 attempts so a busy
server cannot trigger an unbounded hourly loop. Transport failure and validation
failure both consume retry attempts, keeping malformed model output observable.

## Human review as a separate state

Messages move `pending -> done` or `pending -> failed`. An empty extraction is a
successful `done` result. Context items move `pending -> approved/rejected`, and
review may be reversed by an operator. An edit deletes derived records and queues
a new source revision; new context IDs prevent approving a stale item by accident.
Deletion purges the source and records, retaining only an ID tombstone.

## SQLite and Markdown have different jobs

SQLite retains source, status, evidence, model/prompt identity, and operational
state. Markdown is an intentionally small, replaceable read model for agents.
Exports hold a SQLite write reservation while reading and replacing the file so
concurrent exporters cannot publish older snapshots after newer mutations. A
filesystem failure can still leave a stale file: the DB remains authoritative and
the live worker retries export every second. Review commands report write failures.

## Trust boundary

Allowlisting is enforced before storing or sending messages to a provider. The
model receives content only, not Discord credentials, author IDs, tools, or filesystem
access. A strict validator checks fields, bounded size, categories, finite confidence,
and exact evidence. Neither JSON validation nor Markdown escaping eliminates semantic
prompt injection. Explicit human approval and low-trust instructions in the export
provide additional boundaries; agents must still treat imported text as evidence.

## Follow-up work that warrants measurement

1. Label realistic synthetic conversations and measure category precision/recall,
   supported-evidence rate, false decision promotion, latency, and cost by provider.
2. Add thread/reply windows with per-claim source IDs and invalidation of all
   dependent records when any source changes.
3. Add offline history reconciliation and retention before unattended deployment.
4. Add an authenticated local review UI if CLI review proves too cumbersome.
5. Consider retrieval/search only after the bounded Markdown export becomes a
   measured limitation; avoid adding a vector database for appearance alone.
