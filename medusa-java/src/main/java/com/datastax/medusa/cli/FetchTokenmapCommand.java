package com.datastax.medusa.cli;

import com.datastax.medusa.MedusaCommand;
import com.datastax.medusa.MedusaConfiguration;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

@Command(name = "fetch-tokenmap", description = "Get the token/node mapping for a specific backup")
public class FetchTokenmapCommand implements MedusaCommand {

    @Option(names = "--backup-name", description = "Backup name", required = true)
    String backupName;

    private MedusaConfiguration config;

    @Override
    public void setMedusaConfiguration(MedusaConfiguration config) {
        this.config = config;
    }

    @Override
    public void run() {
        System.out.println("Fetch Tokenmap called");
    }
}
