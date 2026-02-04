# Medusa Migration to Java - High Level Plan

## Session 1: Foundation & Configuration
*   **Goal**: Establish the project structure, CLI skeleton, and configuration management.
*   **Steps**:
    1.  Initialize Maven project in `medusa-java`.
    2.  Add dependencies (Picocli, SnakeYAML/Apache Commons, JUnit 5, Mockito).
    3.  Implement Configuration Loader (INI parsing, Env vars, CLI overrides).
    4.  Implement CLI Entry Point (Main class, subcommands).
    5.  Port `tests/config_test.py` to Java.
    6.  Verify Configuration loading and CLI help output.

## Session 2: Core Domain & Cassandra Integration
*   **Goal**: Define domain objects and interaction with Cassandra.
*   **Steps**:
    1.  Define Domain Models (Backup, Node, Cluster, Keyspace, Table).
    2.  Implement `CassandraConfig` and connection logic.
    3.  Implement Nodetool wrapper (Process execution).
    4.  Implement CQL client (using Datastax Java Driver).
    5.  Port `cassandra_utils.py` and related tests.

## Session 3: Storage Layer Abstraction
*   **Goal**: Implement the storage driver system.
*   **Steps**:
    1.  Define `Storage` interface.
    2.  Implement `LocalStorage` driver.
    3.  Implement `S3Storage` driver (AWS SDK).
    4.  Implement `GCSStorage` driver (Google Cloud SDK).
    5.  Implement `AzureStorage` driver (Azure SDK).
    6.  Port `storage` tests.

## Session 4: Backup Logic
*   **Goal**: Implement single node and cluster backup workflows.
*   **Steps**:
    1.  Implement `BackupManager`.
    2.  Implement `NodeBackup` logic (Snapshot, Upload, Metadata).
    3.  Implement `ClusterBackup` orchestration.
    4.  Port `backup_node.py` and `backup_cluster.py` logic.

## Session 5: Restore Logic
*   **Goal**: Implement restore workflows.
*   **Steps**:
    1.  Implement `RestoreManager`.
    2.  Implement `NodeRestore` (Download, Place, Restart).
    3.  Implement `ClusterRestore` orchestration.
    4.  Port `restore_node.py` and `restore_cluster.py`.

## Session 6: Advanced Features & Operations
*   **Goal**: Verify, Purge, Indexing, and Monitoring.
*   **Steps**:
    1.  Implement `Verify` command (Integrity checks).
    2.  Implement `Purge` command (Cleanup old backups).
    3.  Implement Indexing (if applicable/needed in new architecture).
    4.  Add Monitoring hooks (Metrics).

## Session 7: Integration & Polish
*   **Goal**: End-to-end testing and finalization.
*   **Steps**:
    1.  Set up Integration Tests (using TestContainers or CCM).
    2.  Verify feature parity against Python implementation.
    3.  Package and Release preparation.
