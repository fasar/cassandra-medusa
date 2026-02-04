package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import java.util.List;

@Command(name = "restore-cluster", description = "Restore Cassandra cluster")
public class RestoreClusterCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name", required = true)
    String backupName;

    @Option(names = "--seed-target", description = "Seed of the target hosts")
    String seedTarget;

    @Option(names = "--temp-dir", description = "Directory for temporary storage", defaultValue = "/tmp")
    String tempDir;

    @Option(names = "--host-list", description = "List of nodes to restore with the associated target host")
    String hostList;

    @Option(names = "--keep-auth", description = "Keep system_auth as found on the nodes")
    boolean keepAuth;

    @Option(names = {"-y", "--bypass-checks"}, description = "Bypasses the security check for restoring a cluster")
    boolean bypassChecks;

    @Option(names = "--verify", description = "Verify that the cluster is operational after the restore completes")
    boolean verify;

    @Option(names = "--keyspace", description = "Restore tables from this keyspace")
    List<String> keyspaces;

    @Option(names = "--table", description = "Restore only this table")
    List<String> tables;

    @Option(names = "--use-sstableloader", description = "Use the sstableloader to load the backup into the cluster")
    boolean useSstableloader;

    @Option(names = {"--parallel-restores", "-pr"}, description = "Number of concurrent synchronous ssh sessions", defaultValue = "500")
    int parallelRestores;

    @Option(names = "--version-target", description = "Target Cassandra version", defaultValue = "3.11.9")
    String versionTarget;

    @Option(names = "--ignore-racks", description = "Disable matching nodes based on rack topology")
    boolean ignoreRacks;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("Restore Cluster called");
    }
}
