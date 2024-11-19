"""
dvfs_acaps_predictor.py

Implementes the Simple Core State Predictor
"""

class Csp:
    def __init__(self, history_size=5, conf_th=3):
        self.history_size = history_size
        self.history = [None] * history_size
        self.conf = 0
        self.conf_th = conf_th
        self.stats = {"Correct": 0.0, "Incorrect": 0.0}

    def update_history(self, value):
        self.history.pop(0)
        self.history.append(value)

    def update_stats(self, prediction, actual):
        # Update the stats only if the last prediction had enough confidence
        if (self.is_predictable() == False):
            return
        
        if prediction == actual:
            self.stats["Correct"] += 1
        else:
            self.stats["Incorrect"] += 1
        
        # print(f"C: Actual: {actual}, Predicted: {prediction}")

    def update_conf(self, prediction, actual):
        if prediction == actual:
            if (self.conf < self.conf_th):
                self.conf += 1
        else:
            self.conf = 0

    def predict_next_value(self):
        last_value = self.history[-1]
        return last_value
    
    def update(self, actual_value):
        # Get the old value
        predicted_value = self.predict_next_value()

        # Perform the confidence adjustements
        self.update_conf(predicted_value, actual_value)

        # Add actual value to the history
        self.update_history(actual_value)

    def is_predictable(self):
        if self.conf >= self.conf_th:
            return True
        return False

    def get_stats(self):
        return self.stats
