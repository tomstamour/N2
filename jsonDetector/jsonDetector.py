#!/usr/bin/env python3
"""
jsonDetector.py - Real-time JSON file monitoring and script execution

Continuously monitors a directory for new JSON files and executes a specified
script when a JSON file is detected. Tracks processed files to prevent duplicate
executions (configurable via --multiple-trigger flag).
"""

import argparse
import logging
import json
import subprocess
import signal
import sys
import fcntl
import os
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# Global variables for graceful shutdown
shutdown_event = False
logger = None
current_process = None


class StateManager:
    """Manage tracking of processed JSON files."""

    def __init__(self, state_file):
        """
        Initialize state manager.

        Args:
            state_file: Path to JSON file for storing processed filenames
        """
        self.state_file = Path(state_file)
        self.processed_files = set()
        self._load_state()

    def _load_state(self):
        """Load processed files from state file. Start fresh if corrupted."""
        if not self.state_file.exists():
            logger.debug(f"State file does not exist: {self.state_file}")
            return

        try:
            with open(self.state_file, 'r') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    if isinstance(data, dict) and 'processed_files' in data:
                        self.processed_files = set(data['processed_files'])
                        logger.info(f"Loaded {len(self.processed_files)} processed files from state")
                    else:
                        logger.warning("State file has unexpected format, starting fresh")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading state file: {e}, starting fresh")
            self.processed_files = set()

    def is_processed(self, filename):
        """
        Check if filename has already been processed.

        Args:
            filename: Basename of the file to check

        Returns:
            bool: True if already processed, False otherwise
        """
        return filename in self.processed_files

    def mark_processed(self, filename):
        """
        Mark a filename as processed and save state.

        Args:
            filename: Basename of the file to mark
        """
        self.processed_files.add(filename)
        self._save_state()

    def _save_state(self):
        """Save processed files to state file with atomic write."""
        try:
            state_data = {
                'processed_files': sorted(list(self.processed_files)),
                'last_updated': datetime.now().isoformat(),
                'total_processed': len(self.processed_files)
            }

            # Write to temp file first
            temp_path = f"{self.state_file}.tmp"
            with open(temp_path, 'w') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(state_data, f, indent=2)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Atomic rename
            os.replace(temp_path, self.state_file)
            logger.debug(f"State saved: {len(self.processed_files)} files")

        except Exception as e:
            logger.error(f"Error saving state file: {e}")


class ScriptExecutor:
    """Execute target scripts with JSON file paths as arguments."""

    def __init__(self, script_path, timeout=300):
        """
        Initialize script executor.

        Args:
            script_path: Path to the script to execute
            timeout: Timeout in seconds for script execution
        """
        self.script_path = Path(script_path)
        self.timeout = timeout

    def execute(self, json_file_path):
        """
        Execute the target script with JSON file as argument.

        Args:
            json_file_path: Absolute path to JSON file to process

        Returns:
            tuple: (success: bool, stdout: str, stderr: str)
        """
        try:
            # Verify file still exists before execution
            if not Path(json_file_path).exists():
                logger.warning(f"JSON file disappeared before execution: {json_file_path}")
                return False, "", "File not found at execution time"

            logger.debug(f"Executing script with argument: {json_file_path}")

            # Run script with JSON file path as argument
            result = subprocess.run(
                [str(self.script_path), json_file_path],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )

            # Log output
            if result.stdout:
                logger.debug(f"Script stdout: {result.stdout[:500]}")
            if result.stderr:
                logger.debug(f"Script stderr: {result.stderr[:500]}")

            if result.returncode != 0:
                logger.warning(
                    f"Script exited with code {result.returncode}: {json_file_path}"
                )
                return False, result.stdout, result.stderr

            logger.debug(f"Script execution successful for {json_file_path}")
            return True, result.stdout, result.stderr

        except subprocess.TimeoutExpired:
            logger.error(f"Script execution timeout ({self.timeout}s): {json_file_path}")
            return False, "", f"Timeout after {self.timeout} seconds"
        except FileNotFoundError:
            logger.warning(f"JSON file not found: {json_file_path}")
            return False, "", "File not found"
        except Exception as e:
            logger.error(f"Error executing script: {e}")
            return False, "", str(e)


class JSONFileEventHandler(FileSystemEventHandler):
    """Handle file system events for JSON file detection."""

    def __init__(self, callback):
        """
        Initialize event handler.

        Args:
            callback: Function to call when JSON file is detected
        """
        self.callback = callback

    def on_created(self, event):
        """Handle file creation events."""
        if event.is_directory:
            return

        # Check if it's a JSON file
        if event.src_path.endswith('.json'):
            logger.debug(f"JSON file detected: {event.src_path}")
            self.callback(event.src_path)


class JSONDetector:
    """Main orchestrator for JSON file detection and processing."""

    def __init__(self, watch_directory, script_to_launch, multiple_trigger=False,
                 state_file=None, log_dir=None, timeout=300):
        """
        Initialize JSON detector.

        Args:
            watch_directory: Directory to monitor for JSON files
            script_to_launch: Script to execute when JSON is detected
            multiple_trigger: Allow multiple triggers for same filename
            state_file: Path to state file (default: processed_files.json)
            log_dir: Directory for logs (default: ./logs)
            timeout: Script execution timeout in seconds
        """
        self.watch_directory = Path(watch_directory)
        self.script_path = Path(script_to_launch)
        self.multiple_trigger = multiple_trigger
        self.state_file = Path(state_file) if state_file else Path('processed_files.json')
        self.log_dir = Path(log_dir) if log_dir else Path('logs')
        self.timeout = timeout

        self.state_manager = None
        self.script_executor = None
        self.observer = None

    def validate_configuration(self):
        """Validate configuration before starting. Fail fast on errors."""
        errors = []

        # Validate watch directory
        if not self.watch_directory.exists():
            errors.append(f"Watch directory does not exist: {self.watch_directory}")
        elif not self.watch_directory.is_dir():
            errors.append(f"Watch path is not a directory: {self.watch_directory}")
        elif not os.access(self.watch_directory, os.R_OK):
            errors.append(f"Watch directory is not readable: {self.watch_directory}")

        # Validate script
        if not self.script_path.exists():
            errors.append(f"Script does not exist: {self.script_path}")
        elif not self.script_path.is_file():
            errors.append(f"Script path is not a file: {self.script_path}")
        elif not os.access(self.script_path, os.X_OK):
            errors.append(f"Script is not executable: {self.script_path}")

        # Validate state file directory
        state_dir = self.state_file.parent
        if state_dir != Path('.'):
            if not state_dir.exists():
                try:
                    state_dir.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"Created state directory: {state_dir}")
                except Exception as e:
                    errors.append(f"Cannot create state directory: {e}")
            elif not os.access(state_dir, os.W_OK):
                errors.append(f"State directory is not writable: {state_dir}")
        elif not os.access(state_dir, os.W_OK):
            errors.append(f"State directory is not writable: {state_dir}")

        # Validate log directory
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create log directory: {e}")

        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            sys.exit(1)

        logger.info("Configuration validation successful")

    def setup_logging(self):
        """Configure logging with console and file handlers."""
        global logger
        logger = logging.getLogger("jsonDetector")
        logger.setLevel(logging.DEBUG)

        # Create log directory if needed
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # File handler (DEBUG level)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"jsonDetector_{today}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)

        # Console handler (INFO level)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Format
        formatter = logging.Formatter(
            '%(asctime)s %(levelname)s %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Add handlers
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        logger.info("jsonDetector logging initialized")

    def setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            global shutdown_event
            logger.info(f"Received signal {signum}, initiating graceful shutdown")
            shutdown_event = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def on_json_detected(self, json_file_path):
        """
        Handle detection of a new JSON file.

        Args:
            json_file_path: Absolute path to the detected JSON file
        """
        global shutdown_event

        if shutdown_event:
            logger.debug(f"Ignoring JSON file during shutdown: {json_file_path}")
            return

        filename = Path(json_file_path).name

        # Check if already processed (unless multiple_trigger is enabled)
        if not self.multiple_trigger and self.state_manager.is_processed(filename):
            logger.info(f"Skipping already-processed file: {filename}")
            return

        logger.info(f"Processing JSON file: {filename}")

        # Execute script
        success, stdout, stderr = self.script_executor.execute(json_file_path)

        if success:
            logger.info(f"Successfully processed: {filename}")
            self.state_manager.mark_processed(filename)
        else:
            logger.error(f"Failed to process {filename}: {stderr}")
            # Mark as processed to avoid infinite retries
            self.state_manager.mark_processed(filename)

    def initialize_components(self):
        """Initialize all detector components."""
        self.state_manager = StateManager(str(self.state_file))
        self.script_executor = ScriptExecutor(self.script_path, self.timeout)

    def start(self):
        """Start monitoring directory for JSON files."""
        global shutdown_event

        logger.info(f"Starting JSON detector")
        logger.info(f"Watch directory: {self.watch_directory}")
        logger.info(f"Script: {self.script_path}")
        logger.info(f"Multiple trigger: {self.multiple_trigger}")
        logger.info(f"State file: {self.state_file}")
        logger.info(f"Script timeout: {self.timeout}s")

        # Set up watchdog observer
        event_handler = JSONFileEventHandler(self.on_json_detected)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.watch_directory), recursive=False)

        logger.info("Starting file system observer")
        self.observer.start()

        # Monitor until shutdown signal
        try:
            while not shutdown_event:
                signal.pause()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        """Graceful shutdown with cleanup."""
        global shutdown_event
        shutdown_event = True

        logger.info("Stopping file system observer")
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)

        logger.info(f"Processed files: {len(self.state_manager.processed_files)}")
        logger.info("jsonDetector shutdown complete")


def parse_arguments():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Monitor directory for JSON files and execute a script for each detection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --watch-directory /tmp/test --script-to-launch /tmp/handler.sh
  %(prog)s --watch-directory ~/data --script-to-launch ./process.py --multiple-trigger YES
  %(prog)s --watch-directory . --script-to-launch ./handler.sh --timeout 60 --log-dir ./logs
        """
    )

    parser.add_argument(
        '--watch-directory',
        required=True,
        help='Directory to monitor for JSON files'
    )

    parser.add_argument(
        '--script-to-launch',
        required=True,
        help='Script to execute (receives JSON file path as first argument)'
    )

    parser.add_argument(
        '--multiple-trigger',
        choices=['YES', 'NO'],
        default='NO',
        help='Allow multiple triggers for same filename (default: NO)'
    )

    parser.add_argument(
        '--state-file',
        default='processed_files.json',
        help='JSON file for tracking processed files (default: processed_files.json)'
    )

    parser.add_argument(
        '--log-dir',
        default='logs',
        help='Directory for log files (default: logs)'
    )

    parser.add_argument(
        '--timeout',
        type=int,
        default=300,
        help='Script execution timeout in seconds (default: 300)'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()

    # Create detector instance
    detector = JSONDetector(
        watch_directory=args.watch_directory,
        script_to_launch=args.script_to_launch,
        multiple_trigger=(args.multiple_trigger == 'YES'),
        state_file=args.state_file,
        log_dir=args.log_dir,
        timeout=args.timeout
    )

    # Setup logging before validation
    detector.setup_logging()

    # Validate configuration
    detector.validate_configuration()

    # Setup signal handlers
    detector.setup_signal_handlers()

    # Initialize components
    detector.initialize_components()

    # Start monitoring
    detector.start()


if __name__ == '__main__':
    main()
