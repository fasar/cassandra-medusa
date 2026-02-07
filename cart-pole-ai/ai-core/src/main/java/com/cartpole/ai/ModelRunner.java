package com.cartpole.ai;

import org.deeplearning4j.rl4j.policy.DQNPolicy;
import org.deeplearning4j.rl4j.space.Box;
import org.nd4j.linalg.factory.Nd4j;

import java.io.IOException;

public class ModelRunner {
    private final DQNPolicy<Box> policy;

    public ModelRunner(String modelPath) throws IOException {
        this.policy = DQNPolicy.load(modelPath);
    }

    public int getAction(double[] state) {
        // Use double[] constructor for Box
        Box observation = new Box(state);
        // Pass INDArray to policy
        return policy.nextAction(observation.getData());
    }
}
