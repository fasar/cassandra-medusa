package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

@Command(name = "status", description = "Show status of backups")
public class StatusCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name", required = true)
    String backupName;

    @Option(names = {"-o", "--output"}, description = "Output format (text, json)", defaultValue = "text")
    String output;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("Status called");
    }
}
