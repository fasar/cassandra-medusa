package com.cartpole.ui;

import com.cartpole.ai.DqnTrainer;
import com.cartpole.ai.ModelRunner;
import com.cartpole.physics.CartPoleSystem;
import com.cartpole.physics.ImperfectionModel;
import com.cartpole.physics.PhysicsParameters;
import javafx.animation.AnimationTimer;
import javafx.application.Application;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.scene.Scene;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.control.*;
import javafx.scene.input.KeyCode;
import javafx.scene.layout.BorderPane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.stage.Stage;

import java.io.File;
import java.io.IOException;

public class MainApp extends Application {

    private static final double WIDTH = 800;
    private static final double HEIGHT = 600;
    private static final String MODEL_PATH = "cartpole_model.bin";

    private CartPoleSystem system;
    private ImperfectionModel imperfectionModel;
    private GameCanvas canvas;
    private AnimationTimer timer;
    private ModelRunner modelRunner;

    private boolean isAiPlaying = false;
    private volatile boolean isTraining = false;
    private boolean leftPressed = false;
    private boolean rightPressed = false;

    // UI Controls
    private Slider massVarianceSlider;
    private Slider frictionVarianceSlider;
    private Slider asymmetrySlider;
    private Slider sensorNoiseSlider;
    private Label statusLabel;
    private Button trainButton;
    private ToggleButton aiToggle;
    private Button resetButton;

    @Override
    public void start(Stage primaryStage) {
        imperfectionModel = new ImperfectionModel();
        system = new CartPoleSystem(imperfectionModel);

        canvas = new GameCanvas(WIDTH, HEIGHT);

        BorderPane root = new BorderPane();
        root.setCenter(canvas);
        root.setRight(createControls());

        Scene scene = new Scene(root, WIDTH, HEIGHT);
        setupInput(scene);

        primaryStage.setTitle("Cart-Pole Deep RL");
        primaryStage.setScene(scene);
        primaryStage.show();

        loadModel();
        startLoop();
    }

    private VBox createControls() {
        VBox box = new VBox(10);
        box.setPadding(new Insets(10));
        box.setStyle("-fx-background-color: #f0f0f0;");
        box.setPrefWidth(250);

        statusLabel = new Label("Status: Ready");

        trainButton = new Button("Train Agent");
        trainButton.setMaxWidth(Double.MAX_VALUE);
        trainButton.setOnAction(e -> startTraining());

        aiToggle = new ToggleButton("AI Control");
        aiToggle.setMaxWidth(Double.MAX_VALUE);
        aiToggle.setDisable(true);
        aiToggle.setOnAction(e -> {
            isAiPlaying = aiToggle.isSelected();
            if (isAiPlaying && system.isDone()) system.reset();
            // Refocus canvas/scene to capture key events if needed, though AI doesn't need keys
            canvas.requestFocus();
        });

        resetButton = new Button("Reset Simulation");
        resetButton.setMaxWidth(Double.MAX_VALUE);
        resetButton.setOnAction(e -> {
            system.reset();
            // Ensure physics parameters are updated from sliders if reset calls createWithImperfections
            // Actually system.reset() re-creates params using current imperfectionModel.
            // And imperfectionModel is updated by sliders.
        });

        // Sliders
        massVarianceSlider = new Slider(0, 0.5, 0); // 0 to 50%
        frictionVarianceSlider = new Slider(0, 1.0, 0); // 0 to 1.0
        asymmetrySlider = new Slider(0, 0.2, 0); // 0 to 0.2
        sensorNoiseSlider = new Slider(0, 0.1, 0); // 0 to 0.1 std dev

        configureSlider(massVarianceSlider);
        configureSlider(frictionVarianceSlider);
        configureSlider(asymmetrySlider);
        configureSlider(sensorNoiseSlider);

        box.getChildren().addAll(
                new Label("Controls"),
                statusLabel,
                trainButton,
                aiToggle,
                resetButton,
                new Separator(),
                new Label("Imperfections"),
                new Label("Mass Variance (0-50%):"), massVarianceSlider,
                new Label("Friction Variance (0-1.0):"), frictionVarianceSlider,
                new Label("Wheel Asymmetry (0-0.2):"), asymmetrySlider,
                new Label("Sensor Noise (StdDev):"), sensorNoiseSlider
        );
        return box;
    }

    private void configureSlider(Slider slider) {
        slider.setShowTickLabels(true);
        slider.setShowTickMarks(true);
        slider.valueProperty().addListener((obs, oldVal, newVal) -> updateImperfections());
    }

    private void updateImperfections() {
        if (imperfectionModel == null) return;
        imperfectionModel.setMassVariance(massVarianceSlider.getValue());
        imperfectionModel.setFrictionVariance(frictionVarianceSlider.getValue());
        imperfectionModel.setWheelAsymmetry(asymmetrySlider.getValue());
        imperfectionModel.setSensorNoiseStdDev(sensorNoiseSlider.getValue());
    }

    private void setupInput(Scene scene) {
        scene.setOnKeyPressed(e -> {
            if (e.getCode() == KeyCode.LEFT) leftPressed = true;
            if (e.getCode() == KeyCode.RIGHT) rightPressed = true;
        });
        scene.setOnKeyReleased(e -> {
            if (e.getCode() == KeyCode.LEFT) leftPressed = false;
            if (e.getCode() == KeyCode.RIGHT) rightPressed = false;
        });
    }

    private void startLoop() {
        timer = new AnimationTimer() {
            @Override
            public void handle(long now) {
                if (isTraining) {
                    // Maybe show training progress spinner or something
                    return;
                }

                if (!system.isDone()) {
                    int action = -1;
                    if (isAiPlaying && modelRunner != null) {
                        try {
                            double[] state = system.getState();
                            action = modelRunner.getAction(state);
                        } catch (Exception ex) {
                            ex.printStackTrace();
                            isAiPlaying = false;
                            aiToggle.setSelected(false);
                            statusLabel.setText("Error in AI");
                        }
                    } else {
                        // Manual
                        if (leftPressed && !rightPressed) action = 0; // Left
                        else if (rightPressed && !leftPressed) action = 1; // Right
                    }

                    if (action != -1) {
                        system.step(action);
                    }
                }

                canvas.draw(system);
            }
        };
        timer.start();
    }

    private void startTraining() {
        if (isTraining) return;
        isTraining = true;
        statusLabel.setText("Status: Training...");
        trainButton.setDisable(true);
        aiToggle.setDisable(true);
        resetButton.setDisable(true);

        new Thread(() -> {
            try {
                // We create a new imperfection model for training to ensure we pass the current settings
                // Actually we can just pass the current object if it's thread safe or copy it.
                // ImperfectionModel is just data.
                DqnTrainer.train(imperfectionModel, MODEL_PATH);

                Platform.runLater(() -> {
                    statusLabel.setText("Status: Training Complete");
                    isTraining = false;
                    trainButton.setDisable(false);
                    resetButton.setDisable(false);
                    loadModel();
                });
            } catch (Exception e) {
                e.printStackTrace();
                Platform.runLater(() -> {
                    statusLabel.setText("Status: Training Failed: " + e.getMessage());
                    isTraining = false;
                    trainButton.setDisable(false);
                    resetButton.setDisable(false);
                });
            }
        }).start();
    }

    private void loadModel() {
        File f = new File(MODEL_PATH);
        if (f.exists()) {
            try {
                modelRunner = new ModelRunner(MODEL_PATH);
                aiToggle.setDisable(false);
                statusLabel.setText("Status: Model Loaded");
            } catch (IOException e) {
                e.printStackTrace();
                statusLabel.setText("Status: Model Load Failed");
            }
        } else {
             statusLabel.setText("Status: No Model Found");
        }
    }

    public static void main(String[] args) {
        launch(args);
    }
}
