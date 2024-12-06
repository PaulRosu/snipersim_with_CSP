"""
Core State At Branch Event Data Exporter

A tool to collect and analyze core state patterns following branch events.
Exports detailed state transition data and statistical summaries to help understand
core behavior patterns and their relationship to branch instructions.



Core state values:
- RUNNING        0
- INITIALIZING   1
- STALLED        2
- SLEEPING       3
- WAKING_UP      4
- IDLE           5
- BROKEN         6
- NUM_STATES     7

Example run command:
paulrosu@WorkstationP1:~/experiments$ ~/snipersim_SCSP/run-sniper -v -n 4 -c gainestown --roi -s analysis_data_export --power -- ~/snipersim_SCSP/test/fft/fft -p 4

It will generate the following files, besides the usual ones:
- core_state_patterns.csv
- state_pattern_summary.csv

Next steps:
- Add a script to plot the data
- Investigate what observation window and sampling period are optimal
- Implement state corellation between cores

"""

import sim
import os
import csv

class CoreStateAtBranchEventAnalyzer:
    def __init__(self):
        self.results_folder = None
        self.state_patterns_file = None
        self.analysis_summary_file = None
        self.event_counter = 0
        self.active_core_records = {}

    def setup(self, args):
        # Configuration parameters
        # TODO: Make these configurable via command line
        # the values below were empirically determined by trial and error; feel free to experiment with them
        self.observation_window = 10  # Duration to track states after each branch (microseconds)
        self.sampling_period = 1      # State sampling interval (microseconds)
        self.active_records = {}      # Currently tracked branch events
        self.event_counter = 0        # Event counter
        self.active_core_records = {} # Currently tracked core records
        
        print(f"[CORE_ANALYZER] Initialized with observation_window={self.observation_window}us, sampling_period={self.sampling_period}us")
        
        # Setup output files
        self.results_folder = sim.config.output_dir
        self.state_patterns_file = os.path.join(self.results_folder, "core_state_patterns.csv")
        self.analysis_summary_file = os.path.join(self.results_folder, "state_pattern_summary.csv")
        
        # Clear existing files
        for filepath in [self.state_patterns_file, self.analysis_summary_file]:
            if os.path.exists(filepath):
                os.remove(filepath)
        
        # Initialize state patterns file with headers
        with open(self.state_patterns_file, 'w') as f:
            f.write("Event_ID,Start_Time,Core_ID,Branch_IP,Branch_Taken,States\n")
        
        # Register callbacks
        sim.util.EveryBranch(self.record_branch_event)
        sim.util.Every(self.sampling_period * sim.util.Time.US, self.collect_state_sample)

    def record_branch_event(self, ip, predicted, actual, indirect, core_id):
        """Record a new branch event and initialize state tracking."""
        current_time = sim.stats.time()
        self.event_counter += 1
        
        # Initialize tracking for new core if needed
        if core_id not in self.active_core_records:
            self.active_core_records[core_id] = []
        
        # Create record for this branch event
        record_key = (self.event_counter, ip)
        event_record = {
            'start_time': current_time,
            'states': [],
            'branch_taken': actual,
            'core_id': core_id,
            'event_id': self.event_counter,
            'ip': ip
        }
        
        # Store record in both tracking dictionaries
        self.active_records[record_key] = event_record
        self.active_core_records[core_id].append(record_key)

    def collect_state_sample(self, time, time_delta):
        """Collect state samples for all active recording windows."""
        if time_delta == 0:
            return
        
        # Process each core's records
        for core_id, record_keys in list(self.active_core_records.items()):
            if not record_keys:
                continue
            
            current_state = sim.dvfs.get_core_state(core_id)
            
            # Update all active records for this core
            for record_key in record_keys[:]:
                record = self.active_records[record_key]
                elapsed_time = time - record['start_time']
                
                # Add new state sample
                record['states'].append((elapsed_time, current_state))
                
                # Check if observation window is complete
                if elapsed_time > (self.observation_window * sim.util.Time.US):
                    self.export_state_sequence(record_key, record)
                    # Clean up completed record
                    del self.active_records[record_key]
                    record_keys.remove(record_key)

    def export_state_sequence(self, record_key, record):
        """Export a completed state sequence to the CSV file."""
        event_id, ip = record_key
        
        with open(self.state_patterns_file, 'a') as f:
            # Format state sequence as comma-separated string
            state_sequence = ','.join(str(s) for _, s in record['states'])
            
            # Write record to CSV
            f.write(f"{record['event_id']},{record['start_time']},{record['core_id']},"
                   f"{hex(ip)},{record['branch_taken']},{state_sequence}\n")

    def generate_analysis_summary(self):
        """Generate statistical summary of collected state patterns."""
        print("[CORE_ANALYZER] Generating analysis summary...")
        pattern_stats = {}
        total_records = 0
        
        try:
            # Process state pattern records
            with open(self.state_patterns_file, 'r') as f:
                next(f)  # Skip header
                
                for line in f:
                    try:
                        parts = line.strip().split(',')
                        
                        # Parse record fields
                        event_id = int(parts[0])
                        start_time = int(parts[1])
                        core_id = int(parts[2])
                        ip = parts[3]
                        branch_taken = parts[4].lower() == 'true'
                        states = [int(s) for s in parts[5:]]
                        
                        total_records += 1
                        
                        # Analyze IDLE states (state 5)
                        idle_positions = [i for i, state in enumerate(states) if state == 5]
                        
                        if idle_positions:
                            if ip not in pattern_stats:
                                pattern_stats[ip] = {
                                    'count': 1,
                                    'idle_positions': idle_positions,
                                    'branch_taken_count': 1 if branch_taken else 0
                                }
                            else:
                                pattern_stats[ip]['count'] += 1
                                pattern_stats[ip]['idle_positions'].extend(idle_positions)
                                if branch_taken:
                                    pattern_stats[ip]['branch_taken_count'] += 1
                                    
                    except Exception as e:
                        print(f"[CORE_ANALYZER] Error processing record: {line.strip()}")
                        print(f"[CORE_ANALYZER] Error details: {str(e)}")
            
            # Write statistical summary
            with open(self.analysis_summary_file, 'w') as f:
                f.write("Branch_IP,Count,Avg_Idle_Position,Idle_Time_Percent,Branch_Taken_Ratio\n")
                
                for ip, stats in pattern_stats.items():
                    count = stats['count']
                    idle_positions = stats['idle_positions']
                    
                    # Calculate metrics
                    avg_position = sum(idle_positions) / len(idle_positions)
                    idle_percentage = len(idle_positions) / (count * self.observation_window) * 100
                    branch_taken_ratio = stats['branch_taken_count'] / count
                    
                    f.write(f"{ip},{count},{avg_position:.2f},{idle_percentage:.2f},{branch_taken_ratio:.2f}\n")
            
        except Exception as e:
            print(f"[CORE_ANALYZER] Error generating summary: {str(e)}")
        
        finally:
            print(f"[CORE_ANALYZER] Analyzed {total_records} total records")
            print(f"[CORE_ANALYZER] Found {len(pattern_stats)} branches with IDLE states")

    def hook_sim_end(self):
        """Generate final analysis when simulation ends."""
        self.generate_analysis_summary()


sim.util.register(CoreStateAtBranchEventAnalyzer())
