from rl.agents.dqn import DQNAgent as BaseDQNAgent
import numpy as np

class DQNAgent(BaseDQNAgent):
    def forward(self, observation):
        # Select an action.
        state = self.memory.get_recent_state(observation)
        q_values = self.compute_q_values(state)
        if self.training:
            action = self.policy.select_action(q_values=q_values)
        else:
            action = self.test_policy.select_action(q_values=q_values)

        # Book-keeping.
        self.recent_observation = observation
        self.recent_action = action

        return action

    def remember(self, state, action, reward, terminal):
        # Store most recent experience in memory.
        self.memory.append(state, action, reward, terminal, training=True)

    def optimize(self):

        experiences = self.memory.sample(self.batch_size)
        print(experiences)
        if len(experiences) < self.nb_steps_warmup:
            return

        # Start by extracting the necessary parameters (we use a vectorized implementation).
        state0_batch = []
        reward_batch = []
        action_batch = []
        terminal1_batch = []
        state1_batch = []
        for e in experiences:
            state0_batch.append(e.state0)
            state1_batch.append(e.state1)
            reward_batch.append(e.reward)
            action_batch.append(e.action)
            terminal1_batch.append(0. if e.terminal1 else 1.)

        # Prepare and validate parameters.
        state0_batch = self.process_state_batch(state0_batch)
        state1_batch = self.process_state_batch(state1_batch)
        terminal1_batch = np.array(terminal1_batch)
        reward_batch = np.array(reward_batch)
        assert reward_batch.shape == (self.batch_size,)
        assert terminal1_batch.shape == reward_batch.shape
        assert len(action_batch) == len(reward_batch)

        # Compute Q values for mini-batch update.
        if self.enable_double_dqn:
            # According to the paper "Deep Reinforcement Learning with Double Q-learning"
            # (van Hasselt et al., 2015), in Double DQN, the online network predicts the actions
            # while the target network is used to estimate the Q value.
            q_values = self.model.predict_on_batch(state1_batch)
            assert q_values.shape == (self.batch_size, self.nb_actions)
            actions = np.argmax(q_values, axis=1)
            assert actions.shape == (self.batch_size,)

            # Now, estimate Q values using the target network but select the values with the
            # highest Q value wrt to the online model (as computed above).
            target_q_values = self.target_model.predict_on_batch(state1_batch)
            assert target_q_values.shape == (self.batch_size, self.nb_actions)
            q_batch = target_q_values[range(self.batch_size), actions]
        else:
            # Compute the q_values given state1, and extract the maximum for each sample in the batch.
            # We perform this prediction on the target_model instead of the model for reasons
            # outlined in Mnih (2015). In short: it makes the algorithm more stable.
            target_q_values = self.target_model.predict_on_batch(state1_batch)
            assert target_q_values.shape == (self.batch_size, self.nb_actions)
            q_batch = np.max(target_q_values, axis=1).flatten()
        assert q_batch.shape == (self.batch_size,)

        targets = np.zeros((self.batch_size, self.nb_actions))
        dummy_targets = np.zeros((self.batch_size,))
        masks = np.zeros((self.batch_size, self.nb_actions))

        # Compute r_t + gamma * max_a Q(s_t+1, a) and update the target targets accordingly,
        # but only for the affected output units (as given by action_batch).
        discounted_reward_batch = self.gamma * q_batch
        # Set discounted reward to zero for all states that were terminal.
        discounted_reward_batch *= terminal1_batch
        assert discounted_reward_batch.shape == reward_batch.shape
        Rs = reward_batch + discounted_reward_batch
        for idx, (target, mask, R, action) in enumerate(zip(targets, masks, Rs, action_batch)):
            target[action] = R  # update action with estimated accumulated reward
            dummy_targets[idx] = R
            mask[action] = 1.  # enable loss for this specific action
        targets = np.array(targets).astype('float32')
        masks = np.array(masks).astype('float32')

        # Finally, perform a single update on the entire batch. We use a dummy target since
        # the actual loss is computed in a Lambda layer that needs more complex input. However,
        # it is still useful to know the actual target to compute metrics properly.
        ins = [state0_batch] if type(self.model.input) is not list else state0_batch
        metrics = self.trainable_model.train_on_batch(ins + [targets, masks], [dummy_targets, targets])
        metrics = [metric for idx, metric in enumerate(metrics) if idx not in (1, 2)]  # throw away individual losses
        metrics += self.policy.metrics
        if self.processor is not None:
            metrics += self.processor.metrics

        if self.target_model_update >= 1 and self.step % self.target_model_update == 0:
            self.update_target_model_hard()

        return metrics