#!/usr/bin/env python3
"""
GTTviewer (GusTradeTable Viewer)
Live terminal viewer for GusTradeTable files with color coding and auto-refresh

Author: GUS3 Project
Version: 1.0
"""

import argparse
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box


class GTTViewer:
    """Main GTTviewer class for displaying GusTradeTable files"""

    def __init__(self, args):
        self.args = args
        self.console = Console()
        self.last_file_mtime = None
        self.error_count = 0
        self.max_errors = 10

        # Set up logging if requested
        if args.log:
            logging.basicConfig(
                filename=args.log,
                level=logging.DEBUG if args.verbose else logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
            self.logger = logging.getLogger(__name__)
        else:
            self.logger = None

    def log(self, level: str, message: str):
        """Log message if logging is enabled"""
        if self.logger:
            getattr(self.logger, level)(message)

    def load_gtt_file(self) -> Optional[pd.DataFrame]:
        """
        Load GTT file as tab-delimited DataFrame
        Returns None if file cannot be loaded
        """
        try:
            file_path = Path(self.args.input_GTT)

            if not file_path.exists():
                self.console.print(f"\n[bold red]❌ Error: GTT file not found[/bold red]")
                self.console.print(f"   Path: {file_path}\n")
                self.console.print("   Please check:")
                self.console.print("   - File path is correct")
                self.console.print("   - File exists")
                self.console.print("   - You have read permissions\n")
                return None

            # Check if file has been modified
            current_mtime = file_path.stat().st_mtime
            if self.last_file_mtime != current_mtime:
                self.last_file_mtime = current_mtime
                self.log('info', f'File modified, reloading: {file_path}')

            # Read tab-delimited file
            df = pd.read_csv(file_path, sep='\t')

            if df.empty:
                self.console.print("\n[yellow]⚠️  Warning: GTT file is empty[/yellow]")
                self.console.print(f"   Waiting for data... (refreshing every {self.args.refresh_interval} seconds)\n")
                return None

            # Validate required columns
            required_cols = ['Symbol']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                self.console.print(f"\n[bold red]❌ Error: Invalid GTT file format[/bold red]")
                self.console.print(f"\n   Missing required columns: {', '.join(missing_cols)}")
                self.console.print(f"   Found columns: {', '.join(df.columns.tolist())}\n")
                return None

            # Convert numeric columns (NEW column names)
            numeric_cols = ['CP-A($)', 'CP-B($)', 'Gap(%)', 'GapL(%)']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # Parse timestamps
            if 'LastUpdate' in df.columns:
                df['LastUpdate'] = pd.to_datetime(df['LastUpdate'], errors='coerce')

            # Convert other potential numeric columns (FDF enrichment + GSS columns)
            # Note: Float(M$) and NetIncome(M$) are already in millions in the file
            fdf_numeric_cols = ['Float(M$)', 'Instit(%)', 'Short_Interest',
                              'Short_Interest_Ratio', 'Market_Cap', 'Shares_Outstanding',
                              'EPS', 'PE_Ratio', 'Beta', 'NetIncome(M$)', 'Revenue_TTM',
                              'Profit_Margin', 'ROE', 'Debt_To_Equity', 'BorrowFee',
                              'FDF_Current_Price', 'FDF_Volume', 'FDF_Bid', 'FDF_Ask',
                              'ShortDiff', 'ShortableShares', 'BF1', 'BF2', 'BF_BEST']
            for col in fdf_numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            self.log('info', f'Loaded {len(df)} rows from {file_path}')
            self.error_count = 0  # Reset error count on successful load
            return df

        except Exception as e:
            self.console.print(f"\n[bold red]❌ Error loading GTT file: {e}[/bold red]\n")
            self.log('error', f'Failed to load GTT file: {e}')
            self.error_count += 1
            return None

    def get_gain_color(self, gain: float) -> str:
        """Return Rich color string based on gain percentage"""
        if pd.isna(gain):
            return "dim white"
        if gain < 0:
            return "bold red"
        elif gain < 5:
            return "white"
        elif gain < 15:
            return "green"
        elif gain < 30:
            return "bright_green"
        elif gain < 50:
            return "yellow"
        elif gain < 75:
            return "bright_yellow"
        elif gain < 100:
            return "orange1"
        else:
            return "bright_red"  # Extreme gain

    def get_borrow_color(self, status: str) -> str:
        """Return Rich color string for borrow status"""
        status = str(status).strip().upper()
        if status == "EASY":
            return "green"
        elif status == "AVAILABLE":
            return "yellow"
        elif status == "HARD":
            return "red"
        else:  # N/A or other
            return "dim white"

    def get_timestamp_color(self, timestamp: pd.Timestamp) -> str:
        """Return color based on timestamp age"""
        if pd.isna(timestamp):
            return "dim white"

        try:
            now = datetime.now()
            age_minutes = (now - timestamp).total_seconds() / 60

            if age_minutes < 1:
                return "green"
            elif age_minutes < 5:
                return "white"
            elif age_minutes < 15:
                return "yellow"
            elif age_minutes < 30:
                return "orange1"
            else:
                return "red"  # Stale data
        except:
            return "dim white"

    def format_value(self, value, decimals: int = 2) -> str:
        """Format numeric value with proper decimal places"""
        if pd.isna(value):
            return "N/A"
        if isinstance(value, (int, float)):
            return f"{value:.{decimals}f}"
        return str(value)

    def format_timestamp(self, timestamp: pd.Timestamp, short: bool = False) -> str:
        """Format timestamp for display"""
        if pd.isna(timestamp):
            return "N/A"
        try:
            if short:
                return timestamp.strftime("%m/%d %H:%M")
            return timestamp.strftime("%Y-%m-%d %H:%M:%S")
        except:
            return str(timestamp)

    def detect_enrichment_columns(self, df: pd.DataFrame) -> Dict[str, List[str]]:
        """
        Detect which enrichment columns are present
        Returns dict of column categories
        """
        enrichment = {
            'base': ['Symbol', 'CP-A(t)', 'CP-A($)',
                    'CP-B(t)', 'CP-B($)', 'Gap(%)',
                    'GapL(%)', 'ShortStatus', 'LastUpdate'],
            'fdf_critical': ['Float(M$)', 'Instit(%)', 'Short_Interest',
                            'Short_Interest_Ratio', 'ShortableShares', 'BorrowFee'],
            'fdf_financial': ['NetIncome(M$)', 'Revenue_TTM', 'Profit_Margin',
                             'ROE', 'Debt_To_Equity'],
            'fdf_valuation': ['Market_Cap', 'Shares_Outstanding', 'EPS',
                             'PE_Ratio', 'Beta'],
            'fdf_market': ['FDF_Current_Price', 'FDF_Volume', 'FDF_Bid',
                          'FDF_Ask', 'FDF_Timestamp']
        }

        present = {}
        for category, cols in enrichment.items():
            present[category] = [c for c in cols if c in df.columns]

        return present

    def get_visible_columns(self, df: pd.DataFrame) -> List[str]:
        """Get list of columns to display based on mode and user settings"""
        available = self.detect_enrichment_columns(df)

        # Handle --show-columns override
        if self.args.show_columns:
            specified = [c.strip() for c in self.args.show_columns.split(',')]
            return [c for c in specified if c in df.columns]

        # Display mode selection
        if self.args.display_mode == 'compact':
            cols = ['Symbol', 'CP-A($)', 'Gap(%)',
                   'GapL(%)', 'ShortStatus', 'LastUpdate']
        elif self.args.display_mode == 'detailed':
            # All columns
            cols = df.columns.tolist()
        else:  # normal
            # Show all columns (like detailed mode)
            cols = df.columns.tolist()

        # Filter out hidden columns
        if self.args.hide_columns:
            hidden = [c.strip() for c in self.args.hide_columns.split(',')]
            cols = [c for c in cols if c not in hidden]

        # Return only columns that exist in DataFrame
        return [c for c in cols if c in df.columns]

    def apply_filters(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply user-specified filters to DataFrame"""
        original_count = len(df)

        # Filter by minimum gain
        if self.args.filter_min_gain is not None:
            if 'GapL(%)' in df.columns:
                df = df[df['GapL(%)'] >= self.args.filter_min_gain]

        # Filter by maximum gain
        if self.args.filter_max_gain is not None:
            if 'GapL(%)' in df.columns:
                df = df[df['GapL(%)'] <= self.args.filter_max_gain]

        # Filter by borrow status
        if self.args.filter_borrow:
            if 'ShortStatus' in df.columns:
                df = df[df['ShortStatus'].astype(str).str.upper() == self.args.filter_borrow.upper()]

        filtered_count = original_count - len(df)
        if filtered_count > 0:
            self.log('debug', f'Filtered out {filtered_count} rows')

        return df

    def apply_sorting(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply user-specified sorting to DataFrame"""
        if self.args.sort_by:
            if self.args.sort_by in df.columns:
                ascending = (self.args.sort_order == 'asc')
                df = df.sort_values(by=self.args.sort_by, ascending=ascending)
                self.log('debug', f'Sorted by {self.args.sort_by} ({self.args.sort_order})')
            else:
                # Log warning about invalid column
                self.log('warning', f'Sort column "{self.args.sort_by}" not found in GTT file. Available columns: {", ".join(df.columns.tolist())}')
                self.console.print(f"\n[yellow]⚠️  Warning: Sort column '{self.args.sort_by}' not found. Data shown in file order.[/yellow]")

        return df

    def create_table(self, df: pd.DataFrame) -> Table:
        """
        Create Rich Table from DataFrame with color coding
        """
        # Apply filters
        original_df = df.copy()
        df = self.apply_filters(df)

        # Apply sorting
        df = self.apply_sorting(df)

        # Limit rows if specified
        if self.args.max_rows and self.args.max_rows > 0:
            df = df.head(self.args.max_rows)

        # Get visible columns
        visible_columns = self.get_visible_columns(df)

        # Create Rich table
        table = Table(
            title=f"[bold magenta]📊 GusTradeTable Viewer[/bold magenta]",
            show_header=True,
            header_style="bold cyan",
            border_style="blue",
            box=box.ROUNDED if not self.args.no_borders else box.SIMPLE,
            title_style="bold magenta",
            show_lines=False
        )

        # Add columns
        for col in visible_columns:
            # Determine column justification
            if col in ['Symbol', 'Borrow_Status']:
                justify = "left"
            elif 'TIME' in col or 'Update' in col:
                justify = "center"
            else:
                justify = "right"

            table.add_column(col, justify=justify)

        # Add rows with color coding
        highlight_symbols = []
        if self.args.highlight:
            highlight_symbols = [s.strip().upper() for s in self.args.highlight.split(',')]

        for _, row in df.iterrows():
            styled_row = []
            symbol = str(row.get('Symbol', '')).upper()
            is_highlighted = symbol in highlight_symbols

            for col in visible_columns:
                value = row[col]

                # Format and colorize based on column type
                if pd.isna(value):
                    formatted = "[dim white]N/A[/dim white]"
                elif col == 'Symbol':
                    if is_highlighted:
                        formatted = f"[bold cyan on black]{value}[/bold cyan on black]"
                    else:
                        formatted = f"[bold white]{value}[/bold white]"
                elif col in ['Gap(%)', 'GapL(%)']:
                    color = self.get_gain_color(value) if not self.args.no_colors else "white"
                    formatted = f"[{color}]{self.format_value(value, 2)}%[/{color}]"
                elif col == 'ShortStatus':
                    color = self.get_borrow_color(value) if not self.args.no_colors else "white"
                    formatted = f"[{color}]{value}[/{color}]"
                elif col == 'LastUpdate':
                    color = self.get_timestamp_color(value) if not self.args.no_colors else "white"
                    formatted = f"[{color}]{self.format_timestamp(value, short=(self.args.display_mode=='compact'))}[/{color}]"
                elif '(t)' in col:  # Timestamp columns like CP-A(t), CP-B(t)
                    formatted = self.format_timestamp(pd.to_datetime(value, errors='coerce'), short=True)
                elif '($)' in col or 'Current' in col:  # Price columns like CP-A($), CP-B($)
                    formatted = f"{self.format_value(value, 2)}"
                elif col in ['Float(M$)', 'NetIncome(M$)']:
                    # Already in millions - format with 2 decimals and M suffix
                    if isinstance(value, (int, float)) and not pd.isna(value):
                        formatted = f"{value:.2f}M"
                    else:
                        formatted = "N/A"
                elif col in ['Market_Cap', 'Shares_Outstanding', 'Revenue_TTM']:
                    # Large numbers - format with commas
                    if isinstance(value, (int, float)) and not pd.isna(value):
                        formatted = f"{int(value):,}"
                    else:
                        formatted = "N/A"
                elif col in ['Instit(%)', 'Profit_Margin', 'ROE']:
                    # Percentages
                    formatted = f"{self.format_value(value, 1)}%" if not pd.isna(value) else "N/A"
                elif isinstance(value, (int, float)):
                    formatted = self.format_value(value, 2)
                else:
                    formatted = str(value)

                styled_row.append(formatted)

            table.add_row(*styled_row)

        return table

    def create_status_bar(self, original_count: int, filtered_count: int) -> Text:
        """Create status bar with configuration info"""
        status = Text()

        status.append("📊 GTTviewer", style="bold magenta")
        status.append(" | ")
        status.append(f"File: {Path(self.args.input_GTT).name}", style="cyan")
        status.append(" | ")
        status.append(f"Rows: {original_count}", style="white")

        if filtered_count > 0:
            status.append(" | ", style="white")
            status.append(f"Filtered: {filtered_count}", style="yellow")

        if self.args.sort_by:
            status.append(" | ", style="white")
            arrow = "↑" if self.args.sort_order == "asc" else "↓"
            status.append(f"Sort: {self.args.sort_by} {arrow}", style="green")

        status.append(" | ", style="white")
        status.append(f"Refresh: {self.args.refresh_interval}s", style="blue")
        status.append(" | ", style="white")
        status.append("Press Ctrl+C to exit", style="dim")

        return status

    def run(self):
        """Main viewer loop with live refresh"""
        self.console.print("\n[bold green]🚀 Starting GTTviewer...[/bold green]\n")

        if self.args.verbose:
            self.console.print(f"[dim]Input file: {self.args.input_GTT}[/dim]")
            self.console.print(f"[dim]Refresh interval: {self.args.refresh_interval}s[/dim]")
            self.console.print(f"[dim]Display mode: {self.args.display_mode}[/dim]")
            if self.args.sort_by:
                self.console.print(f"[dim]Sort by: {self.args.sort_by} ({self.args.sort_order})[/dim]")
            self.console.print()

        try:
            with Live(console=self.console, refresh_per_second=1, screen=False) as live:
                while True:
                    try:
                        # Load GTT file
                        df = self.load_gtt_file()

                        if df is None or df.empty:
                            # Show waiting message
                            waiting_panel = Panel(
                                f"[yellow]Waiting for data...[/yellow]\n"
                                f"File: {self.args.input_GTT}\n"
                                f"Checking every {self.args.refresh_interval} seconds",
                                title="[bold]GTTviewer[/bold]",
                                border_style="yellow"
                            )
                            live.update(waiting_panel)
                            time.sleep(self.args.refresh_interval)
                            continue

                        original_count = len(df)

                        # Apply filters to calculate filtered count
                        filtered_df = self.apply_filters(df.copy())
                        filtered_count = original_count - len(filtered_df)

                        # Create table
                        table = self.create_table(df)

                        # Create status bar
                        status_bar = self.create_status_bar(original_count, filtered_count)

                        # Combine elements
                        from rich.layout import Layout
                        layout = Layout()
                        layout.split_column(
                            Layout(table, name="table"),
                            Layout(status_bar, name="status", size=1)
                        )

                        # Update display
                        live.update(layout)

                        # Wait for refresh interval
                        time.sleep(self.args.refresh_interval)

                    except KeyboardInterrupt:
                        raise  # Pass to outer handler
                    except Exception as e:
                        self.console.print(f"\n[red]❌ Error: {e}[/red]")
                        self.log('error', f'Error in main loop: {e}')
                        self.error_count += 1

                        if self.error_count >= self.max_errors:
                            self.console.print(f"\n[bold red]Too many errors ({self.max_errors}), exiting...[/bold red]\n")
                            break

                        time.sleep(self.args.refresh_interval)

        except KeyboardInterrupt:
            self.console.print("\n\n[yellow]✋ Viewer stopped by user[/yellow]\n")
            self.log('info', 'Viewer stopped by user')
        except Exception as e:
            self.console.print(f"\n[bold red]❌ Fatal error: {e}[/bold red]\n")
            self.log('error', f'Fatal error: {e}')
            sys.exit(1)


def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='GTTviewer - Live terminal viewer for GusTradeTable files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt

  # Fast refresh with filtering
  python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \\
    --refresh-interval 2 --filter-min-gain 10 --sort-by GapL(%)

  # Compact mode, top 20 gainers
  python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \\
    --display-mode compact --max-rows 20 --sort-by GapL(%)

  # Monitor specific columns
  python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \\
    --show-columns Symbol,GapL(%),Gap(%),ShortStatus
        """
    )

    # Required arguments
    parser.add_argument('--input-GTT', type=str, required=True,
                       help='Path to GusTradeTable file (tab-delimited)')

    # Display options
    parser.add_argument('--refresh-interval', type=int, default=5,
                       help='Refresh rate in seconds (default: 5)')
    parser.add_argument('--display-mode', type=str, default='normal',
                       choices=['compact', 'normal', 'detailed'],
                       help='Display mode: compact (minimal columns) | normal (all columns) | detailed (all columns, same as normal) (default: normal)')

    # Sorting options
    parser.add_argument('--sort-by', type=str, default='GapL(%)',
                       help='Sort by column name (default: GapL(%%))')
    parser.add_argument('--sort-order', type=str, default='desc',
                       choices=['asc', 'desc'],
                       help='Sort order: asc|desc (default: desc)')

    # Filtering options
    parser.add_argument('--filter-min-gain', type=float, default=None,
                       help='Minimum gain percentage to show')
    parser.add_argument('--filter-max-gain', type=float, default=None,
                       help='Maximum gain percentage to show')
    parser.add_argument('--filter-borrow', type=str, default=None,
                       choices=['Easy', 'Available', 'Hard'],
                       help='Filter by borrow status')

    # Column control
    parser.add_argument('--show-columns', type=str, default=None,
                       help='Comma-separated list of columns to show')
    parser.add_argument('--hide-columns', type=str, default=None,
                       help='Comma-separated list of columns to hide')

    # Display options
    parser.add_argument('--no-colors', action='store_true',
                       help='Disable color coding')
    parser.add_argument('--no-borders', action='store_true',
                       help='Minimal table borders')
    parser.add_argument('--max-rows', type=int, default=None,
                       help='Limit display to N rows')
    parser.add_argument('--highlight', type=str, default=None,
                       help='Comma-separated list of symbols to highlight')

    # Logging
    parser.add_argument('--log', type=str, default=None,
                       help='Log file path')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_arguments()

    viewer = GTTViewer(args)
    viewer.run()


if __name__ == '__main__':
    main()
