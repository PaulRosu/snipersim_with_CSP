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
paulrosu@WorkstationP1:~/experiments$ ~/snipersim/run-sniper -v -n 4 -c gainestown --roi -s analysis_data_export --power -- ~/snipersim_SCSP/test/fft/fft -p 4
paulrosu@e1e47903c925:~/workspace/benchmarks$ ./run-sniper -d ../simresults -s analysis_data_export:100:10 --power -p splash2-fft -i test -n 4

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

class CoreStateAnalyzer:
    """Individual core analyzer - one instance per core"""
    def __init__(self, core_id, results_folder, observation_window, sampling_period):
        self.core_id = core_id
        self.results_folder = results_folder
        self.observation_window = observation_window
        self.sampling_period = sampling_period
        
        self.active_records = {}        # key=event_id, value=record dict
        self.completed_records = []     # store completed records in memory
        self.next_event_id = 1         # track the next available event ID for this core

    def record_branch_event(self, ip, predicted, actual, indirect):
        """Record a new branch event for this core."""
        event_id = self.next_event_id
        self.next_event_id += 1
        
        record = {
            'event_id': event_id,
            'core_id': self.core_id,
            'ip': ip,
            'branch_taken': actual,
            'start_time': sim.stats.time(),
            'instruction_count': sim.stats.get('performance_model', self.core_id, 'instruction_count'),
            'states': []
        }
        
        self.active_records[event_id] = record
        #print("[DEBUG] Core %d: New branch event %d at IP %s" % (self.core_id, event_id, hex(ip)))

    def collect_state_sample(self, time, time_delta):
        """Collect state samples for this core's active recording windows."""
        if time_delta == 0:
            return

        current_state = sim.dvfs.get_core_state(self.core_id)
        
        for event_id in list(self.active_records.keys()):
            record = self.active_records[event_id]
            elapsed_time = time - record['start_time']
            
            record['states'].append((elapsed_time, current_state))
            
            if elapsed_time > (self.observation_window * sim.util.Time.US):
                self.completed_records.append(record)
                del self.active_records[event_id]
                # print("[DEBUG] Core %d: Completed record %d with %d states" % 
                #       (self.core_id, event_id, len(record['states'])))

class CoreStateAtBranchEventAnalyzer:
    def __init__(self):
        self.results_folder = None
        self.state_patterns_file = None
        self.analysis_summary_file = None
        self.observation_window = None  # Will be set in setup()
        self.sampling_period = None     # Will be set in setup()
        self.total_branches = 0         # track total branches
        self.core_analyzers = {}        # key=core_id, value=CoreStateAnalyzer

    def setup(self, args):
        # Parse arguments similar to SCSP style
        args = dict(enumerate((args or '').split(':')))
        
        # Default observation window 100 microseconds
        self.observation_window = int(args.get(0, None) or 100)
        
        # Default sampling period 1 microsecond
        self.sampling_period = int(args.get(1, None) or 1)
        
        self.results_folder = sim.config.output_dir
        self.state_patterns_file = os.path.join(self.results_folder, "core_state_patterns.csv")
        self.analysis_summary_file = os.path.join(self.results_folder, "state_pattern_summary.csv")
        
        # Create analyzers for each core
        num_cores = sim.config.ncores
        for core_id in range(num_cores):
            self.core_analyzers[core_id] = CoreStateAnalyzer(
                core_id, 
                self.results_folder,
                self.observation_window,
                self.sampling_period
            )
        
        # Register branch prediction callback using EveryBranch
        def branch_callback(ip, predicted, actual, indirect, core_id):
            self.hook_branch_predictor(core_id, ip, predicted, actual, indirect)
        
        # Register periodic callback using Every
        self.periodic_hook = sim.util.Every(
            self.sampling_period * sim.util.Time.US,  # Convert to femtoseconds
            lambda time, time_delta: self.hook_periodic(time, time_delta)
        )
        
        self.branch_hook = sim.util.EveryBranch(branch_callback)
        
        print("[CORE_ANALYZER] Initialized %d core analyzers" % num_cores)
        print("[CORE_ANALYZER] Registered branch prediction and periodic callbacks")
        print("[CORE_ANALYZER] Observation window [us]: %d" % self.observation_window)
        print("[CORE_ANALYZER] Sampling period [us]: %d" % self.sampling_period)

    # These methods will be automatically called by Sniper's hook system
    def hook_periodic(self, time, time_delta=0):
        """Periodic hook for state sampling - delegates to each core analyzer."""
        for analyzer in self.core_analyzers.values():
            analyzer.collect_state_sample(time, time_delta)

    def hook_branch_predictor(self, core_id, ip, predicted, actual, indirect):
        """Hook for branch events - delegates to appropriate core analyzer."""
        #print("[DEBUG] Branch event detected on core %d at IP %s" % (core_id, hex(ip)))  # Add debug print
        self.total_branches += 1
        if core_id in self.core_analyzers:
            self.core_analyzers[core_id].record_branch_event(ip, predicted, actual, indirect)

    def hook_sim_end(self):
        """Simulation end hook - combines and writes results from all cores."""
        all_completed_records = []
        
        # Collect remaining active records and all completed records
        for analyzer in self.core_analyzers.values():
            print("[DEBUG] Core %d has %d completed records and %d active records" % 
                  (analyzer.core_id, len(analyzer.completed_records), len(analyzer.active_records)))
            all_completed_records.extend(analyzer.completed_records)
            for record in analyzer.active_records.values():
                all_completed_records.append(record)

        print("[DEBUG] Writing %d total records" % len(all_completed_records))

        # Write all records to file
        with open(self.state_patterns_file, 'w') as f:
            f.write("Event_ID,Instruction_Count,Start_Time,Core_ID,Branch_IP,Branch_Taken,States\n")
            for record in all_completed_records:
                states_str = ','.join(str(s) for _, s in record['states'])
                f.write("%d,%d,%d,%d,%s,%s,%s\n" % (
                    record['event_id'],
                    record['instruction_count'],
                    record['start_time'],
                    record['core_id'],
                    hex(record['ip']),
                    record['branch_taken'],
                    states_str))

        self.generate_analysis_summary(all_completed_records)
        print("[CORE_ANALYZER] Total branches encountered: %d" % self.total_branches)

    def generate_analysis_summary(self, all_records):
        """Generate statistical summary from all cores' records."""
        # Your existing generate_analysis_summary code, but using all_records parameter
        pattern_stats = {}
        total_records = len(all_records)

        for record in all_records:
            ip = hex(record['ip'])
            branch_taken = record['branch_taken']
            states = [s for _, s in record['states']]

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

        # Write pattern summary
        with open(self.analysis_summary_file, 'w') as f:
            f.write("Branch_IP,Count,Avg_Idle_Position,Idle_Time_Percent,Branch_Taken_Ratio\n")
            for ip, stats in pattern_stats.items():
                count = stats['count']
                idle_positions = stats['idle_positions']
                avg_position = sum(idle_positions) / float(len(idle_positions))  # Use float division for Python 2
                total_samples_per_record = self.observation_window
                idle_percentage = (float(len(idle_positions)) / (count * total_samples_per_record)) * 100
                branch_taken_ratio = float(stats['branch_taken_count']) / count
                f.write("%s,%d,%.2f,%.2f,%.2f\n" % (
                    ip, count, avg_position, idle_percentage, branch_taken_ratio))

        print("[CORE_ANALYZER] Analyzed %d total records" % total_records)
        print("[CORE_ANALYZER] Found %d branches with IDLE states" % len(pattern_stats))

# Register the analyzer
analyzer = CoreStateAtBranchEventAnalyzer()
sim.util.register(analyzer)
