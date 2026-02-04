package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

@Command(name = "list-backups", description = "List backups")
public class ListBackupsCommand implements MedusaCommand {

    @Option(names = "--show-all", description = "List all backups in the bucket")
    boolean showAll;

    @Option(names = "--output", description = "Output format (text, json)", defaultValue = "text")
    String output;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("List Backups called");
    }
}
