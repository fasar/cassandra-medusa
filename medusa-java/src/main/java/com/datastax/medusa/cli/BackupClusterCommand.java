package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

@Command(name = "backup-cluster", description = "Backup Cassandra cluster")
public class BackupClusterCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name")
    String backupName;

    @Option(names = "--seed-target", description = "Seed of the target hosts")
    String seedTarget;

    @Option(names = "--stagger", description = "Drop initial backups if longer than a duration in seconds")
    Integer stagger;

    @Option(names = "--enable-md5-checks", description = "Use md5 calculations")
    boolean enableMd5Checks;

    @Option(names = "--mode", description = "Backup mode", defaultValue = "differential")
    String mode;

    @Option(names = "--temp-dir", description = "Directory for temporary storage", defaultValue = "/tmp")
    String tempDir;

    @Option(names = {"--parallel-snapshots", "-ps"}, description = "Number of concurrent synchronous ssh sessions for snapshots", defaultValue = "500")
    int parallelSnapshots;

    @Option(names = {"--parallel-uploads", "-pu"}, description = "Number of concurrent synchronous ssh sessions for uploads", defaultValue = "1")
    int parallelUploads;

    @Option(names = "--keep-snapshot", description = "Dont delete snapshot after successful backup")
    boolean keepSnapshot;

    @Option(names = "--use-existing-snapshot", description = "Dont create snapshot, only backup it")
    boolean useExistingSnapshot;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("Backup Cluster called");
    }
}
