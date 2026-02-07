package com.cartpole.physics;

/**
 * Defines the range of imperfections to be applied to the simulation.
 */
public class ImperfectionModel {
    private double massVariance = 0.0; // Percentage (0.0 to 1.0)
    private double frictionVariance = 0.0; // Percentage
    private double wheelAsymmetry = 0.0; // Factor (0.0 = perfect, 0.1 = 10% diff)
    private double sensorNoiseStdDev = 0.0; // Standard deviation for Gaussian noise

    public ImperfectionModel() {
    }

    public double getMassVariance() {
        return massVariance;
    }

    public void setMassVariance(double massVariance) {
        this.massVariance = massVariance;
    }

    public double getFrictionVariance() {
        return frictionVariance;
    }

    public void setFrictionVariance(double frictionVariance) {
        this.frictionVariance = frictionVariance;
    }

    public double getWheelAsymmetry() {
        return wheelAsymmetry;
    }

    public void setWheelAsymmetry(double wheelAsymmetry) {
        this.wheelAsymmetry = wheelAsymmetry;
    }

    public double getSensorNoiseStdDev() {
        return sensorNoiseStdDev;
    }

    public void setSensorNoiseStdDev(double sensorNoiseStdDev) {
        this.sensorNoiseStdDev = sensorNoiseStdDev;
    }
}
