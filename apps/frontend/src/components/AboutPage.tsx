export function AboutPage() {
  return (
    <div className="about-page">
      <div className="about-hero">
        <h1 className="about-title">An Interactive CAP Theorem Demo</h1>
        <p className="about-lead">
          Two independent backends, NestJS (TypeScript) owning a MongoDB collection and FastAPI (Python)
          owning a PostgreSQL table, replicate state through Redis pub/sub. You can trigger network partitions,
          watch the backends diverge or freeze depending on the consistency model, then heal the partition
          and observe reconciliation in real time.
        </p>
      </div>

      <section className="about-section">
        <h2 className="about-section-title">System Architecture</h2>
        <p className="about-section-desc">
          Two backends, each with their own database, connected by a Redis replication channel.
          The outbox pattern guarantees at-least-once delivery even across process restarts.
        </p>
        <div className="about-diagram">
          <img src="/diagrams/system-architecture.svg" alt="System architecture: Frontend, NestJS+MongoDB, FastAPI+PostgreSQL, Redis" />
        </div>
      </section>

      <section className="about-section">
        <h2 className="about-section-title">The CAP Triangle</h2>
        <p className="about-section-desc">
          Every distributed system faces a forced choice when a partition occurs: sacrifice write availability
          (CP) or sacrifice consistency (AP). P (partition tolerance) is mandatory in any real network.
        </p>
        <div className="about-diagram about-diagram-narrow">
          <img src="/diagrams/cap-triangle.svg" alt="CAP theorem triangle showing CP and AP mode positions" />
        </div>
      </section>

      <section className="about-section">
        <h2 className="about-section-title">How the Modes Work</h2>
        <div className="about-cards">
          <div className="about-card about-card-normal">
            <div className="about-card-badge">Normal</div>
            <p>Both backends accept writes. Each write is applied locally, broadcast over WebSocket immediately,
            then published to Redis via the outbox relay (at-least-once delivery). The other backend subscribes, applies the value to its own storage,
            and broadcasts to its clients.</p>
          </div>
          <div className="about-card about-card-ap">
            <div className="about-card-badge about-card-badge-ap">AP Partition</div>
            <p>Replication is suppressed: no Redis events are published or consumed. Both backends
            continue accepting writes. Drag each marker independently and watch them diverge. On heal,
            the chosen heuristic (LWW, NestJS-wins, or FastAPI-wins) picks the winner.</p>
          </div>
          <div className="about-card about-card-cp">
            <div className="about-card-badge about-card-badge-cp">CP Partition</div>
            <p>Both backends reject all writes with HTTP 503. Neither table changes, so both
            markers stay frozen and consistent. Reads still succeed. The system trades availability
            for the guarantee that no divergence occurs.</p>
          </div>
        </div>
      </section>

      <section className="about-section">
        <h2 className="about-section-title">Architecture &amp; Data Flow</h2>
        <p className="about-section-desc">
          Three modes, three data flow patterns. Green paths are active; red paths are blocked or cut.
        </p>
        <div className="about-diagram">
          <img src="/diagrams/architecture-dataflow.svg" alt="Architecture data flow in Normal, AP, and CP modes" />
        </div>
      </section>

      <section className="about-section">
        <h2 className="about-section-title">Normal Write Sequence</h2>
        <p className="about-section-desc">
          A single PATCH immediately updates the writing backend's WebSocket clients (step 3) before
          the replication event reaches the other backend (step 7); the latency gap is part of the demo.
        </p>
        <div className="about-diagram">
          <img src="/diagrams/sequence-write-normal.svg" alt="Sequence diagram for a normal write flow" />
        </div>
      </section>

      <section className="about-section">
        <h2 className="about-section-title">Outbox Write Flow</h2>
        <p className="about-section-desc">
          Every write atomically inserts a position record and an outbox record in the same transaction.
          A relay process reads the outbox and publishes to Redis, deleting each entry only after
          a successful publish, guaranteeing at-least-once delivery even if the process crashes mid-flight.
        </p>
        <div className="about-diagram">
          <img src="/diagrams/outbox-write-flow.svg" alt="Outbox write flow: atomic DB write, relay publishes to Redis, deletes outbox entry on success" />
        </div>
      </section>

      <section className="about-section about-section-links">
        <h2 className="about-section-title">Go deeper</h2>
        <div className="about-links">
          <a className="about-link" href="https://github.com/atannus/interactive-cap-demo/blob/main/ARCHITECTURE.md" target="_blank" rel="noopener noreferrer">
            Full architecture document →
          </a>
          <a className="about-link" href="https://github.com/atannus/interactive-cap-demo" target="_blank" rel="noopener noreferrer">
            View source on GitHub →
          </a>
        </div>
        <p className="about-stack">React · NestJS · FastAPI · MongoDB · PostgreSQL · Redis</p>
      </section>
    </div>
  )
}
