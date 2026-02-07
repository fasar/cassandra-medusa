package com.cartpole.ai;

import com.cartpole.physics.ImperfectionModel;
import org.deeplearning4j.rl4j.learning.sync.qlearning.QLearning;
import org.deeplearning4j.rl4j.learning.sync.qlearning.discrete.QLearningDiscreteDense;
import org.deeplearning4j.rl4j.network.configuration.DQNDenseNetworkConfiguration;
import org.deeplearning4j.rl4j.network.dqn.DQNFactoryStdDense;
import org.deeplearning4j.rl4j.space.Box;
import org.deeplearning4j.rl4j.util.DataManager;
import org.nd4j.linalg.learning.config.Adam;

import java.io.IOException;

public class DqnTrainer {

    public static void train(ImperfectionModel imperfectionModel, String outputModelPath) throws IOException {
        CartPoleMdp mdp = new CartPoleMdp(imperfectionModel);

        QLearning.QLConfiguration config = new QLearning.QLConfiguration(
                123,    // seed
                200,    // maxEpochStep
                20000,  // maxStep (20k steps)
                50000,  // expRepMaxSize
                32,     // batchSize
                500,    // targetDqnUpdateFreq
                100,    // updateStart
                0.01,   // rewardFactor
                0.99,   // gamma
                1.0,    // errorClamp
                0.1f,   // minEpsilon
                1000,   // epsilonNbStep
                true    // doubleDQN
        );

        DQNDenseNetworkConfiguration netConfig = DQNDenseNetworkConfiguration.builder()
                .l2(0.001)
                .updater(new Adam(0.001))
                .numHiddenNodes(16)
                .numLayers(2)
                .build();

        DataManager manager = new DataManager(true);

        QLearningDiscreteDense<Box> dql = new QLearningDiscreteDense<>(
                mdp,
                new DQNFactoryStdDense(netConfig),
                config,
                manager
        );

        dql.train();

        dql.getPolicy().save(outputModelPath);

        mdp.close();
    }
}
