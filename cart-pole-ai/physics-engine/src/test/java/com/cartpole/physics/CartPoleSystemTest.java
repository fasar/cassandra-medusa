package com.cartpole.physics;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class CartPoleSystemTest {

    @Test
    public void testDefaultInitialization() {
        ImperfectionModel model = new ImperfectionModel();
        CartPoleSystem system = new CartPoleSystem(model);

        // Check initial state is near 0
        assertTrue(Math.abs(system.getX()) < 0.1);
        assertTrue(Math.abs(system.getTheta()) < 0.1);

        // Check params are default
        PhysicsParameters params = system.getParams();
        assertEquals(PhysicsParameters.MASSCART_DEFAULT, params.massCart, 0.001);
    }

    @Test
    public void testStepUpdatesState() {
        ImperfectionModel model = new ImperfectionModel();
        CartPoleSystem system = new CartPoleSystem(model);

        double xBefore = system.getX();
        system.step(1); // Push right
        double xAfter = system.getX();

        // Should move? Actually after 1 step (0.02s), velocity increases, position increases slightly.
        // x = x + tau * x_dot. Initial x_dot is small random.
        // It might not move much but x_dot should change due to force.

        assertNotEquals(xBefore, xAfter, 0.00001);
    }

    @Test
    public void testImperfections() {
        ImperfectionModel model = new ImperfectionModel();
        model.setMassVariance(0.5); // 50% variance

        boolean varied = false;
        for (int i = 0; i < 100; i++) {
            CartPoleSystem system = new CartPoleSystem(model);
            if (Math.abs(system.getParams().massCart - PhysicsParameters.MASSCART_DEFAULT) > 0.01) {
                varied = true;
                break;
            }
        }
        assertTrue(varied, "Mass should vary with imperfection model");
    }
}
