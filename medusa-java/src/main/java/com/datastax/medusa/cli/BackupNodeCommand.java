package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

@Command(name = "backup-node", aliases = {"backup"}, description = "Backup single Cassandra node")
public class BackupNodeCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name")
    String backupName;

    @Option(names = "--stagger", description = "Drop initial backups if longer than a duration in seconds")
    Integer stagger;

    @Option(names = "--enable-md5-checks", description = "Use md5 calculations")
    boolean enableMd5Checks;

    @Option(names = "--mode", description = "Backup mode", defaultValue = "differential")
    String mode;

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
        System.out.println("Backup Node called");
        if (backupName != null) {
            System.out.println("Backup name: " + backupName);
        }
    }
}
