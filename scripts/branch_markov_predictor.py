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

class BranchMarkovPredictor:
    def setup(self, args):
        self.branch_count = 0
        sim.util.EveryBranch(self.handle_branch)

    def handle_branch(self, ip, predicted, actual, indirect):
        self.branch_count += 1
        if self.branch_count % 1000 == 0:  # Print periodically
            print(f"[BRANCH_MARKOV] Branch #{self.branch_count}")
            print(f"  IP: {hex(ip)}")
            print(f"  Predicted: {predicted}, Actual: {actual}")
            print(f"  {'Correct!' if predicted == actual else 'Incorrect'}")

sim.util.register(BranchMarkovPredictor())