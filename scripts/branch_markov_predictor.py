"""
Branch Core State Markov Chain Predictor

A simple implementation to monitor branch prediction behavior.
Will be extended to a three-order Markov chain predictor that will 
attempt to predict the next core state using historical information from the branch predictor
and core states.

Core state values:
- RUNNING        0
- INITIALIZING   1
- STALLED        2
- SLEEPING       3
- WAKING_UP      4
- IDLE           5
- BROKEN         6
- NUM_STATES     7
"""

import sim
import os
import csv

class BranchMarkovPredictor:
    def setup(self, args):
        self.branch_count = 0
        sim.util.EveryBranch(self.handle_branch)

    def handle_branch(self, ip, predicted, actual, indirect, core_id):
        self.branch_count += 1
        if self.branch_count % 1000 == 0:  # Print periodically
            print("[BRANCH_MARKOV] Branch #{0}".format(self.branch_count))
            print("  IP: {0}".format(hex(ip)))
            print("  Predicted: {0}, Actual: {1}".format(predicted, actual))
            print("  Core: {0}".format(core_id))
            print("  {0}".format('Correct!' if predicted == actual else 'Incorrect'))

sim.util.register(BranchMarkovPredictor())