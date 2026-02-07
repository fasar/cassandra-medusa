package com.cartpole.ui;

import com.cartpole.physics.CartPoleSystem;
import com.cartpole.physics.PhysicsParameters;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.paint.Color;

public class GameCanvas extends Canvas {

    private static final double SCALE = 100.0; // pixels per meter
    private static final double CART_WIDTH = 50.0;
    private static final double CART_HEIGHT = 30.0;
    private static final double POLE_WIDTH = 10.0;

    public GameCanvas(double width, double height) {
        super(width, height);
    }

    public void draw(CartPoleSystem system) {
        GraphicsContext gc = getGraphicsContext2D();
        double w = getWidth();
        double h = getHeight();

        // Clear
        gc.setFill(Color.WHITE);
        gc.fillRect(0, 0, w, h);

        // Draw ground
        double groundY = h * 0.7;
        gc.setStroke(Color.BLACK);
        gc.setLineWidth(2.0);
        gc.strokeLine(0, groundY, w, groundY);

        if (system == null) return;

        double x = system.getX();
        double theta = system.getTheta();
        double poleLength = system.getParams().length * 2; // Physics has half-length

        // Center of the screen is x=0
        double screenX = w / 2.0 + x * SCALE;
        double screenY = groundY;

        // Draw Cart
        gc.setFill(Color.BLUE);
        gc.fillRect(screenX - CART_WIDTH / 2, screenY - CART_HEIGHT / 2, CART_WIDTH, CART_HEIGHT);

        // Draw Pole
        // Pole pivot is at cart center
        double poleLenPixels = poleLength * SCALE;
        double poleEndX = screenX + Math.sin(theta) * poleLenPixels;
        double poleEndY = screenY - Math.cos(theta) * poleLenPixels;

        gc.setStroke(Color.BROWN);
        gc.setLineWidth(POLE_WIDTH);
        gc.strokeLine(screenX, screenY, poleEndX, poleEndY);

        // Draw Pivot
        gc.setFill(Color.RED);
        gc.fillOval(screenX - 3, screenY - 3, 6, 6);

        // Draw info text
        gc.setFill(Color.BLACK);
        gc.fillText(String.format("X: %.2f", x), 10, 20);
        gc.fillText(String.format("Theta: %.2f deg", Math.toDegrees(theta)), 10, 40);

        if (system.isDone()) {
            gc.setFill(Color.RED);
            gc.fillText("FAILED", w/2 - 20, 50);
        }
    }
}
