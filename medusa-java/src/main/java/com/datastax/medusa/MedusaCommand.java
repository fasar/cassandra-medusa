package com.datastax.medusa;

public interface MedusaCommand extends Runnable {
    void setMedusaConfiguration(MedusaConfiguration config);
}
