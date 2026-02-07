package com.cartpole.ai;

import com.cartpole.physics.CartPoleSystem;
import com.cartpole.physics.ImperfectionModel;
import org.deeplearning4j.gym.StepReply;
import org.deeplearning4j.rl4j.mdp.MDP;
import org.deeplearning4j.rl4j.space.ArrayObservationSpace;
import org.deeplearning4j.rl4j.space.Box;
import org.deeplearning4j.rl4j.space.DiscreteSpace;
import org.deeplearning4j.rl4j.space.ObservationSpace;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.factory.Nd4j;

public class CartPoleMdp implements MDP<Box, Integer, DiscreteSpace> {

    private final CartPoleSystem system;
    private final ImperfectionModel imperfectionModel;
    private final DiscreteSpace actionSpace = new DiscreteSpace(2);
    private final ObservationSpace<Box> observationSpace = new ArrayObservationSpace<>(new int[]{4});

    public CartPoleMdp(ImperfectionModel imperfectionModel) {
        this.imperfectionModel = imperfectionModel;
        this.system = new CartPoleSystem(imperfectionModel);
    }

    @Override
    public ObservationSpace<Box> getObservationSpace() {
        return observationSpace;
    }

    @Override
    public DiscreteSpace getActionSpace() {
        return actionSpace;
    }

    @Override
    public Box reset() {
        system.reset();
        return new Box(system.getState());
    }

    @Override
    public void close() {
        // No resources to close
    }

    @Override
    public StepReply<Box> step(Integer action) {
        system.step(action);

        double reward = 1.0;
        boolean done = system.isDone();

        return new StepReply<>(
            new Box(system.getState()),
            reward,
            done,
            null
        );
    }

    @Override
    public boolean isDone() {
        return system.isDone();
    }

    @Override
    public MDP<Box, Integer, DiscreteSpace> newInstance() {
        return new CartPoleMdp(imperfectionModel);
    }
}
