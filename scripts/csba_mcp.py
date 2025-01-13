import sim
import os
import csv


CORE_STATE_LABELS = {
    0: 'RUNNING',
    1: 'INITIALIZING',
    2: 'STALLED',
    3: 'SLEEPING',
    4: 'WAKING_UP',
    5: 'IDLE',
    6: 'BROKEN'
}

def is_idle(state_id):
    return (state_id == 5)

class MarkovChainPredictor:
    """
    A simple Markov chain predictor for idle vs. active states based on
    consecutive branch states. Each transition is keyed by:
      ( (ip_prev, taken_prev), (ip_curr, taken_curr) )
    and stores how many times these transitions were observed and
    how many led to an idle state in between.
    """

    def __init__(self):
        # Transition stats for Markov chain:
        #   key: ((ip_prev, taken_prev), (ip_curr, taken_curr))
        #   val: {'count': int, 'idle_count': int}
        self.transition_stats = {}

        # Last branch state per core for chaining:
        #   core_id -> (ip, taken)
        self.last_branch_state = {}

        # Log of predictions (for offline analysis):
        #   Each item: {
        #       'event_id': int,
        #       'core_id': int,
        #       'predicted_idle': bool or None,
        #       'actual_idle': bool
        #   }
        self.prediction_log = []

        # Statistics counters:
        # - how many branches taken / not-taken
        self.branches_taken = 0
        self.branches_not_taken = 0

        # - how many predictions were made, how many correct, how many predicted idle/active
        self.num_predictions_made = 0
        self.num_predictions_correct = 0
        self.num_predictions_idle = 0
        self.num_predictions_active = 0

        self.previous_predictions = {}  # Store predictions per core

    def update_chain(self, core_id, branch_state, encountered_states):
        """
        Update the Markov chain with a new branch transition for the specified core.

        :param core_id: The core on which the branch occurred
        :param branch_state: Tuple (ip, taken) for this new branch
        :param encountered_states: List of state IDs observed since the last branch
        """
        # Count how many times branches are taken vs. not taken
        _, taken_flag = branch_state
        if taken_flag:
            self.branches_taken += 1
        else:
            self.branches_not_taken += 1

        # If no last branch state, record this one and return
        if core_id not in self.last_branch_state:
            self.last_branch_state[core_id] = branch_state
            return

        prev_branch_state = self.last_branch_state[core_id]
        self.last_branch_state[core_id] = branch_state

        # Build Markov chain key and record the idle or not-idle
        key = (prev_branch_state, branch_state)
        idle_observed = any(is_idle(s) for s in encountered_states)

        if key not in self.transition_stats:
            self.transition_stats[key] = {
                'count': 0,
                'idle_count': 0
            }

        self.transition_stats[key]['count'] += 1
        if idle_observed:
            self.transition_stats[key]['idle_count'] += 1

    def predict_idle(self, core_id):
        """
        Predict whether the core is likely to go idle next time.
        Summarize all transitions from the last known branch state for this core.

        :param core_id: The core for which we want to predict
        :return: True if predicted idle, False if active, or None if no data
        """
        if core_id not in self.last_branch_state:
            return None

        ip_taken_prev = self.last_branch_state[core_id]
        total_count = 0
        total_idle = 0

        # Sum up transitions: (ip_taken_prev) -> ...
        for (k_prev, k_curr), stats in self.transition_stats.items():
            if k_prev == ip_taken_prev:
                total_count += stats['count']
                total_idle += stats['idle_count']

        if total_count == 0:
            return None

        idle_prob = float(total_idle) / float(total_count)
        predicted_idle = (idle_prob > 0.5)
        self.previous_predictions[core_id] = predicted_idle
        return predicted_idle

    def log_prediction(self, event_id, core_id, predicted_idle, actual_idle):
        """
        Record a real-time prediction outcome.
        Increment internal counters for final statistics.

        :param event_id: Unique event ID
        :param core_id: Which core was involved
        :param predicted_idle: The Markov chain's prediction (True, False, or None)
        :param actual_idle: The actual observed idle status
        """
        # Update counters only if a real prediction was made (not None)
        if predicted_idle is not None:
            self.num_predictions_made += 1
            if predicted_idle == actual_idle:
                self.num_predictions_correct += 1
            if predicted_idle is True:
                self.num_predictions_idle += 1
            else:
                self.num_predictions_active += 1

        self.prediction_log.append({
            'event_id': event_id,
            'core_id': core_id,
            'predicted_idle': predicted_idle,
            'actual_idle': actual_idle
        })

    def dump_prediction_log(self, filepath):
        """
        Write the real-time prediction log to CSV.

        :param filepath: Where to write the CSV file
        """
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=[
                'event_id', 'core_id', 'predicted_idle', 'actual_idle'
            ])
            writer.writeheader()
            for row in self.prediction_log:
                writer.writerow(row)

    def dump_transition_stats(self, filepath):
        """
        Write the Markov chain transition table to CSV.

        :param filepath: Where to write the CSV file
        """
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'Prev_IP', 'Prev_Taken', 'Next_IP', 'Next_Taken',
                'Count', 'Idle_Count', 'Idle_Ratio'
            ])
            for (prev_state, curr_state), stats in self.transition_stats.items():
                (prev_ip, prev_taken) = prev_state
                (curr_ip, curr_taken) = curr_state
                count = stats['count']
                idle_count = stats['idle_count']
                ratio = float(idle_count) / count if count > 0 else 0.0
                writer.writerow([
                    hex(prev_ip), prev_taken,
                    hex(curr_ip), curr_taken,
                    count, idle_count,
                    f"{ratio:.4f}"
                ])

    def dump_statistics(self, filepath):
        """
        Write overall statistics (branch counts, prediction accuracy, etc.) to CSV.

        :param filepath: Where to write the CSV file
        """
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Statistic', 'Value'])

            # Branch stats
            writer.writerow(['Branches_Taken', self.branches_taken])
            writer.writerow(['Branches_Not_Taken', self.branches_not_taken])

            # Prediction stats
            writer.writerow(['Predictions_Made', self.num_predictions_made])
            writer.writerow(['Predictions_Correct', self.num_predictions_correct])

            accuracy = 0.0
            if self.num_predictions_made > 0:
                accuracy = (float(self.num_predictions_correct)
                            / float(self.num_predictions_made)) * 100.0
            writer.writerow(['Accuracy_Percent', f"{accuracy:.2f}"])

            writer.writerow(['Predictions_Idle', self.num_predictions_idle])
            writer.writerow(['Predictions_Active', self.num_predictions_active])


class CoreStateAndBranchMonitor:
    """
    A module that:
      - Periodically samples core states and logs them with unique event IDs.
      - Captures branch events, logs them, and updates a Markov chain that
        relates consecutive branches to whether the core goes idle in between.
      - Uses real-time predictions from the Markov chain and logs
        how often they are correct.
      - Does not rely on time as the unique key; we maintain global_event_id.
    """

    def __init__(self):
        self.sampling_period_us = None
        self.results_folder = None

        # A global counter for all events, both periodic and branch
        self.global_event_id = 1
        
        # Logs for states and branches
        self.core_state_log = []  # (event_id, core_id, state_id, time_fs)
        self.branch_log = []      # (event_id, core_id, ip, actual_taken, predicted_taken, indirect, time_fs)

        # Our Markov chain predictor instance
        self.markov_predictor = MarkovChainPredictor()

    def setup(self, args):
        """
        Setup the sampling period (default 1 us). Example usage:
          run-sniper -s core_state_branch_monitor:10
        => sampling_period_us = 10
        """
        args = dict(enumerate((args or '').split(':')))
        self.sampling_period_us = int(args.get(0, None) or 1)
        self.results_folder = sim.config.output_dir

        # Register periodic sampling
        self.periodic_hook = sim.util.Every(
            self.sampling_period_us * sim.util.Time.US,
            self.hook_periodic
        )

        # Register branch hook
        def branch_callback(ip, predicted, actual, indirect, core_id):
            self.hook_branch_predictor(core_id, ip, predicted, actual, indirect)
        self.branch_hook = sim.util.EveryBranch(branch_callback)

        print("[CORE_STATE_BRANCH_MONITOR] Setup complete.")
        print(f"  Sampling period [us]: {self.sampling_period_us}")

    def hook_periodic(self, time_fs, time_delta_fs = 0):
        """
        Called every sampling_period_us of simulated time (if time_delta_fs != 0).
        This logs the core states and also records a Markov chain prediction for each core.
        """
        if time_delta_fs == 0:
            return

        num_cores = sim.config.ncores
        for core_id in range(num_cores):
            event_id = self.global_event_id
            self.global_event_id += 1

            state_id = sim.dvfs.get_core_state(core_id)
            
            # Log the current core state
            self.core_state_log.append((event_id, core_id, state_id, time_fs))

            # Compare current state with previous prediction
            previous_prediction = self.markov_predictor.previous_predictions.get(core_id)
            if previous_prediction is not None:
                self.markov_predictor.log_prediction(
                    event_id=event_id,
                    core_id=core_id,
                    predicted_idle=previous_prediction,
                    actual_idle=is_idle(state_id)
                )
            
            # Make and store new prediction for next period
            self.markov_predictor.predict_idle(core_id) 

    def hook_branch_predictor(self, core_id, ip, predicted, actual, indirect):
        """
        Called on every branch event. We increment global_event_id for each branch,
        log the event, and update the Markov chain with the states encountered
        since the last branch on this core.
        """
        event_id = self.global_event_id
        self.global_event_id += 1

        time_fs = sim.stats.time()
        self.branch_log.append(
            (event_id, core_id, ip, actual, predicted, indirect, time_fs)
        )

        # Find the last branch event_id for this core
        last_branch_id = None
        for i in range(len(self.branch_log) - 2, -1, -1):
            b_event_id, b_core, _, _, _, _, _ = self.branch_log[i]
            if b_core == core_id:
                last_branch_id = b_event_id
                break

        # Collect any core state IDs observed between that last branch and now
        encountered_states = []
        if last_branch_id is not None:
            for (cid_event_id, cid_core, cid_state, _) in self.core_state_log:
                if cid_core == core_id and last_branch_id < cid_event_id < event_id:
                    encountered_states.append(cid_state)

        # Update Markov chain transitions
        branch_state = (ip, actual)
        self.markov_predictor.update_chain(core_id, branch_state, encountered_states)

    def hook_sim_end(self):
        """
        At simulation end, write out:
          1) Periodic core states
          2) Branch events
          3) Markov chain transitions
          4) Markov chain prediction log
          5) Overall statistics (branches, predictions, accuracy, etc.)
        """
        # 1) Core state samples
        core_states_file = os.path.join(self.results_folder, "periodic_core_states.csv")
        with open(core_states_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["event_id", "core_id", "state_id", "time_fs"])
            for (event_id, core_id, state_id, time_fs) in self.core_state_log:
                writer.writerow([event_id, core_id, state_id, time_fs])

        # 2) Branch events
        branch_file = os.path.join(self.results_folder, "branch_events.csv")
        with open(branch_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "event_id", "core_id", "ip",
                "actual_taken", "predicted_taken", "indirect", "time_fs"
            ])
            for (event_id, core_id, ip, actual, predicted, indirect, time_fs) in self.branch_log:
                writer.writerow([
                    event_id, core_id, hex(ip), actual, predicted, indirect, time_fs
                ])

        # 3) Markov chain transition stats
        transitions_file = os.path.join(self.results_folder, "markov_chain_transitions.csv")
        self.markov_predictor.dump_transition_stats(transitions_file)

        # 4) Markov chain predictions
        predictions_file = os.path.join(self.results_folder, "markov_chain_predictions.csv")
        self.markov_predictor.dump_prediction_log(predictions_file)

        # 5) Final statistics (branches taken/not, prediction accuracy, etc.)
        stats_file = os.path.join(self.results_folder, "statistics.csv")
        self.markov_predictor.dump_statistics(stats_file)

        print("[CORE_STATE_BRANCH_MONITOR] Simulation ended. Data saved:")
        print(f"  - {core_states_file}")
        print(f"  - {branch_file}")
        print(f"  - {transitions_file}")
        print(f"  - {predictions_file}")
        print(f"  - {stats_file}")


# Register the analyzer
analyzer = CoreStateAndBranchMonitor()
sim.util.register(analyzer)
