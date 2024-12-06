"""
Branch Markov Predictor

A simple implementation to monitor branch prediction behavior.
Will be extended to a three-order Markov chain predictor.
"""

import sim

class BranchMarkovPredictor:
    def setup(self, args):
        self.branch_count = 0
        sim.util.EveryBranch(self.handle_branch)

    def handle_branch(self, ip, predicted, actual, indirect, core_id):
        core_state = sim.dvfs.get_core_state(core_id)
        
        self.branch_count += 1
        #if self.branch_count % 1000 == 0:
        if core_id != 0:
            print(f"[BRANCH_MARKOV] Branch #{self.branch_count}")
            print(f"  Core: {core_id}, State: {core_state}")
            print(f"  IP: {hex(ip)}")
            print(f"  Predicted: {predicted}, Actual: {actual}")
            print(f"  {'Correct!' if predicted == actual else 'Incorrect'}")

sim.util.register(BranchMarkovPredictor())