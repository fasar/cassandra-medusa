package com.cartpole.physics;

import java.util.Random;

/**
 * Holds the physical constants for a single simulation run.
 */
public class PhysicsParameters {
    public static final double GRAVITY = 9.8;
    public static final double MASSCART_DEFAULT = 1.0;
    public static final double MASSPOLE_DEFAULT = 0.1;
    public static final double TOTAL_MASS_DEFAULT = (MASSCART_DEFAULT + MASSPOLE_DEFAULT);
    public static final double LENGTH_DEFAULT = 0.5; // actually half the pole's length
    public static final double POLEMASS_LENGTH_DEFAULT = (MASSPOLE_DEFAULT * LENGTH_DEFAULT);
    public static final double FORCE_MAG_DEFAULT = 10.0;
    public static final double TAU = 0.02; // seconds between state updates

    // Instance variables that can vary per episode
    public final double massCart;
    public final double massPole;
    public final double length;
    public final double forceMag;
    public final double frictionCart; // Coefficient of friction
    public final double frictionPole; // Coefficient of friction
    public final double wheelAsymmetryFactor; // >1 or <1 multiplier for one side

    public PhysicsParameters(double massCart, double massPole, double length, double forceMag,
                             double frictionCart, double frictionPole, double wheelAsymmetryFactor) {
        this.massCart = massCart;
        this.massPole = massPole;
        this.length = length;
        this.forceMag = forceMag;
        this.frictionCart = frictionCart;
        this.frictionPole = frictionPole;
        this.wheelAsymmetryFactor = wheelAsymmetryFactor;
    }

    public static PhysicsParameters createDefault() {
        return new PhysicsParameters(MASSCART_DEFAULT, MASSPOLE_DEFAULT, LENGTH_DEFAULT, FORCE_MAG_DEFAULT, 0.0, 0.0, 0.0);
    }

    /**
     * Generates parameters with imperfections based on the model.
     */
    public static PhysicsParameters createWithImperfections(ImperfectionModel model) {
        Random rand = new Random();

        // Vary mass
        double massCart = MASSCART_DEFAULT * (1.0 + (rand.nextDouble() * 2 - 1) * model.getMassVariance());
        double massPole = MASSPOLE_DEFAULT * (1.0 + (rand.nextDouble() * 2 - 1) * model.getMassVariance());

        // Vary Friction (if non-zero variance, we assume base friction is small but existing, say 0.1, or just purely additive variance)
        // Let's assume base friction is 0.0 but we add random friction if variance is set, or vary around a base if we had one.
        // The prompt says "Friction variable sur les roues". Let's say random friction between 0 and variance.
        double frictionCart = rand.nextDouble() * model.getFrictionVariance();
        double frictionPole = rand.nextDouble() * model.getFrictionVariance() * 0.1; // Pole friction usually smaller

        // Wheel asymmetry: one wheel has more friction or drag?
        // Or maybe the force applied is asymmetric?
        // "roues légèrement différentes l'une de l'autre" -> affects movement straightness or effective force.
        // I will model it as a multiplier on the force when moving in one direction vs the other.
        // e.g. moving right is 1.0, moving left is 0.95.
        // Let's say asymmetry factor is a small deviation from 0.0.
        // If asymmetry is 0.1, then factor is between -0.1 and 0.1.
        double asymmetry = (rand.nextDouble() * 2 - 1) * model.getWheelAsymmetry();

        return new PhysicsParameters(massCart, massPole, LENGTH_DEFAULT, FORCE_MAG_DEFAULT,
                                     frictionCart, frictionPole, asymmetry);
    }

    public double getTotalMass() {
        return massCart + massPole;
    }

    public double getPoleMassLength() {
        return massPole * length;
    }
}
