package com.cartpole.physics;

import java.util.Random;

public class CartPoleSystem {

    private double x;
    private double x_dot;
    private double theta;
    private double theta_dot;

    private PhysicsParameters params;
    private final ImperfectionModel imperfectionModel;
    private final Random random = new Random();

    // Thresholds for termination
    public static final double X_THRESHOLD = 2.4;
    public static final double THETA_THRESHOLD_RADIANS = 12 * 2 * Math.PI / 360;

    public CartPoleSystem(ImperfectionModel imperfectionModel) {
        this.imperfectionModel = imperfectionModel;
        reset();
    }

    public void reset() {
        // Randomize initial state slightly
        // uniform random between -0.05 and 0.05
        x = random.nextDouble() * 0.1 - 0.05;
        x_dot = random.nextDouble() * 0.1 - 0.05;
        theta = random.nextDouble() * 0.1 - 0.05;
        theta_dot = random.nextDouble() * 0.1 - 0.05;

        // Randomize physics parameters
        params = PhysicsParameters.createWithImperfections(imperfectionModel);
    }

    /**
     * Updates the system state for one time step.
     * @param action 0 for push left, 1 for push right.
     */
    public void step(int action) {
        double force = (action == 1) ? params.forceMag : -params.forceMag;

        // Apply wheel asymmetry
        // If asymmetry is positive, right (1) is stronger, left (-1) is weaker?
        // Let's say asymmetry is a factor added to the force multiplier.
        // If asymmetry is 0.1, right force is 1.1*F, left force is 0.9*F?
        // Or simpler: force = force * (1 + asymmetry * direction)
        // direction is 1 for right, -1 for left.
        // if asymmetry=0.1: right -> 1.1, left -> (-1)*(1-0.1) = -0.9.
        // This makes sense for "roues légèrement différentes".
        double direction = (action == 1) ? 1.0 : -1.0;
        force *= (1.0 + params.wheelAsymmetryFactor * direction);

        // Apply friction (opposes velocity)
        // force -= friction * x_dot
        if (params.frictionCart > 0) {
            force -= params.frictionCart * x_dot;
        }

        double costheta = Math.cos(theta);
        double sintheta = Math.sin(theta);

        double totalMass = params.getTotalMass();
        double poleMassLength = params.getPoleMassLength();

        double temp = (force + poleMassLength * theta_dot * theta_dot * sintheta) / totalMass;
        double thetaacc = (PhysicsParameters.GRAVITY * sintheta - costheta * temp) /
                          (params.length * (4.0 / 3.0 - params.massPole * costheta * costheta / totalMass));
        double xacc = temp - poleMassLength * thetaacc * costheta / totalMass;

        // Euler integration
        x += PhysicsParameters.TAU * x_dot;
        x_dot += PhysicsParameters.TAU * xacc;
        theta += PhysicsParameters.TAU * theta_dot;
        theta_dot += PhysicsParameters.TAU * thetaacc;
    }

    public boolean isDone() {
        return x < -X_THRESHOLD || x > X_THRESHOLD ||
               theta < -THETA_THRESHOLD_RADIANS || theta > THETA_THRESHOLD_RADIANS;
    }

    /**
     * Returns the state observation [x, x_dot, theta, theta_dot]
     * with optional sensor noise added.
     */
    public double[] getState() {
        double noise = imperfectionModel.getSensorNoiseStdDev();
        double n1 = 0, n2 = 0, n3 = 0, n4 = 0;

        if (noise > 0) {
            n1 = random.nextGaussian() * noise;
            n2 = random.nextGaussian() * noise;
            n3 = random.nextGaussian() * noise;
            n4 = random.nextGaussian() * noise;
        }

        return new double[]{x + n1, x_dot + n2, theta + n3, theta_dot + n4};
    }

    // Getters for current actual state (for UI visualization, noise-free usually)
    public double getX() { return x; }
    public double getXDot() { return x_dot; }
    public double getTheta() { return theta; }
    public double getThetaDot() { return theta_dot; }

    public PhysicsParameters getParams() {
        return params;
    }
}
