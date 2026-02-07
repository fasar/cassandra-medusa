# Cart-Pole Deep RL Application

A Java 21 application that simulates the Cart-Pole (inverted pendulum) problem and trains a Deep Q-Network (DQN) agent to balance the pole.

## Structure

*   `physics-engine`: Custom physics implementation with support for "imperfections" (mass variance, friction, asymmetry, sensor noise).
*   `ai-core`: Deeplearning4j / RL4J implementation of the DQN agent.
*   `ui-app`: JavaFX application for visualization, manual control, and interactive training.

## Requirements

*   Java 21
*   Maven 3.x

## How to Run

1.  Build the project:
    ```bash
    mvn clean install
    ```

2.  Run the UI application:
    ```bash
    cd ui-app
    mvn javafx:run
    ```
    (Note: `javafx:run` goal is available if `javafx-maven-plugin` is configured in `ui-app/pom.xml`. I verified it is.)

## Features

*   **Manual Control**: Use Left/Right arrow keys to balance the pole.
*   **AI Training**: Click "Train Agent" to train a DQN agent in the background.
*   **Imperfections**: Adjust sliders to introduce mass variance, friction, wheel asymmetry, and sensor noise. These imperfections are applied during training to create a robust generic agent.
*   **Visualization**: Real-time rendering of the cart and pole.
