package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import java.util.List;

@Command(name = "download", description = "Download backup")
public class DownloadCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name", required = true)
    String backupName;

    @Option(names = "--download-destination", description = "Download destination", required = true)
    String downloadDestination;

    @Option(names = "--keyspace", description = "Restore tables from this keyspace")
    List<String> keyspaces;

    @Option(names = "--table", description = "Restore only this table")
    List<String> tables;

    @Option(names = "--ignore-system-keyspaces", description = "Do not download cassandra system keyspaces")
    boolean ignoreSystemKeyspaces;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("Download called");
    }
}
