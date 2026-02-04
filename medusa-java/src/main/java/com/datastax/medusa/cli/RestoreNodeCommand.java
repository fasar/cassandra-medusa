package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import java.util.List;

@Command(name = "restore-node", description = "Restore single Cassandra node")
public class RestoreNodeCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name", required = true)
    String backupName;

    @Option(names = "--temp-dir", description = "Directory for temporary storage", defaultValue = "/tmp")
    String tempDir;

    @Option(names = "--in-place", description = "Indicates if the restore happens on the node the backup was done on.", defaultValue = "true")
    boolean inPlace;

    @Option(names = "--remote", description = "Indicates if the restore happens on a remote node.", hidden = true) // handled via logic usually
    boolean remote;

    @Option(names = "--keep-auth", description = "Keep system_auth as found on the nodes")
    boolean keepAuth;

    @Option(names = "--seeds", description = "Nodes to wait for after downloading backup but before starting C*")
    String seeds;

    @Option(names = "--verify", description = "Verify that the cluster is operational after the restore completes")
    boolean verify;

    @Option(names = "--keyspace", description = "Restore tables from this keyspace")
    List<String> keyspaces;

    @Option(names = "--table", description = "Restore only this table")
    List<String> tables;

    @Option(names = "--use-sstableloader", description = "Use the sstableloader to load the backup into the cluster")
    boolean useSstableloader;

    @Option(names = "--version-target", description = "Target Cassandra version", defaultValue = "3.11.9")
    String versionTarget;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("Restore Node called");
    }
}
